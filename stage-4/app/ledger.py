"""
Pocketful Stage 2 Ledger & State Engine.
Implements double-entry ledger invariants, payment authorizations (two-phase holds & captures),
SQLite WAL mode, and atomic state transitions.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.models import (
    PocketfulError,
    compute_body_hash,
    derive_handle_from_email,
    equal_split,
    hash_password,
    utc_now_iso,
    validate_amount,
    validate_handle,
    verify_password,
    parse_rfc3339,
    validate_correction_body,
    validate_correction_batch_payload,
)


class LedgerEngine:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.getenv("POCKETFUL_DB_PATH", "pocketful_stage4.db")
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        handle TEXT UNIQUE NOT NULL,
                        balance INTEGER NOT NULL CHECK (balance >= 0),
                        opening_balance INTEGER NOT NULL DEFAULT 0
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        token TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS payments (
                        payment_id TEXT PRIMARY KEY,
                        from_user_id TEXT NOT NULL REFERENCES users(id),
                        from_handle TEXT NOT NULL,
                        to_user_id TEXT NOT NULL REFERENCES users(id),
                        to_handle TEXT NOT NULL,
                        amount INTEGER NOT NULL CHECK (amount > 0),
                        currency TEXT NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
                        request_id TEXT,
                        settlement_id TEXT,
                        authorization_id TEXT,
                        refund_of TEXT,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS payment_revisions (
                        payment_id TEXT NOT NULL REFERENCES payments(payment_id),
                        revision INTEGER NOT NULL,
                        amount INTEGER NOT NULL CHECK (amount >= 0),
                        effective_at TEXT NOT NULL,
                        recorded_at TEXT NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        correction_batch_id TEXT,
                        PRIMARY KEY (payment_id, revision)
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rev_pmt ON payment_revisions(payment_id, revision);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rev_eff ON payment_revisions(effective_at);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rev_rec ON payment_revisions(recorded_at);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_pmt_refund ON payments(refund_of);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_rev_batch ON payment_revisions(correction_batch_id);
                """)
                try:
                    conn.execute("ALTER TABLE payments ADD COLUMN refund_of TEXT;")
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE payment_revisions ADD COLUMN correction_batch_id TEXT;")
                except sqlite3.OperationalError:
                    pass
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS requests (
                        request_id TEXT PRIMARY KEY,
                        requester_id TEXT NOT NULL REFERENCES users(id),
                        requester_handle TEXT NOT NULL,
                        payer_id TEXT NOT NULL REFERENCES users(id),
                        payer_handle TEXT NOT NULL,
                        amount INTEGER NOT NULL CHECK (amount >= 0),
                        currency TEXT NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'declined', 'cancelled')),
                        payment_id TEXT REFERENCES payments(payment_id),
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS splits (
                        split_id TEXT PRIMARY KEY,
                        amount INTEGER NOT NULL,
                        currency TEXT NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        shares_json TEXT NOT NULL,
                        requests_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS authorizations (
                        authorization_id TEXT PRIMARY KEY,
                        from_user_id TEXT NOT NULL REFERENCES users(id),
                        from_handle TEXT NOT NULL,
                        to_user_id TEXT NOT NULL REFERENCES users(id),
                        to_handle TEXT NOT NULL,
                        amount INTEGER NOT NULL CHECK (amount > 0),
                        captured_amount INTEGER NOT NULL DEFAULT 0,
                        currency TEXT NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
                        status TEXT NOT NULL CHECK (status IN ('open', 'captured', 'voided', 'expired')),
                        expires_at TEXT NOT NULL,
                        closed_at TEXT,
                        payment_id TEXT,
                        payment_ids TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_pmt_from ON payments(from_user_id);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_pmt_to ON payments(to_user_id);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_auth_from ON authorizations(from_user_id);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_auth_to ON authorizations(to_user_id);
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS snapshots (
                        token TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        data_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS idempotency (
                        user_id TEXT NOT NULL,
                        key TEXT NOT NULL,
                        method TEXT NOT NULL,
                        path TEXT NOT NULL,
                        body_hash TEXT NOT NULL,
                        status_code INTEGER NOT NULL,
                        response_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (user_id, key, method, path)
                    );
                """)

                # Initialize default meta if absent
                existing = conn.execute("SELECT key FROM meta WHERE key = 'currency';").fetchone()
                if not existing:
                    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('currency', 'EUR');")
                    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('minor_units', '2');")
                    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('settlement_operators', '[]');")
                    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('authorization_ttl_seconds', '600');")
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def get_meta(self) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute("SELECT key, value FROM meta;").fetchall()
                data = {row["key"]: row["value"] for row in rows}
                return {
                    "currency": data.get("currency", "EUR"),
                    "minor_units": int(data.get("minor_units", 2)),
                    "settlement_operator_ids": json.loads(data.get("settlement_operators", "[]")),
                    "authorization_ttl_seconds": int(data.get("authorization_ttl_seconds", 600)),
                }
            finally:
                conn.close()

    def _expire_holds(self, conn: sqlite3.Connection, now_iso: str) -> None:
        conn.execute("""
            UPDATE authorizations
            SET status = 'expired', closed_at = expires_at
            WHERE status = 'open' AND expires_at <= ?;
        """, (now_iso,))

    def get_user_balances(self, conn: sqlite3.Connection, user_id: str, now_iso: str) -> Tuple[int, int, int]:
        """Calculates total, available, and held balances for a user."""
        self._expire_holds(conn, now_iso)
        urow = conn.execute("SELECT balance FROM users WHERE id = ?;", (user_id,)).fetchone()
        if not urow:
            return 0, 0, 0
        total = urow["balance"]
        hrow = conn.execute("""
            SELECT COALESCE(SUM(amount - captured_amount), 0) AS total_held
            FROM authorizations
            WHERE from_user_id = ? AND status = 'open';
        """, (user_id,)).fetchone()
        held = hrow["total_held"]
        available = max(0, total - held)
        return total, available, held

    def reset_fixture(self, fixture: Dict[str, Any]) -> None:
        """Atomically replaces all database state per §3.3 & Stage 2 spec."""
        if not isinstance(fixture, dict):
            raise PocketfulError(422, "validation_failed", "Fixture must be a JSON object")

        currency = fixture.get("currency", "EUR")
        minor_units = fixture.get("minor_units", 2)
        ttl = fixture.get("authorization_ttl_seconds", 600)
        now = utc_now_iso()

        if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
            raise PocketfulError(422, "validation_failed", "Currency must be 3-letter ISO code")
        if not isinstance(minor_units, int) or minor_units < 0 or minor_units > 4:
            raise PocketfulError(422, "validation_failed", "minor_units must be integer between 0 and 4")
        if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 1:
            raise PocketfulError(422, "validation_failed", "authorization_ttl_seconds must be a positive integer")

        settlement_ops = fixture.get("settlement_operator_ids", [])
        auths = fixture.get("authorizations", [])

        # Validate seeded payments created_at not in future
        now_dt = datetime.now(timezone.utc)
        payments_list = fixture.get("payments", [])
        for p in payments_list:
            if "created_at" in p and p["created_at"]:
                p_dt = parse_rfc3339(p["created_at"], "created_at")
                if p_dt > now_dt:
                    raise PocketfulError(422, "validation_failed", "Seeded payment created_at cannot be in the future")

        # Validate seeded negative balances and open holds overflow
        user_opening_balances = {}
        for u in fixture.get("users", []):
            bal = u.get("balance", 0)
            if not isinstance(bal, int) or bal < 0:
                raise PocketfulError(422, "validation_failed", f"User {u.get('id')} has negative balance")
            # Calculate opening balance: ending balance minus net seeded payments
            net_seeded = 0
            for p in payments_list:
                amt = int(p.get("amount", 0))
                if p.get("to_user_id") == u["id"]:
                    net_seeded += amt
                if p.get("from_user_id") == u["id"]:
                    net_seeded -= amt
            user_opening_balances[u["id"]] = bal - net_seeded

            # Check sum of unexpired open holds
            user_open_holds = sum(
                int(a["amount"]) - int(a.get("captured_amount", 0))
                for a in auths
                if a.get("from_user_id") == u["id"] and a.get("status") == "open" and a.get("expires_at", "") > now
            )
            if user_open_holds > bal:
                raise PocketfulError(422, "validation_failed", f"Seeded holds for user {u['id']} exceed balance")

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute("DELETE FROM snapshots;")
                conn.execute("DELETE FROM payment_revisions;")
                conn.execute("DELETE FROM idempotency;")
                conn.execute("DELETE FROM splits;")
                conn.execute("DELETE FROM requests;")
                conn.execute("DELETE FROM payments;")
                conn.execute("DELETE FROM authorizations;")
                conn.execute("DELETE FROM sessions;")
                conn.execute("DELETE FROM users;")
                conn.execute("DELETE FROM meta;")

                conn.execute("INSERT INTO meta (key, value) VALUES ('currency', ?);", (currency,))
                conn.execute("INSERT INTO meta (key, value) VALUES ('minor_units', ?);", (str(minor_units),))
                conn.execute("INSERT INTO meta (key, value) VALUES ('settlement_operators', ?);", (json.dumps(settlement_ops),))
                conn.execute("INSERT INTO meta (key, value) VALUES ('authorization_ttl_seconds', ?);", (str(ttl),))

                for u in fixture.get("users", []):
                    uid = u["id"]
                    email = u["email"]
                    pwd = u.get("password", "correct horse")
                    dname = u.get("display_name", u.get("handle", "User"))
                    handle = u["handle"].lower()
                    balance = int(u.get("balance", 0))
                    opening_bal = user_opening_balances.get(uid, balance)
                    pwd_hash = hash_password(pwd)
                    conn.execute("""
                        INSERT INTO users (id, email, password_hash, display_name, handle, balance, opening_balance)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                    """, (uid, email, pwd_hash, dname, handle, balance, opening_bal))

                for p in payments_list:
                    pid = p["id"] if "id" in p else p["payment_id"]
                    fid = p["from_user_id"]
                    tid = p["to_user_id"]
                    amt = int(p["amount"])
                    note = str(p.get("note", ""))
                    vis = str(p.get("visibility", "public"))
                    rid = p.get("request_id")
                    sid = p.get("settlement_id")
                    aid = p.get("authorization_id")
                    ref_of = p.get("refund_of")
                    cat = p.get("created_at", now)

                    f_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (fid,)).fetchone()["handle"]
                    t_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (tid,)).fetchone()["handle"]

                    conn.execute("""
                        INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                              amount, currency, note, visibility, request_id, settlement_id, authorization_id, refund_of, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (pid, fid, f_handle, tid, t_handle, amt, currency, note, vis, rid, sid, aid, ref_of, cat))

                    conn.execute("""
                        INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                        VALUES (?, 1, ?, ?, ?, '', NULL);
                    """, (pid, amt, cat, cat))

                for r in fixture.get("requests", []):
                    rqid = r["id"]
                    req_id = r["requester_id"]
                    payer_id = r["payer_id"]
                    amt = int(r["amount"])
                    note = str(r.get("note", ""))
                    status = str(r.get("status", "pending"))
                    pmt_id = r.get("payment_id")
                    cat = r.get("created_at", now)

                    req_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (req_id,)).fetchone()["handle"]
                    payer_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (payer_id,)).fetchone()["handle"]

                    conn.execute("""
                        INSERT INTO requests (request_id, requester_id, requester_handle, payer_id, payer_handle,
                                             amount, currency, note, status, payment_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (rqid, req_id, req_handle, payer_id, payer_handle, amt, currency, note, status, pmt_id, cat))

                for a in auths:
                    aid = a["id"]
                    fid = a["from_user_id"]
                    tid = a["to_user_id"]
                    amt = int(a["amount"])
                    camt = int(a.get("captured_amount", 0))
                    note = str(a.get("note", ""))
                    vis = str(a.get("visibility", "public"))
                    st = str(a.get("status", "open"))
                    eat = str(a["expires_at"])
                    pmt_id = a.get("payment_id")
                    pids = a.get("payment_ids", [pmt_id] if pmt_id else [])
                    cat = a.get("created_at", now)
                    closed_at = a.get("closed_at")
                    if closed_at is None and st != "open":
                        if st == "expired":
                            closed_at = eat
                        else:
                            closed_at = cat

                    f_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (fid,)).fetchone()["handle"]
                    t_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (tid,)).fetchone()["handle"]

                    conn.execute("""
                        INSERT INTO authorizations (authorization_id, from_user_id, from_handle, to_user_id, to_handle,
                                                    amount, captured_amount, currency, note, visibility, status, expires_at, closed_at,
                                                    payment_id, payment_ids, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (aid, fid, f_handle, tid, t_handle, amt, camt, currency, note, vis, st, eat, closed_at, pmt_id, json.dumps(pids), cat))

                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def export_state(self) -> Dict[str, Any]:
        """Produces a complete, read-only snapshot per §10 & Stage 2."""
        with self._lock:
            conn = self._get_connection()
            try:
                meta = self.get_meta()
                users = [dict(row) for row in conn.execute("SELECT * FROM users;").fetchall()]
                sessions = [dict(row) for row in conn.execute("SELECT * FROM sessions;").fetchall()]
                payments = [dict(row) for row in conn.execute("SELECT * FROM payments;").fetchall()]
                revisions = [dict(row) for row in conn.execute("SELECT * FROM payment_revisions ORDER BY payment_id ASC, revision ASC;").fetchall()]
                requests = [dict(row) for row in conn.execute("SELECT * FROM requests;").fetchall()]
                splits = [dict(row) for row in conn.execute("SELECT * FROM splits;").fetchall()]
                auths = [dict(row) for row in conn.execute("SELECT * FROM authorizations;").fetchall()]
                idempotency = [dict(row) for row in conn.execute("SELECT * FROM idempotency;").fetchall()]

                return {
                    "track": "pocketful",
                    "format_version": 1,
                    "state": {
                        "meta": meta,
                        "users": users,
                        "sessions": sessions,
                        "payments": payments,
                        "payment_revisions": revisions,
                        "requests": requests,
                        "splits": splits,
                        "authorizations": auths,
                        "idempotency": idempotency
                    }
                }
            finally:
                conn.close()

    def import_state(self, payload: Dict[str, Any]) -> None:
        """Atomically replaces all service state from an exported snapshot per §10."""
        if not isinstance(payload, dict):
            raise PocketfulError(422, "validation_failed", "Malformed import payload")
        if payload.get("track") != "pocketful" or payload.get("format_version") != 1:
            raise PocketfulError(422, "validation_failed", "Incompatible track or format_version")

        state = payload.get("state")
        if not isinstance(state, dict):
            raise PocketfulError(422, "validation_failed", "Missing state object in import payload")

        meta = state.get("meta", {})
        currency = meta.get("currency", "EUR")
        minor_units = meta.get("minor_units", 2)
        settlement_ops = meta.get("settlement_operator_ids", [])
        ttl = meta.get("authorization_ttl_seconds", 600)

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute("DELETE FROM snapshots;")
                conn.execute("DELETE FROM payment_revisions;")
                conn.execute("DELETE FROM idempotency;")
                conn.execute("DELETE FROM splits;")
                conn.execute("DELETE FROM requests;")
                conn.execute("DELETE FROM payments;")
                conn.execute("DELETE FROM authorizations;")
                conn.execute("DELETE FROM sessions;")
                conn.execute("DELETE FROM users;")
                conn.execute("DELETE FROM meta;")

                conn.execute("INSERT INTO meta (key, value) VALUES ('currency', ?);", (currency,))
                conn.execute("INSERT INTO meta (key, value) VALUES ('minor_units', ?);", (str(minor_units),))
                conn.execute("INSERT INTO meta (key, value) VALUES ('settlement_operators', ?);", (json.dumps(settlement_ops),))
                conn.execute("INSERT INTO meta (key, value) VALUES ('authorization_ttl_seconds', ?);", (str(ttl),))

                for u in state.get("users", []):
                    bal = u["balance"]
                    opening_bal = u.get("opening_balance")
                    if opening_bal is None:
                        net = sum(p["amount"] for p in state.get("payments", []) if p["to_user_id"] == u["id"]) - \
                              sum(p["amount"] for p in state.get("payments", []) if p["from_user_id"] == u["id"])
                        opening_bal = bal - net
                    conn.execute("""
                        INSERT INTO users (id, email, password_hash, display_name, handle, balance, opening_balance)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                    """, (u["id"], u["email"], u["password_hash"], u["display_name"], u["handle"], bal, opening_bal))

                for s in state.get("sessions", []):
                    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?);", (s["token"], s["user_id"]))

                for p in state.get("payments", []):
                    conn.execute("""
                        INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                              amount, currency, note, visibility, request_id, settlement_id, authorization_id, refund_of, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (p["payment_id"], p["from_user_id"], p["from_handle"], p["to_user_id"], p["to_handle"],
                          p["amount"], p["currency"], p.get("note", ""), p.get("visibility", "public"),
                          p.get("request_id"), p.get("settlement_id"), p.get("authorization_id"), p.get("refund_of"), p["created_at"]))

                if "payment_revisions" in state and state["payment_revisions"]:
                    for rev in state["payment_revisions"]:
                        conn.execute("""
                            INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?);
                        """, (rev["payment_id"], rev["revision"], rev["amount"], rev["effective_at"], rev["recorded_at"], rev.get("reason", ""), rev.get("correction_batch_id")))
                else:
                    for p in state.get("payments", []):
                        conn.execute("""
                            INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                            VALUES (?, 1, ?, ?, ?, '', NULL);
                        """, (p["payment_id"], p["amount"], p["created_at"], p["created_at"]))

                for r in state.get("requests", []):
                    conn.execute("""
                        INSERT INTO requests (request_id, requester_id, requester_handle, payer_id, payer_handle,
                                             amount, currency, note, status, payment_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (r["request_id"], r["requester_id"], r["requester_handle"], r["payer_id"], r["payer_handle"],
                          r["amount"], r["currency"], r.get("note", ""), r["status"], r.get("payment_id"), r["created_at"]))

                for sp in state.get("splits", []):
                    conn.execute("""
                        INSERT INTO splits (split_id, amount, currency, note, shares_json, requests_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                    """, (sp["split_id"], sp["amount"], sp["currency"], sp.get("note", ""),
                          sp["shares_json"], sp["requests_json"], sp["created_at"]))

                for a in state.get("authorizations", []):
                    st = a["status"]
                    closed_at = a.get("closed_at")
                    if closed_at is None and st != "open":
                        if st == "expired":
                            closed_at = a["expires_at"]
                        else:
                            closed_at = a.get("created_at")

                    conn.execute("""
                        INSERT INTO authorizations (authorization_id, from_user_id, from_handle, to_user_id, to_handle,
                                                    amount, captured_amount, currency, note, visibility, status, expires_at, closed_at,
                                                    payment_id, payment_ids, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (a["authorization_id"], a["from_user_id"], a["from_handle"], a["to_user_id"], a["to_handle"],
                          a["amount"], a.get("captured_amount", 0), a["currency"], a.get("note", ""),
                          a.get("visibility", "public"), st, a["expires_at"], closed_at,
                          a.get("payment_id"), a.get("payment_ids", "[]"), a["created_at"]))

                for i in state.get("idempotency", []):
                    conn.execute("""
                        INSERT INTO idempotency (user_id, key, method, path, body_hash, status_code, response_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """, (i["user_id"], i["key"], i["method"], i["path"], i["body_hash"], i["status_code"], i["response_json"], i["created_at"]))

                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def signup(self, email: str, password: str, display_name: str) -> Tuple[Dict[str, Any], str]:
        """Creates a new user account per §6."""
        if not email or "@" not in email or not isinstance(email, str):
            raise PocketfulError(422, "validation_failed", "Invalid email format")
        if not password or not isinstance(password, str) or len(password) < 8:
            raise PocketfulError(422, "validation_failed", "Password must be at least 8 characters")
        if not display_name or not isinstance(display_name, str) or len(display_name) > 50:
            raise PocketfulError(422, "validation_failed", "Display name must be between 1 and 50 characters")

        handle = derive_handle_from_email(email)
        user_id = f"u_{uuid.uuid4().hex[:12]}"
        token = uuid.uuid4().hex
        pwd_hash = hash_password(password)
        meta = self.get_meta()

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                existing_email = conn.execute("SELECT id FROM users WHERE email = ?;", (email,)).fetchone()
                if existing_email:
                    raise PocketfulError(409, "email_taken", "An account with this email already exists")

                existing_handle = conn.execute("SELECT id FROM users WHERE handle = ?;", (handle,)).fetchone()
                if existing_handle:
                    raise PocketfulError(409, "handle_taken", f"Derived handle '{handle}' is already in use")

                conn.execute("""
                    INSERT INTO users (id, email, password_hash, display_name, handle, balance)
                    VALUES (?, ?, ?, ?, ?, 0);
                """, (user_id, email, pwd_hash, display_name, handle))

                conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?);", (token, user_id))
                conn.execute("COMMIT;")
                return {
                    "token": token,
                    "user": {
                        "user_id": user_id,
                        "display_name": display_name,
                        "handle": handle,
                        "balance": 0,
                        "total": 0,
                        "available": 0,
                        "held": 0,
                        "currency": meta["currency"],
                        "minor_units": meta["minor_units"]
                    }
                }, token
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def login(self, email: str, password: str) -> Tuple[Dict[str, Any], str]:
        """Authenticates user and issues bearer token per §6."""
        with self._lock:
            conn = self._get_connection()
            try:
                user = conn.execute("SELECT * FROM users WHERE email = ?;", (email,)).fetchone()
                if not user or not verify_password(password, user["password_hash"]):
                    raise PocketfulError(401, "unauthenticated", "Invalid email or password")

                token = uuid.uuid4().hex
                conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?);", (token, user["id"]))
                meta = self.get_meta()
                now = utc_now_iso()
                total, available, held = self.get_user_balances(conn, user["id"], now)

                return {
                    "token": token,
                    "user": {
                        "user_id": user["id"],
                        "display_name": user["display_name"],
                        "handle": user["handle"],
                        "balance": total,
                        "total": total,
                        "available": available,
                        "held": held,
                        "currency": meta["currency"],
                        "minor_units": meta["minor_units"]
                    }
                }, token
            finally:
                conn.close()

    def get_user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Resolves authenticated user from bearer token."""
        if not token:
            return None
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("""
                    SELECT u.* FROM users u
                    JOIN sessions s ON u.id = s.user_id
                    WHERE s.token = ?;
                """, (token,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def check_idempotency(self, user_id: str, key: str, method: str, path: str, body_hash: str) -> Optional[Tuple[int, Dict[str, Any]]]:
        """Resolves existing idempotency state per §7."""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("""
                    SELECT body_hash, status_code, response_json FROM idempotency
                    WHERE user_id = ? AND key = ? AND method = ? AND path = ?;
                """, (user_id, key, method, path)).fetchone()
                if not row:
                    return None
                if row["body_hash"] != body_hash:
                    raise PocketfulError(409, "idempotency_key_reuse", "Idempotency key already used with a different request payload")
                return 200, json.loads(row["response_json"])
            finally:
                conn.close()

    def record_idempotency(self, user_id: str, key: str, method: str, path: str, body_hash: str, status_code: int, response_data: Dict[str, Any]) -> None:
        """Stores successful idempotency result."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO idempotency (user_id, key, method, path, body_hash, status_code, response_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (user_id, key, method, path, body_hash, status_code, json.dumps(response_data), utc_now_iso()))
            finally:
                conn.close()

    def create_payment(self, caller: Dict[str, Any], to_handle: str, amount: Any, note: str = "", visibility: str = "public") -> Dict[str, Any]:
        """Executes atomic P2P transfer, validating against available balance."""
        amount = validate_amount(amount)
        to_handle = validate_handle(to_handle, "to_handle")
        if to_handle == caller["handle"]:
            raise PocketfulError(422, "self_payment", "Cannot send money to yourself")
        if not isinstance(note, str) or len(note) > 200:
            raise PocketfulError(422, "validation_failed", "Note must be a string up to 200 characters")
        if visibility not in ("public", "private"):
            raise PocketfulError(422, "validation_failed", "Visibility must be 'public' or 'private'")

        meta = self.get_meta()
        currency = meta["currency"]
        pid = f"p_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                recip = conn.execute("SELECT * FROM users WHERE handle = ?;", (to_handle,)).fetchone()
                if not recip:
                    raise PocketfulError(404, "not_found", f"User with handle '{to_handle}' not found")

                total, available, held = self.get_user_balances(conn, caller["id"], now)
                if available < amount:
                    raise PocketfulError(409, "insufficient_funds", "Insufficient available funds")

                # Double-entry balance transfer with sum(delta) = 0
                conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?;", (amount, caller["id"]))
                conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (amount, recip["id"]))

                conn.execute("""
                    INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                          amount, currency, note, visibility, request_id, settlement_id, authorization_id, refund_of, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?);
                """, (pid, caller["id"], caller["handle"], recip["id"], recip["handle"],
                      amount, currency, note, visibility, None, None, now))

                conn.execute("""
                    INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                    VALUES (?, 1, ?, ?, ?, '', NULL);
                """, (pid, amount, now, now))

                conn.execute("COMMIT;")
                return {
                    "payment_id": pid,
                    "from_user_id": caller["id"],
                    "from_handle": caller["handle"],
                    "to_user_id": recip["id"],
                    "to_handle": recip["handle"],
                    "amount": amount,
                    "currency": currency,
                    "note": note,
                    "visibility": visibility,
                    "request_id": None,
                    "settlement_id": None,
                    "authorization_id": None,
                    "refund_of": None,
                    "created_at": now
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def create_request(self, caller: Dict[str, Any], payer_handle: str, amount: Any, note: str = "") -> Dict[str, Any]:
        """Creates a money request per §8."""
        amount = validate_amount(amount)
        payer_handle = validate_handle(payer_handle, "payer_handle")
        if payer_handle == caller["handle"]:
            raise PocketfulError(422, "self_request", "Cannot request money from yourself")
        if not isinstance(note, str) or len(note) > 200:
            raise PocketfulError(422, "validation_failed", "Note must be a string up to 200 characters")

        meta = self.get_meta()
        currency = meta["currency"]
        rqid = f"rq_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        with self._lock:
            conn = self._get_connection()
            try:
                payer = conn.execute("SELECT * FROM users WHERE handle = ?;", (payer_handle,)).fetchone()
                if not payer:
                    raise PocketfulError(404, "not_found", f"User '{payer_handle}' not found")

                conn.execute("""
                    INSERT INTO requests (request_id, requester_id, requester_handle, payer_id, payer_handle,
                                         amount, currency, note, status, payment_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?);
                """, (rqid, caller["id"], caller["handle"], payer["id"], payer["handle"],
                      amount, currency, note, now))

                return {
                    "request_id": rqid,
                    "requester_id": caller["id"],
                    "requester_handle": caller["handle"],
                    "payer_id": payer["id"],
                    "payer_handle": payer["handle"],
                    "amount": amount,
                    "currency": currency,
                    "note": note,
                    "status": "pending",
                    "payment_id": None,
                    "created_at": now
                }
            finally:
                conn.close()

    def pay_request(self, caller: Dict[str, Any], request_id: str, visibility: str = "public") -> Dict[str, Any]:
        """Fulfills a pending request against available funds."""
        if visibility not in ("public", "private"):
            raise PocketfulError(422, "validation_failed", "Visibility must be 'public' or 'private'")

        meta = self.get_meta()
        currency = meta["currency"]
        pid = f"p_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                rq = conn.execute("SELECT * FROM requests WHERE request_id = ?;", (request_id,)).fetchone()
                if not rq:
                    raise PocketfulError(404, "not_found", "Request not found")
                if rq["payer_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only the designated payer can pay a request")
                if rq["status"] != "pending":
                    raise PocketfulError(409, "request_not_pending", f"Request status is '{rq['status']}'")

                amount = rq["amount"]
                total, available, held = self.get_user_balances(conn, caller["id"], now)
                if available < amount:
                    raise PocketfulError(409, "insufficient_funds", "Insufficient available funds to pay request")

                conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?;", (amount, caller["id"]))
                conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (amount, rq["requester_id"]))

                conn.execute("""
                    INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                          amount, currency, note, visibility, request_id, settlement_id, authorization_id, refund_of, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?);
                """, (pid, caller["id"], caller["handle"], rq["requester_id"], rq["requester_handle"],
                      amount, currency, rq["note"], visibility, request_id, None, now))

                conn.execute("""
                    INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                    VALUES (?, 1, ?, ?, ?, '', NULL);
                """, (pid, amount, now, now))

                conn.execute("UPDATE requests SET status = 'paid', payment_id = ? WHERE request_id = ?;", (pid, request_id))
                conn.execute("COMMIT;")
                return {
                    "payment_id": pid,
                    "from_user_id": caller["id"],
                    "from_handle": caller["handle"],
                    "to_user_id": rq["requester_id"],
                    "to_handle": rq["requester_handle"],
                    "amount": amount,
                    "currency": currency,
                    "note": rq["note"],
                    "visibility": visibility,
                    "request_id": request_id,
                    "settlement_id": None,
                    "authorization_id": None,
                    "refund_of": None,
                    "created_at": now
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def decline_request(self, caller: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_connection()
            try:
                rq = conn.execute("SELECT * FROM requests WHERE request_id = ?;", (request_id,)).fetchone()
                if not rq:
                    raise PocketfulError(404, "not_found", "Request not found")
                if rq["payer_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only the payer may decline this request")
                if rq["status"] == "declined":
                    return dict(rq)
                if rq["status"] in ("paid", "cancelled"):
                    raise PocketfulError(409, "request_not_pending", f"Cannot decline {rq['status']} request")

                conn.execute("UPDATE requests SET status = 'declined' WHERE request_id = ?;", (request_id,))
                res = dict(rq)
                res["status"] = "declined"
                return res
            finally:
                conn.close()

    def cancel_request(self, caller: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_connection()
            try:
                rq = conn.execute("SELECT * FROM requests WHERE request_id = ?;", (request_id,)).fetchone()
                if not rq:
                    raise PocketfulError(404, "not_found", "Request not found")
                if rq["requester_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only the requester may cancel this request")
                if rq["status"] == "cancelled":
                    return dict(rq)
                if rq["status"] in ("paid", "declined"):
                    raise PocketfulError(409, "request_not_pending", f"Cannot cancel {rq['status']} request")

                conn.execute("UPDATE requests SET status = 'cancelled' WHERE request_id = ?;", (request_id,))
                res = dict(rq)
                res["status"] = "cancelled"
                return res
            finally:
                conn.close()

    def list_requests(self, caller: Dict[str, Any], direction: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], bool]:
        if direction not in (None, "incoming", "outgoing"):
            raise PocketfulError(422, "validation_failed", "Invalid direction filter")
        if status not in (None, "pending", "paid", "declined", "cancelled"):
            raise PocketfulError(422, "validation_failed", "Invalid status filter")
        if limit < 1 or limit > 200 or offset < 0:
            raise PocketfulError(422, "validation_failed", "Invalid pagination limit or offset")

        with self._lock:
            conn = self._get_connection()
            try:
                where_clauses = []
                params: List[Any] = []

                if direction == "incoming":
                    where_clauses.append("payer_id = ?")
                    params.append(caller["id"])
                elif direction == "outgoing":
                    where_clauses.append("requester_id = ?")
                    params.append(caller["id"])
                else:
                    where_clauses.append("(requester_id = ? OR payer_id = ?)")
                    params.extend([caller["id"], caller["id"]])

                if status is not None:
                    where_clauses.append("status = ?")
                    params.append(status)

                where_sql = " AND ".join(where_clauses)
                query = f"""
                    SELECT * FROM requests
                    WHERE {where_sql}
                    ORDER BY created_at DESC, request_id DESC
                    LIMIT ? OFFSET ?;
                """
                params.extend([limit + 1, offset])
                rows = conn.execute(query, tuple(params)).fetchall()
                has_more = len(rows) > limit
                result = [dict(r) for r in rows[:limit]]
                return result, has_more
            finally:
                conn.close()

    def create_split(self, caller: Dict[str, Any], amount: Any, participant_handles: List[str], note: str = "") -> Dict[str, Any]:
        amount = validate_amount(amount)
        if not participant_handles or not isinstance(participant_handles, list):
            raise PocketfulError(422, "validation_failed", "participant_handles cannot be empty")
        if len(participant_handles) != len(set(participant_handles)):
            raise PocketfulError(422, "validation_failed", "Duplicate handles in participant_handles")
        for h in participant_handles:
            validate_handle(h, "participant_handles")
        if not isinstance(note, str) or len(note) > 200:
            raise PocketfulError(422, "validation_failed", "Note must be a string up to 200 characters")

        n = len(participant_handles)
        shares_amounts = equal_split(amount, n)
        meta = self.get_meta()
        currency = meta["currency"]
        sp_id = f"sp_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                participants = []
                for h in participant_handles:
                    row = conn.execute("SELECT * FROM users WHERE handle = ?;", (h,)).fetchone()
                    if not row:
                        raise PocketfulError(404, "not_found", f"User '{h}' not found")
                    participants.append(dict(row))

                shares = []
                created_requests = []
                for p, share_amt in zip(participants, shares_amounts):
                    shares.append({"handle": p["handle"], "amount": share_amt})
                    if p["id"] != caller["id"]:
                        rqid = f"rq_{uuid.uuid4().hex[:12]}"
                        conn.execute("""
                            INSERT INTO requests (request_id, requester_id, requester_handle, payer_id, payer_handle,
                                                 amount, currency, note, status, payment_id, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?);
                        """, (rqid, caller["id"], caller["handle"], p["id"], p["handle"],
                              share_amt, currency, note, now))
                        created_requests.append({
                            "request_id": rqid,
                            "requester_id": caller["id"],
                            "requester_handle": caller["handle"],
                            "payer_id": p["id"],
                            "payer_handle": p["handle"],
                            "amount": share_amt,
                            "currency": currency,
                            "note": note,
                            "status": "pending",
                            "payment_id": None,
                            "created_at": now
                        })

                conn.execute("""
                    INSERT INTO splits (split_id, amount, currency, note, shares_json, requests_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (sp_id, amount, currency, note, json.dumps(shares), json.dumps(created_requests), now))

                conn.execute("COMMIT;")
                return {
                    "split_id": sp_id,
                    "amount": amount,
                    "currency": currency,
                    "note": note,
                    "shares": shares,
                    "requests": created_requests,
                    "created_at": now
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def list_activity(self, caller: Dict[str, Any], limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], bool]:
        if limit < 1 or limit > 200 or offset < 0:
            raise PocketfulError(422, "validation_failed", "Invalid pagination limit or offset")

        with self._lock:
            conn = self._get_connection()
            try:
                query = """
                    SELECT * FROM payments
                    WHERE visibility = 'public' OR from_user_id = ? OR to_user_id = ?
                    ORDER BY created_at DESC, payment_id DESC
                    LIMIT ? OFFSET ?;
                """
                rows = conn.execute(query, (caller["id"], caller["id"], limit + 1, offset)).fetchall()
                has_more = len(rows) > limit
                result = [dict(r) for r in rows[:limit]]
                return result, has_more
            finally:
                conn.close()

    def create_settlement(self, caller: Dict[str, Any], transfers: List[Dict[str, Any]]) -> Dict[str, Any]:
        meta = self.get_meta()
        if caller["id"] not in meta.get("settlement_operator_ids", []):
            raise PocketfulError(403, "forbidden", "Only authorized settlement operators can execute settlements")

        if not transfers or not isinstance(transfers, list) or len(transfers) < 1 or len(transfers) > 32:
            raise PocketfulError(422, "validation_failed", "transfers must contain between 1 and 32 items")

        currency = meta["currency"]
        settlement_id = f"st_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                validated_transfers = []
                net_deltas: Dict[str, int] = {}

                for t in transfers:
                    if not isinstance(t, dict):
                        raise PocketfulError(422, "validation_failed", "Malformed transfer object")
                    amt = validate_amount(t.get("amount"))
                    f_handle = validate_handle(t.get("from_handle"), "from_handle")
                    t_handle = validate_handle(t.get("to_handle"), "to_handle")
                    note = str(t.get("note", ""))
                    vis = str(t.get("visibility", "public"))

                    if len(note) > 200:
                        raise PocketfulError(422, "validation_failed", "Note must be a string up to 200 characters")
                    if f_handle == t_handle:
                        raise PocketfulError(422, "self_payment", "Self-transfer is not permitted")
                    if vis not in ("public", "private"):
                        raise PocketfulError(422, "validation_failed", "Invalid visibility")

                    f_user = conn.execute("SELECT * FROM users WHERE handle = ?;", (f_handle,)).fetchone()
                    if not f_user:
                        raise PocketfulError(404, "not_found", f"Sender '{f_handle}' not found")
                    t_user = conn.execute("SELECT * FROM users WHERE handle = ?;", (t_handle,)).fetchone()
                    if not t_user:
                        raise PocketfulError(404, "not_found", f"Receiver '{t_handle}' not found")

                    validated_transfers.append({
                        "from_user": dict(f_user),
                        "to_user": dict(t_user),
                        "amount": amt,
                        "note": note,
                        "visibility": vis
                    })

                    net_deltas[f_user["id"]] = net_deltas.get(f_user["id"], 0) - amt
                    net_deltas[t_user["id"]] = net_deltas.get(t_user["id"], 0) + amt

                # In Stage 2, available funds after debits must be non-negative
                for uid, delta in net_deltas.items():
                    if delta < 0:
                        total, available, held = self.get_user_balances(conn, uid, now)
                        if available + delta < 0:
                            raise PocketfulError(409, "insufficient_funds", "Settlement causes wallet deficit against available funds")

                # Apply balance movements
                for uid, delta in net_deltas.items():
                    conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (delta, uid))

                created_payments = []
                for item in validated_transfers:
                    pid = f"p_{uuid.uuid4().hex[:12]}"
                    conn.execute("""
                        INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                              amount, currency, note, visibility, request_id, settlement_id, authorization_id, refund_of, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?);
                    """, (pid, item["from_user"]["id"], item["from_user"]["handle"],
                          item["to_user"]["id"], item["to_user"]["handle"],
                          item["amount"], currency, item["note"], item["visibility"],
                          None, settlement_id, now))
                    conn.execute("""
                        INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                        VALUES (?, 1, ?, ?, ?, '', NULL);
                    """, (pid, item["amount"], now, now))
                    created_payments.append({
                        "payment_id": pid,
                        "from_user_id": item["from_user"]["id"],
                        "from_handle": item["from_user"]["handle"],
                        "to_user_id": item["to_user"]["id"],
                        "to_handle": item["to_user"]["handle"],
                        "amount": item["amount"],
                        "currency": currency,
                        "note": item["note"],
                        "visibility": item["visibility"],
                        "request_id": None,
                        "settlement_id": settlement_id,
                        "authorization_id": None,
                        "refund_of": None,
                        "created_at": now
                    })

                conn.execute("COMMIT;")
                return {
                    "settlement_id": settlement_id,
                    "committed_at": now,
                    "payments": created_payments
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    # ==============================================================================
    # Stage 2: Payment Authorizations (Holds & Captures)
    # ==============================================================================

    def create_authorization(self, caller: Dict[str, Any], to_handle: str, amount: Any, note: str = "", visibility: str = "public") -> Dict[str, Any]:
        """Creates a payment hold per Stage 2 specification."""
        amount = validate_amount(amount)
        to_handle = validate_handle(to_handle, "to_handle")
        if to_handle == caller["handle"]:
            raise PocketfulError(422, "self_payment", "Cannot authorize payment to yourself")
        if not isinstance(note, str) or len(note) > 200:
            raise PocketfulError(422, "validation_failed", "Note must be a string up to 200 characters")
        if visibility not in ("public", "private"):
            raise PocketfulError(422, "validation_failed", "Visibility must be 'public' or 'private'")

        meta = self.get_meta()
        currency = meta["currency"]
        ttl = meta.get("authorization_ttl_seconds", 600)
        aid = f"a_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        now_dt = datetime.now(timezone.utc)
        expires_at = (now_dt + timedelta(seconds=ttl)).isoformat()

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                recip = conn.execute("SELECT * FROM users WHERE handle = ?;", (to_handle,)).fetchone()
                if not recip:
                    raise PocketfulError(404, "not_found", f"User with handle '{to_handle}' not found")

                total, available, held = self.get_user_balances(conn, caller["id"], now)
                if available < amount:
                    raise PocketfulError(409, "insufficient_funds", "Insufficient available funds for authorization")

                conn.execute("""
                    INSERT INTO authorizations (authorization_id, from_user_id, from_handle, to_user_id, to_handle,
                                                amount, captured_amount, currency, note, visibility, status, expires_at, closed_at,
                                                payment_id, payment_ids, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'open', ?, NULL, NULL, '[]', ?);
                """, (aid, caller["id"], caller["handle"], recip["id"], recip["handle"],
                      amount, currency, note, visibility, expires_at, now))

                conn.execute("COMMIT;")
                return {
                    "authorization_id": aid,
                    "from_user_id": caller["id"],
                    "from_handle": caller["handle"],
                    "to_user_id": recip["id"],
                    "to_handle": recip["handle"],
                    "amount": amount,
                    "captured_amount": 0,
                    "remaining_amount": amount,
                    "currency": currency,
                    "note": note,
                    "visibility": visibility,
                    "status": "open",
                    "expires_at": expires_at,
                    "closed_at": None,
                    "payment_id": None,
                    "created_at": now
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def capture_authorization(self, caller: Dict[str, Any], authorization_id: str, amount: Optional[Any] = None, final: bool = True) -> Dict[str, Any]:
        """Receiver captures funds from an open hold per Stage 2 specification."""
        meta = self.get_meta()
        currency = meta["currency"]
        now = utc_now_iso()
        pid = f"p_{uuid.uuid4().hex[:12]}"

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                self._expire_holds(conn, now)
                auth = conn.execute("SELECT * FROM authorizations WHERE authorization_id = ?;", (authorization_id,)).fetchone()
                if not auth:
                    raise PocketfulError(404, "not_found", "Authorization not found")
                if auth["to_user_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only the designated receiver may capture an authorization")

                if auth["status"] != "open":
                    if auth["status"] == "expired" or auth["expires_at"] <= now:
                        raise PocketfulError(409, "authorization_expired", "Authorization has expired")
                    raise PocketfulError(409, "authorization_not_open", f"Authorization is '{auth['status']}'")

                if auth["expires_at"] <= now:
                    conn.execute("UPDATE authorizations SET status = 'expired', closed_at = expires_at WHERE authorization_id = ?;", (authorization_id,))
                    raise PocketfulError(409, "authorization_expired", "Authorization has expired")

                remaining = auth["amount"] - auth["captured_amount"]
                if amount is None:
                    capture_amt = remaining
                else:
                    capture_amt = validate_amount(amount)
                    if capture_amt > remaining:
                        raise PocketfulError(422, "capture_exceeds_authorization", "Amount exceeds remaining uncaptured hold")

                # Debit payer, credit receiver
                conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?;", (capture_amt, auth["from_user_id"]))
                conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (capture_amt, auth["to_user_id"]))

                # Record payment linking authorization_id
                conn.execute("""
                    INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                          amount, currency, note, visibility, request_id, settlement_id, authorization_id, refund_of, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?);
                """, (pid, auth["from_user_id"], auth["from_handle"], auth["to_user_id"], auth["to_handle"],
                      capture_amt, currency, auth["note"], auth["visibility"], authorization_id, now))

                conn.execute("""
                    INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                    VALUES (?, 1, ?, ?, ?, '', NULL);
                """, (pid, capture_amt, now, now))

                new_captured = auth["captured_amount"] + capture_amt
                existing_pids = json.loads(auth["payment_ids"]) if auth["payment_ids"] else []
                new_pids = existing_pids + [pid]

                new_status = "captured" if (final or capture_amt == remaining) else "open"
                closed_at = now if new_status == "captured" else None
                conn.execute("""
                    UPDATE authorizations
                    SET captured_amount = ?, status = ?, closed_at = ?, payment_id = ?, payment_ids = ?
                    WHERE authorization_id = ?;
                """, (new_captured, new_status, closed_at, pid, json.dumps(new_pids), authorization_id))

                conn.execute("COMMIT;")
                return {
                    "payment_id": pid,
                    "from_user_id": auth["from_user_id"],
                    "from_handle": auth["from_handle"],
                    "to_user_id": auth["to_user_id"],
                    "to_handle": auth["to_handle"],
                    "amount": capture_amt,
                    "currency": currency,
                    "note": auth["note"],
                    "visibility": auth["visibility"],
                    "request_id": None,
                    "settlement_id": None,
                    "authorization_id": authorization_id,
                    "refund_of": None,
                    "created_at": now
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def void_authorization(self, caller: Dict[str, Any], authorization_id: str) -> Dict[str, Any]:
        """Payer voids hold and releases remaining funds."""
        now = utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                self._expire_holds(conn, now)
                auth = conn.execute("SELECT * FROM authorizations WHERE authorization_id = ?;", (authorization_id,)).fetchone()
                if not auth:
                    raise PocketfulError(404, "not_found", "Authorization not found")
                if auth["from_user_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only the payer may void an authorization")

                if auth["status"] == "voided":
                    res = dict(auth)
                    res["remaining_amount"] = 0
                    return res
                if auth["status"] != "open":
                    raise PocketfulError(409, "authorization_not_open", f"Authorization is '{auth['status']}'")

                conn.execute("UPDATE authorizations SET status = 'voided', closed_at = ? WHERE authorization_id = ?;", (now, authorization_id))
                conn.execute("COMMIT;")
                res = dict(auth)
                res["status"] = "voided"
                res["closed_at"] = now
                res["remaining_amount"] = 0
                return res
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def list_authorizations(self, caller: Dict[str, Any], direction: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], bool]:
        if direction not in (None, "incoming", "outgoing"):
            raise PocketfulError(422, "validation_failed", "Invalid direction filter")
        if status not in (None, "open", "captured", "voided", "expired"):
            raise PocketfulError(422, "validation_failed", "Invalid status filter")
        if limit < 1 or limit > 200 or offset < 0:
            raise PocketfulError(422, "validation_failed", "Invalid pagination limit or offset")

        now = utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            try:
                self._expire_holds(conn, now)
                where_clauses = []
                params: List[Any] = []

                if direction == "incoming":
                    where_clauses.append("to_user_id = ?")
                    params.append(caller["id"])
                elif direction == "outgoing":
                    where_clauses.append("from_user_id = ?")
                    params.append(caller["id"])
                else:
                    where_clauses.append("(from_user_id = ? OR to_user_id = ?)")
                    params.extend([caller["id"], caller["id"]])

                if status is not None:
                    where_clauses.append("status = ?")
                    params.append(status)

                where_sql = " AND ".join(where_clauses)
                query = f"""
                    SELECT * FROM authorizations
                    WHERE {where_sql}
                    ORDER BY created_at DESC, authorization_id DESC
                    LIMIT ? OFFSET ?;
                """
                params.extend([limit + 1, offset])
                rows = conn.execute(query, tuple(params)).fetchall()
                has_more = len(rows) > limit

                result = []
                for r in rows[:limit]:
                    item = dict(r)
                    rem = item["amount"] - item["captured_amount"] if item["status"] == "open" else 0
                    item["remaining_amount"] = rem
                    result.append(item)
                return result, has_more
            finally:
                conn.close()

    # ==============================================================================
    # Stage 3: Bitemporal Revisions, Statements & Corrections
    # ==============================================================================

    def get_user_balances_as_of(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        as_of: Optional[str] = None,
        known_at: Optional[str] = None
    ) -> Tuple[int, int, int]:
        """
        Calculates total, available, and held balances as of a specified effective instant (as_of)
        using only information recorded at or before known_at per Stage 3 specification.
        """
        now_dt = datetime.now(timezone.utc)
        as_of_dt = parse_rfc3339(as_of, "as_of") if as_of is not None else now_dt
        known_at_dt = parse_rfc3339(known_at, "known_at") if known_at is not None else None

        urow = conn.execute("SELECT opening_balance FROM users WHERE id = ?;", (user_id,)).fetchone()
        if not urow:
            return 0, 0, 0
        opening_bal = urow["opening_balance"]

        # Select all payments involving user
        pmts = conn.execute("""
            SELECT payment_id, from_user_id, to_user_id
            FROM payments
            WHERE from_user_id = ? OR to_user_id = ?;
        """, (user_id, user_id)).fetchall()

        net_delta = 0
        for p in pmts:
            pid = p["payment_id"]
            if known_at_dt is not None:
                revs = conn.execute("""
                    SELECT revision, amount, effective_at, recorded_at
                    FROM payment_revisions
                    WHERE payment_id = ?
                    ORDER BY revision DESC;
                """, (pid,)).fetchall()
                selected_rev = None
                for r in revs:
                    if parse_rfc3339(r["recorded_at"]) <= known_at_dt:
                        selected_rev = r
                        break
            else:
                selected_rev = conn.execute("""
                    SELECT revision, amount, effective_at, recorded_at
                    FROM payment_revisions
                    WHERE payment_id = ?
                    ORDER BY revision DESC LIMIT 1;
                """, (pid,)).fetchone()

            if selected_rev is None:
                continue

            eff_dt = parse_rfc3339(selected_rev["effective_at"])
            if eff_dt <= as_of_dt:
                amt = selected_rev["amount"]
                if p["from_user_id"] == user_id:
                    net_delta -= amt
                else:
                    net_delta += amt

        total = opening_bal + net_delta

        # Calculate held amount for user
        auths = conn.execute("""
            SELECT authorization_id, amount, status, expires_at, closed_at, created_at
            FROM authorizations
            WHERE from_user_id = ?;
        """, (user_id,)).fetchall()

        held = 0
        for a in auths:
            c_dt = parse_rfc3339(a["created_at"])
            e_dt = parse_rfc3339(a["expires_at"])

            if known_at_dt is not None and c_dt > known_at_dt:
                continue
            if c_dt > as_of_dt:
                continue
            if as_of_dt >= e_dt:
                continue

            is_closed = False
            if a["closed_at"]:
                cl_dt = parse_rfc3339(a["closed_at"])
                if (known_at_dt is None or cl_dt <= known_at_dt) and cl_dt <= as_of_dt:
                    is_closed = True

            if is_closed:
                continue

            aid = a["authorization_id"]
            cap_pmts = conn.execute("""
                SELECT amount, created_at
                FROM payments
                WHERE authorization_id = ?;
            """, (aid,)).fetchall()

            captured_so_far = 0
            for cp in cap_pmts:
                cp_dt = parse_rfc3339(cp["created_at"])
                cap_known = (known_at_dt is None or cp_dt <= known_at_dt)
                cap_effective = (cp_dt <= as_of_dt)
                if cap_known and cap_effective:
                    captured_so_far += cp["amount"]

            rem = max(0, a["amount"] - captured_so_far)
            held += rem

        available = max(0, total - held)
        return total, available, held

    def _check_historical_overdraft(
        self,
        conn: sqlite3.Connection,
        sender_id: str,
        receiver_id: str,
        now_dt: datetime
    ) -> None:
        """
        Validates that under the latest recorded revisions, neither sender nor receiver
        drops below 0 in total or available balance at any past effective/event boundary.
        """
        now_iso = now_dt.isoformat()
        boundary_dts = {now_dt}

        # Collect boundaries from payments
        pmts = conn.execute("""
            SELECT payment_id FROM payments
            WHERE from_user_id IN (?, ?) OR to_user_id IN (?, ?);
        """, (sender_id, receiver_id, sender_id, receiver_id)).fetchall()

        for p in pmts:
            rev = conn.execute("""
                SELECT effective_at FROM payment_revisions
                WHERE payment_id = ?
                ORDER BY revision DESC LIMIT 1;
            """, (p["payment_id"],)).fetchone()
            if rev:
                eff_dt = parse_rfc3339(rev["effective_at"])
                if eff_dt <= now_dt:
                    boundary_dts.add(eff_dt)

        # Collect boundaries from authorizations
        auths = conn.execute("""
            SELECT created_at, closed_at, expires_at FROM authorizations
            WHERE from_user_id IN (?, ?) OR to_user_id IN (?, ?);
        """, (sender_id, receiver_id, sender_id, receiver_id)).fetchall()

        for a in auths:
            for field in ("created_at", "closed_at", "expires_at"):
                val = a[field]
                if val:
                    dt = parse_rfc3339(val)
                    if dt <= now_dt:
                        boundary_dts.add(dt)

        # Sort all boundaries chronologically
        sorted_boundaries = sorted(boundary_dts)

        for b_dt in sorted_boundaries:
            b_iso = b_dt.isoformat()
            s_tot, s_avail, _ = self.get_user_balances_as_of(conn, sender_id, as_of=b_iso, known_at=now_iso)
            if s_tot < 0 or s_avail < 0:
                raise PocketfulError(409, "historical_overdraft", "Correction causes historical overdraft for sender")

            r_tot, r_avail, _ = self.get_user_balances_as_of(conn, receiver_id, as_of=b_iso, known_at=now_iso)
            if r_tot < 0 or r_avail < 0:
                raise PocketfulError(409, "historical_overdraft", "Correction causes historical overdraft for receiver")

    def correct_payment(
        self,
        caller: Dict[str, Any],
        payment_id: str,
        expected_revision: int,
        amount: int,
        effective_at: str,
        reason: str
    ) -> Dict[str, Any]:
        """
        Creates an immutable correction revision per Stage 3 specification.
        Enforces permissions, immutability of linked payments, concurrency, current funds,
        and historical non-negativity across all effective-time boundaries.
        """
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                pmt = conn.execute("SELECT * FROM payments WHERE payment_id = ?;", (payment_id,)).fetchone()
                if not pmt:
                    raise PocketfulError(404, "not_found", "Payment not found")
                if pmt["from_user_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only original sender can correct a payment")
                if pmt["settlement_id"] is not None:
                    raise PocketfulError(422, "incomplete_settlement", "Settlement members must be corrected as a complete batch")
                if pmt["authorization_id"] is not None or pmt["refund_of"] is not None:
                    raise PocketfulError(422, "linked_payment_immutable", "Captures and refund payments cannot be corrected")

                latest_rev = conn.execute("""
                    SELECT * FROM payment_revisions
                    WHERE payment_id = ?
                    ORDER BY revision DESC LIMIT 1;
                """, (payment_id,)).fetchone()
                if not latest_rev:
                    raise PocketfulError(404, "not_found", "Payment revisions not found")

                if latest_rev["revision"] != expected_revision:
                    raise PocketfulError(409, "stale_revision", f"Expected revision {expected_revision}, but current revision is {latest_rev['revision']}")

                # Check refund exceeds payment
                sum_refunds_row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE refund_of = ?;", (payment_id,)).fetchone()
                sum_refunds = sum_refunds_row["total"]
                if amount < sum_refunds:
                    raise PocketfulError(422, "refund_exceeds_payment", "Correction cannot reduce payment below already-refunded amount")

                curr_amt = latest_rev["amount"]
                delta = amount - curr_amt
                sender_id = pmt["from_user_id"]
                receiver_id = pmt["to_user_id"]

                # Ensure recorded_at strictly increases
                last_rec_str = latest_rev["recorded_at"]
                last_rec_dt = parse_rfc3339(last_rec_str)
                if now_dt <= last_rec_dt:
                    now_dt = last_rec_dt + timedelta(microseconds=1)
                    now_iso = now_dt.isoformat()

                # Current affordability check (insufficient_funds takes precedence over historical_overdraft)
                if delta > 0:
                    s_tot, s_avail, _ = self.get_user_balances(conn, sender_id, now_iso)
                    if s_avail < delta:
                        raise PocketfulError(409, "insufficient_funds", "Insufficient available funds for correction")
                elif delta < 0:
                    r_tot, r_avail, _ = self.get_user_balances(conn, receiver_id, now_iso)
                    if r_avail < abs(delta):
                        raise PocketfulError(409, "insufficient_funds", "Receiver has insufficient funds for correction reversal")

                # Historical overdraft check inside savepoint
                new_rev = expected_revision + 1
                conn.execute("SAVEPOINT check_overdraft;")
                try:
                    if delta > 0:
                        conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?;", (delta, sender_id))
                        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (delta, receiver_id))
                    elif delta < 0:
                        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (abs(delta), sender_id))
                        conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?;", (abs(delta), receiver_id))

                    conn.execute("""
                        INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                        VALUES (?, ?, ?, ?, ?, ?, NULL);
                    """, (payment_id, new_rev, amount, effective_at, now_iso, reason))

                    self._check_historical_overdraft(conn, sender_id, receiver_id, now_dt)
                    conn.execute("RELEASE SAVEPOINT check_overdraft;")
                except Exception:
                    conn.execute("ROLLBACK TO SAVEPOINT check_overdraft;")
                    conn.execute("RELEASE SAVEPOINT check_overdraft;")
                    raise

                conn.execute("COMMIT;")
                return {
                    "payment_id": payment_id,
                    "revision": new_rev,
                    "amount": amount,
                    "effective_at": effective_at,
                    "recorded_at": now_iso,
                    "reason": reason,
                    "correction_batch_id": None
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def get_payment_revisions(self, caller: Dict[str, Any], payment_id: str) -> Dict[str, Any]:
        """Returns revision history for a payment, accessible only to sender and receiver."""
        with self._lock:
            conn = self._get_connection()
            try:
                pmt = conn.execute("SELECT * FROM payments WHERE payment_id = ?;", (payment_id,)).fetchone()
                if not pmt:
                    raise PocketfulError(404, "not_found", "Payment not found")
                if caller["id"] != pmt["from_user_id"] and caller["id"] != pmt["to_user_id"]:
                    raise PocketfulError(404, "not_found", "Payment not found")

                rows = conn.execute("""
                    SELECT payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id
                    FROM payment_revisions
                    WHERE payment_id = ?
                    ORDER BY revision ASC;
                """, (payment_id,)).fetchall()
                return {"revisions": [dict(r) for r in rows]}
            finally:
                conn.close()

    def get_statement(
        self,
        caller: Dict[str, Any],
        from_str: Optional[str] = None,
        to_str: Optional[str] = None,
        known_at_str: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        snapshot_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Returns statements with stable snapshot pagination per Stage 3 specification."""
        with self._lock:
            conn = self._get_connection()
            try:
                if snapshot_token is not None:
                    if from_str is not None or to_str is not None or known_at_str is not None:
                        raise PocketfulError(422, "validation_failed", "from, to, and known_at cannot be used with snapshot")

                    snap = conn.execute("SELECT * FROM snapshots WHERE token = ?;", (snapshot_token,)).fetchone()
                    if not snap or snap["user_id"] != caller["id"]:
                        raise PocketfulError(404, "not_found", "Snapshot not found")

                    data = json.loads(snap["data_json"])
                    all_entries = data["entries"]
                    sliced = all_entries[offset : offset + limit]
                    has_more = (offset + limit) < len(all_entries)
                    return {
                        "opening_balance": data["opening_balance"],
                        "entries": sliced,
                        "closing_balance": data["closing_balance"],
                        "has_more": has_more,
                        "snapshot": snapshot_token
                    }

                now_dt = datetime.now(timezone.utc)
                from_dt = parse_rfc3339(from_str, "from") if from_str is not None else None
                to_dt = parse_rfc3339(to_str, "to") if to_str is not None else now_dt
                known_at_dt = parse_rfc3339(known_at_str, "known_at") if known_at_str is not None else None

                urow = conn.execute("SELECT opening_balance FROM users WHERE id = ?;", (caller["id"],)).fetchone()
                if not urow:
                    raise PocketfulError(404, "not_found", "User not found")
                wallet_opening = urow["opening_balance"]

                # Payments sent or received by caller
                pmts = conn.execute("""
                    SELECT * FROM payments
                    WHERE from_user_id = ? OR to_user_id = ?;
                """, (caller["id"], caller["id"])).fetchall()

                known_payments = []
                for p in pmts:
                    pid = p["payment_id"]
                    if known_at_dt is not None:
                        revs = conn.execute("""
                            SELECT revision, amount, effective_at, recorded_at
                            FROM payment_revisions
                            WHERE payment_id = ?
                            ORDER BY revision DESC;
                        """, (pid,)).fetchall()
                        selected_rev = None
                        for r in revs:
                            if parse_rfc3339(r["recorded_at"]) <= known_at_dt:
                                selected_rev = r
                                break
                    else:
                        selected_rev = conn.execute("""
                            SELECT revision, amount, effective_at, recorded_at
                            FROM payment_revisions
                            WHERE payment_id = ?
                            ORDER BY revision DESC LIMIT 1;
                        """, (pid,)).fetchone()

                    if selected_rev is None:
                        continue

                    sel_amt = selected_rev["amount"]
                    is_sender = (p["from_user_id"] == caller["id"])
                    delta = -sel_amt if is_sender else sel_amt
                    eff_dt = parse_rfc3339(selected_rev["effective_at"])

                    known_payments.append({
                        "payment_row": dict(p),
                        "selected_amount": sel_amt,
                        "delta": delta,
                        "revision": selected_rev["revision"],
                        "effective_at": selected_rev["effective_at"],
                        "effective_dt": eff_dt,
                        "recorded_at": selected_rev["recorded_at"],
                        "payment_id": pid
                    })

                # Sort chronologically by effective_at ascending, then payment_id ascending
                known_payments.sort(key=lambda x: (x["effective_dt"], x["payment_id"]))

                # Walk running balance_after
                running = wallet_opening
                for kp in known_payments:
                    running += kp["delta"]
                    kp["balance_after"] = running

                # opening_balance: balance immediately before from
                if from_dt is None:
                    opening_balance = wallet_opening
                else:
                    opening_balance = wallet_opening + sum(
                        kp["delta"] for kp in known_payments if kp["effective_dt"] < from_dt
                    )

                # closing_balance: balance immediately before to
                closing_balance = wallet_opening + sum(
                    kp["delta"] for kp in known_payments if kp["effective_dt"] < to_dt
                )

                # Window entries: [from, to)
                window_entries = []
                for kp in known_payments:
                    if from_dt is not None and kp["effective_dt"] < from_dt:
                        continue
                    if kp["effective_dt"] >= to_dt:
                        continue

                    p_dict = dict(kp["payment_row"])
                    p_dict["amount"] = kp["selected_amount"]
                    window_entries.append({
                        "payment": p_dict,
                        "delta": kp["delta"],
                        "balance_after": kp["balance_after"],
                        "revision": kp["revision"],
                        "effective_at": kp["effective_at"],
                        "recorded_at": kp["recorded_at"]
                    })

                s_token = f"snap_{uuid.uuid4().hex}"
                snap_payload = {
                    "opening_balance": opening_balance,
                    "closing_balance": closing_balance,
                    "entries": window_entries
                }
                conn.execute("""
                    INSERT INTO snapshots (token, user_id, data_json, created_at)
                    VALUES (?, ?, ?, ?);
                """, (s_token, caller["id"], json.dumps(snap_payload), utc_now_iso()))

                sliced = window_entries[offset : offset + limit]
                has_more = (offset + limit) < len(window_entries)
                return {
                    "opening_balance": opening_balance,
                    "entries": sliced,
                    "closing_balance": closing_balance,
                    "has_more": has_more,
                    "snapshot": s_token
                }
            finally:
                conn.close()

    def create_refund(self, caller: Dict[str, Any], payment_id: str, amount: Any) -> Dict[str, Any]:
        """
        Executes a partial or full refund of an existing payment per Stage 4 specification.
        Caller must be original receiver, total refunds cannot exceed current payment amount,
        and funds are debited from caller's available balance.
        """
        amount = validate_amount(amount)
        now = utc_now_iso()

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                pmt = conn.execute("SELECT * FROM payments WHERE payment_id = ?;", (payment_id,)).fetchone()
                if not pmt:
                    raise PocketfulError(404, "not_found", "Payment not found")

                # Only original receiver may refund
                if pmt["to_user_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only original receiver may refund a payment")

                # Target cannot be a refund
                if pmt["refund_of"] is not None:
                    raise PocketfulError(422, "invalid_refund_target", "Cannot refund a refund payment")

                # Current corrected amount of target payment
                latest_rev = conn.execute("""
                    SELECT amount FROM payment_revisions
                    WHERE payment_id = ?
                    ORDER BY revision DESC LIMIT 1;
                """, (payment_id,)).fetchone()
                current_target_amount = latest_rev["amount"] if latest_rev else pmt["amount"]

                # Sum of existing refunds for target
                sum_row = conn.execute("""
                    SELECT COALESCE(SUM(amount), 0) AS total
                    FROM payments
                    WHERE refund_of = ?;
                """, (payment_id,)).fetchone()
                sum_refunds = sum_row["total"]

                if sum_refunds + amount > current_target_amount:
                    raise PocketfulError(422, "refund_exceeds_payment", "Total refunds exceed current payment amount")

                # Check caller's available funds
                total, available, held = self.get_user_balances(conn, caller["id"], now)
                if available < amount:
                    raise PocketfulError(409, "insufficient_funds", "Insufficient available funds for refund")

                # Double-entry balance transfer: debit receiver (caller), credit sender
                conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?;", (amount, caller["id"]))
                conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (amount, pmt["from_user_id"]))

                new_pid = f"p_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                    INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                          amount, currency, note, visibility, request_id, settlement_id, authorization_id, refund_of, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?);
                """, (new_pid, caller["id"], caller["handle"], pmt["from_user_id"], pmt["from_handle"],
                      amount, pmt["currency"], pmt["note"], pmt["visibility"], payment_id, now))

                conn.execute("""
                    INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                    VALUES (?, 1, ?, ?, ?, '', NULL);
                """, (new_pid, amount, now, now))

                conn.execute("COMMIT;")
                return {
                    "payment_id": new_pid,
                    "from_user_id": caller["id"],
                    "from_handle": caller["handle"],
                    "to_user_id": pmt["from_user_id"],
                    "to_handle": pmt["from_handle"],
                    "amount": amount,
                    "currency": pmt["currency"],
                    "note": pmt["note"],
                    "visibility": pmt["visibility"],
                    "request_id": None,
                    "settlement_id": None,
                    "authorization_id": None,
                    "refund_of": payment_id,
                    "created_at": now
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def create_correction_batch(self, caller: Dict[str, Any], raw_corrections: Any) -> Dict[str, Any]:
        """
        Executes an atomic batch of payment corrections per Stage 4 specification.
        Requires settlement operator, validates 1..32 items, settlement completeness,
        current available funds, and historical overdrafts across all affected users.
        """
        meta = self.get_meta()
        if caller["id"] not in meta.get("settlement_operator_ids", []):
            raise PocketfulError(403, "forbidden", "Only authorized settlement operators can execute correction batches")

        if isinstance(raw_corrections, list):
            validated_items = validate_correction_batch_payload({"corrections": raw_corrections})
        else:
            validated_items = validate_correction_batch_payload(raw_corrections)

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # 1. Item errors in input order
                target_payments = []
                latest_revisions = []

                for item in validated_items:
                    pid = item["payment_id"]
                    pmt = conn.execute("SELECT * FROM payments WHERE payment_id = ?;", (pid,)).fetchone()
                    if not pmt:
                        raise PocketfulError(404, "not_found", f"Payment '{pid}' not found")

                    # Captures and refunds remain immutable
                    if pmt["authorization_id"] is not None or pmt["refund_of"] is not None:
                        raise PocketfulError(422, "linked_payment_immutable", f"Payment '{pid}' is a capture or refund and cannot be corrected")

                    latest_rev = conn.execute("""
                        SELECT * FROM payment_revisions
                        WHERE payment_id = ?
                        ORDER BY revision DESC LIMIT 1;
                    """, (pid,)).fetchone()
                    if not latest_rev or latest_rev["revision"] != item["expected_revision"]:
                        curr_rev = latest_rev["revision"] if latest_rev else None
                        raise PocketfulError(409, "stale_revision", f"Expected revision {item['expected_revision']} for payment '{pid}', but current is {curr_rev}")

                    sum_row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE refund_of = ?;", (pid,)).fetchone()
                    sum_refunds = sum_row["total"]
                    if item["amount"] < sum_refunds:
                        raise PocketfulError(422, "refund_exceeds_payment", f"Correction reduces payment '{pid}' below already-refunded amount")

                    target_payments.append(pmt)
                    latest_revisions.append(latest_rev)

                # 2. Settlement completeness & identical effective instant check
                settlement_map: Dict[str, List[Dict[str, Any]]] = {}
                for item, pmt in zip(validated_items, target_payments):
                    sid = pmt["settlement_id"]
                    if sid is not None:
                        settlement_map.setdefault(sid, []).append({
                            "item": item,
                            "payment": pmt
                        })

                for sid, members in settlement_map.items():
                    all_settlement_rows = conn.execute("SELECT payment_id FROM payments WHERE settlement_id = ?;", (sid,)).fetchall()
                    all_settlement_pids = {r["payment_id"] for r in all_settlement_rows}
                    batch_settlement_pids = {m["item"]["payment_id"] for m in members}
                    if batch_settlement_pids != all_settlement_pids:
                        raise PocketfulError(422, "incomplete_settlement", f"Batch must include all members of settlement '{sid}'")

                    first_ts = members[0]["item"]["effective_dt"].timestamp()
                    for m in members:
                        if m["item"]["effective_dt"].timestamp() != first_ts:
                            raise PocketfulError(422, "validation_failed", f"Settlement members for settlement '{sid}' must share identical effective instant")

                # 3. Combined net changes for all affected users
                user_net_changes: Dict[str, int] = {}
                affected_user_ids = set()
                for item, pmt, prev_rev in zip(validated_items, target_payments, latest_revisions):
                    delta = item["amount"] - prev_rev["amount"]
                    s_id = pmt["from_user_id"]
                    r_id = pmt["to_user_id"]
                    affected_user_ids.add(s_id)
                    affected_user_ids.add(r_id)
                    user_net_changes[s_id] = user_net_changes.get(s_id, 0) - delta
                    user_net_changes[r_id] = user_net_changes.get(r_id, 0) + delta

                # 4. Resulting current available funds check
                now_dt = datetime.now(timezone.utc)
                now_iso = now_dt.isoformat()
                for uid, net_delta in user_net_changes.items():
                    if net_delta < 0:
                        tot, avail, _ = self.get_user_balances(conn, uid, now_iso)
                        if avail + net_delta < 0:
                            raise PocketfulError(409, "insufficient_funds", f"Insufficient available funds for user '{uid}'")

                # 5. Shared recorded_at strictly later than previous recorded_at of every member
                max_prev_dt = max(parse_rfc3339(rev["recorded_at"]) for rev in latest_revisions)
                if now_dt <= max_prev_dt:
                    now_dt = max_prev_dt + timedelta(microseconds=1)
                shared_recorded_at = now_dt.isoformat()
                batch_id = f"cb_{uuid.uuid4().hex[:12]}"

                # 6. Apply revisions atomically inside a savepoint and check historical overdraft
                conn.execute("SAVEPOINT check_batch;")
                try:
                    for uid, net_delta in user_net_changes.items():
                        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (net_delta, uid))

                    for item in validated_items:
                        new_rev = item["expected_revision"] + 1
                        conn.execute("""
                            INSERT INTO payment_revisions (payment_id, revision, amount, effective_at, recorded_at, reason, correction_batch_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?);
                        """, (item["payment_id"], new_rev, item["amount"], item["effective_at"], shared_recorded_at, item["reason"], batch_id))

                    boundary_dts = {now_dt}
                    for item in validated_items:
                        if item["effective_dt"] <= now_dt:
                            boundary_dts.add(item["effective_dt"])

                    for uid in affected_user_ids:
                        u_pmts = conn.execute("""
                            SELECT payment_id FROM payments
                            WHERE from_user_id = ? OR to_user_id = ?;
                        """, (uid, uid)).fetchall()
                        for p in u_pmts:
                            rev = conn.execute("""
                                SELECT effective_at FROM payment_revisions
                                WHERE payment_id = ?
                                ORDER BY revision DESC LIMIT 1;
                            """, (p["payment_id"],)).fetchone()
                            if rev:
                                eff_dt = parse_rfc3339(rev["effective_at"])
                                if eff_dt <= now_dt:
                                    boundary_dts.add(eff_dt)

                        u_auths = conn.execute("""
                            SELECT created_at, closed_at, expires_at FROM authorizations
                            WHERE from_user_id = ? OR to_user_id = ?;
                        """, (uid, uid)).fetchall()
                        for a in u_auths:
                            for fld in ("created_at", "closed_at", "expires_at"):
                                val = a[fld]
                                if val:
                                    dt = parse_rfc3339(val)
                                    if dt <= now_dt:
                                        boundary_dts.add(dt)

                    sorted_boundaries = sorted(boundary_dts)
                    for b_dt in sorted_boundaries:
                        b_iso = b_dt.isoformat()
                        for uid in affected_user_ids:
                            tot, avail, _ = self.get_user_balances_as_of(conn, uid, as_of=b_iso, known_at=shared_recorded_at)
                            if tot < 0 or avail < 0:
                                raise PocketfulError(409, "historical_overdraft", f"Correction batch causes historical overdraft for user '{uid}'")

                    conn.execute("RELEASE SAVEPOINT check_batch;")
                except Exception:
                    conn.execute("ROLLBACK TO SAVEPOINT check_batch;")
                    conn.execute("RELEASE SAVEPOINT check_batch;")
                    raise

                conn.execute("COMMIT;")

                applied_revisions = [
                    {
                        "payment_id": item["payment_id"],
                        "revision": item["expected_revision"] + 1,
                        "amount": item["amount"],
                        "effective_at": item["effective_at"],
                        "recorded_at": shared_recorded_at,
                        "reason": item["reason"],
                        "correction_batch_id": batch_id
                    }
                    for item in validated_items
                ]

                return {
                    "correction_batch_id": batch_id,
                    "recorded_at": shared_recorded_at,
                    "revisions": applied_revisions
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

