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
)


class LedgerEngine:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.getenv("POCKETFUL_DB_PATH", "pocketful_stage2.db")
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
                        balance INTEGER NOT NULL CHECK (balance >= 0)
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
                        created_at TEXT NOT NULL
                    );
                """)
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
                        payment_id TEXT,
                        payment_ids TEXT NOT NULL DEFAULT '[]',
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
            SET status = 'expired'
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

        # Validate seeded negative balances and open holds overflow
        for u in fixture.get("users", []):
            bal = u.get("balance", 0)
            if not isinstance(bal, int) or bal < 0:
                raise PocketfulError(422, "validation_failed", f"User {u.get('id')} has negative balance")
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
                    pwd_hash = hash_password(pwd)
                    conn.execute("""
                        INSERT INTO users (id, email, password_hash, display_name, handle, balance)
                        VALUES (?, ?, ?, ?, ?, ?);
                    """, (uid, email, pwd_hash, dname, handle, balance))

                for p in fixture.get("payments", []):
                    pid = p["id"]
                    fid = p["from_user_id"]
                    tid = p["to_user_id"]
                    amt = int(p["amount"])
                    note = str(p.get("note", ""))
                    vis = str(p.get("visibility", "public"))
                    rid = p.get("request_id")
                    sid = p.get("settlement_id")
                    aid = p.get("authorization_id")
                    cat = p.get("created_at", now)

                    f_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (fid,)).fetchone()["handle"]
                    t_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (tid,)).fetchone()["handle"]

                    conn.execute("""
                        INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                              amount, currency, note, visibility, request_id, settlement_id, authorization_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (pid, fid, f_handle, tid, t_handle, amt, currency, note, vis, rid, sid, aid, cat))

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

                    f_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (fid,)).fetchone()["handle"]
                    t_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (tid,)).fetchone()["handle"]

                    conn.execute("""
                        INSERT INTO authorizations (authorization_id, from_user_id, from_handle, to_user_id, to_handle,
                                                    amount, captured_amount, currency, note, visibility, status, expires_at,
                                                    payment_id, payment_ids, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (aid, fid, f_handle, tid, t_handle, amt, camt, currency, note, vis, st, eat, pmt_id, json.dumps(pids), cat))

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
                    conn.execute("""
                        INSERT INTO users (id, email, password_hash, display_name, handle, balance)
                        VALUES (?, ?, ?, ?, ?, ?);
                    """, (u["id"], u["email"], u["password_hash"], u["display_name"], u["handle"], u["balance"]))

                for s in state.get("sessions", []):
                    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?);", (s["token"], s["user_id"]))

                for p in state.get("payments", []):
                    conn.execute("""
                        INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                              amount, currency, note, visibility, request_id, settlement_id, authorization_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (p["payment_id"], p["from_user_id"], p["from_handle"], p["to_user_id"], p["to_handle"],
                          p["amount"], p["currency"], p.get("note", ""), p.get("visibility", "public"),
                          p.get("request_id"), p.get("settlement_id"), p.get("authorization_id"), p["created_at"]))

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
                    conn.execute("""
                        INSERT INTO authorizations (authorization_id, from_user_id, from_handle, to_user_id, to_handle,
                                                    amount, captured_amount, currency, note, visibility, status, expires_at,
                                                    payment_id, payment_ids, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (a["authorization_id"], a["from_user_id"], a["from_handle"], a["to_user_id"], a["to_handle"],
                          a["amount"], a.get("captured_amount", 0), a["currency"], a.get("note", ""),
                          a.get("visibility", "public"), a["status"], a["expires_at"],
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
                                          amount, currency, note, visibility, request_id, settlement_id, authorization_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?);
                """, (pid, caller["id"], caller["handle"], recip["id"], recip["handle"],
                      amount, currency, note, visibility, None, None, now))

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
                                          amount, currency, note, visibility, request_id, settlement_id, authorization_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?);
                """, (pid, caller["id"], caller["handle"], rq["requester_id"], rq["requester_handle"],
                      amount, currency, rq["note"], visibility, request_id, None, now))

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
                                              amount, currency, note, visibility, request_id, settlement_id, authorization_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?);
                    """, (pid, item["from_user"]["id"], item["from_user"]["handle"],
                          item["to_user"]["id"], item["to_user"]["handle"],
                          item["amount"], currency, item["note"], item["visibility"],
                          None, settlement_id, now))
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
                                                amount, captured_amount, currency, note, visibility, status, expires_at,
                                                payment_id, payment_ids, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'open', ?, NULL, '[]', ?);
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
                    conn.execute("UPDATE authorizations SET status = 'expired' WHERE authorization_id = ?;", (authorization_id,))
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
                                          amount, currency, note, visibility, request_id, settlement_id, authorization_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?);
                """, (pid, auth["from_user_id"], auth["from_handle"], auth["to_user_id"], auth["to_handle"],
                      capture_amt, currency, auth["note"], auth["visibility"], authorization_id, now))

                new_captured = auth["captured_amount"] + capture_amt
                existing_pids = json.loads(auth["payment_ids"]) if auth["payment_ids"] else []
                new_pids = existing_pids + [pid]

                new_status = "captured" if (final or capture_amt == remaining) else "open"
                conn.execute("""
                    UPDATE authorizations
                    SET captured_amount = ?, status = ?, payment_id = ?, payment_ids = ?
                    WHERE authorization_id = ?;
                """, (new_captured, new_status, pid, json.dumps(new_pids), authorization_id))

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

                conn.execute("UPDATE authorizations SET status = 'voided' WHERE authorization_id = ?;", (authorization_id,))
                conn.execute("COMMIT;")
                res = dict(auth)
                res["status"] = "voided"
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
