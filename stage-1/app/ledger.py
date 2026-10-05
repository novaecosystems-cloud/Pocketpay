"""
Pocketful Ledger & State Engine.
Implements double-entry ledger invariants, SQLite WAL mode, and atomic state transitions.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
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
            db_path = os.getenv("POCKETFUL_DB_PATH", "pocketful_stage1.db")
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
                # Default system metadata
                cur = conn.execute("SELECT value FROM meta WHERE key = 'currency';").fetchone()
                if not cur:
                    conn.execute("INSERT INTO meta (key, value) VALUES ('currency', 'EUR');")
                    conn.execute("INSERT INTO meta (key, value) VALUES ('minor_units', '2');")
                    conn.execute("INSERT INTO meta (key, value) VALUES ('settlement_operators', '[]');")
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
                    "settlement_operator_ids": json.loads(data.get("settlement_operators", "[]"))
                }
            finally:
                conn.close()

    def reset(self, fixture: Dict[str, Any]) -> None:
        """Atomically replaces service state with the supplied fixture per §3.3 & §4."""
        with self._lock:
            users_list = fixture.get("users", [])
            for u in users_list:
                bal = u.get("balance", 0)
                if not isinstance(bal, int) or bal < 0:
                    raise PocketfulError(422, "validation_failed", "Balance cannot be negative")

            currency = str(fixture.get("currency", "EUR"))
            minor_units = fixture.get("minor_units")
            if minor_units is None:
                minor_units = 2 if currency == "EUR" else (0 if currency == "JPY" else 3)
            minor_units = int(minor_units)
            operators = fixture.get("settlement_operator_ids", [])

            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute("DELETE FROM idempotency;")
                conn.execute("DELETE FROM splits;")
                conn.execute("DELETE FROM requests;")
                conn.execute("DELETE FROM payments;")
                conn.execute("DELETE FROM sessions;")
                conn.execute("DELETE FROM users;")
                conn.execute("DELETE FROM meta;")

                conn.execute("INSERT INTO meta (key, value) VALUES ('currency', ?);", (currency,))
                conn.execute("INSERT INTO meta (key, value) VALUES ('minor_units', ?);", (str(minor_units),))
                conn.execute("INSERT INTO meta (key, value) VALUES ('settlement_operators', ?);", (json.dumps(operators),))

                for u in users_list:
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
                    cat = p.get("created_at", utc_now_iso())

                    f_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (fid,)).fetchone()["handle"]
                    t_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (tid,)).fetchone()["handle"]

                    conn.execute("""
                        INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                              amount, currency, note, visibility, request_id, settlement_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (pid, fid, f_handle, tid, t_handle, amt, currency, note, vis, rid, sid, cat))

                for r in fixture.get("requests", []):
                    rqid = r["id"]
                    req_id = r["requester_id"]
                    payer_id = r["payer_id"]
                    amt = int(r["amount"])
                    note = str(r.get("note", ""))
                    status = str(r.get("status", "pending"))
                    pmt_id = r.get("payment_id")
                    cat = r.get("created_at", utc_now_iso())

                    req_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (req_id,)).fetchone()["handle"]
                    payer_handle = conn.execute("SELECT handle FROM users WHERE id = ?;", (payer_id,)).fetchone()["handle"]

                    conn.execute("""
                        INSERT INTO requests (request_id, requester_id, requester_handle, payer_id, payer_handle,
                                             amount, currency, note, status, payment_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (rqid, req_id, req_handle, payer_id, payer_handle, amt, currency, note, status, pmt_id, cat))

                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def export_state(self) -> Dict[str, Any]:
        """Produces a complete, read-only snapshot per §10."""
        with self._lock:
            conn = self._get_connection()
            try:
                meta = self.get_meta()
                users = [dict(row) for row in conn.execute("SELECT * FROM users;").fetchall()]
                sessions = [dict(row) for row in conn.execute("SELECT * FROM sessions;").fetchall()]
                payments = [dict(row) for row in conn.execute("SELECT * FROM payments;").fetchall()]
                requests = [dict(row) for row in conn.execute("SELECT * FROM requests;").fetchall()]
                splits = [dict(row) for row in conn.execute("SELECT * FROM splits;").fetchall()]
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
            raise PocketfulError(422, "validation_failed", "Missing state object")

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute("DELETE FROM idempotency;")
                conn.execute("DELETE FROM splits;")
                conn.execute("DELETE FROM requests;")
                conn.execute("DELETE FROM payments;")
                conn.execute("DELETE FROM sessions;")
                conn.execute("DELETE FROM users;")
                conn.execute("DELETE FROM meta;")

                meta = state.get("meta", {})
                conn.execute("INSERT INTO meta (key, value) VALUES ('currency', ?);", (meta.get("currency", "EUR"),))
                conn.execute("INSERT INTO meta (key, value) VALUES ('minor_units', ?);", (str(meta.get("minor_units", 2)),))
                conn.execute("INSERT INTO meta (key, value) VALUES ('settlement_operators', ?);", (json.dumps(meta.get("settlement_operator_ids", [])),))

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
                                              amount, currency, note, visibility, request_id, settlement_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (p["payment_id"], p["from_user_id"], p["from_handle"], p["to_user_id"], p["to_handle"],
                          p["amount"], p["currency"], p["note"], p["visibility"], p.get("request_id"), p.get("settlement_id"), p["created_at"]))

                for r in state.get("requests", []):
                    conn.execute("""
                        INSERT INTO requests (request_id, requester_id, requester_handle, payer_id, payer_handle,
                                             amount, currency, note, status, payment_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (r["request_id"], r["requester_id"], r["requester_handle"], r["payer_id"], r["payer_handle"],
                          r["amount"], r["currency"], r["note"], r["status"], r.get("payment_id"), r["created_at"]))

                for sp in state.get("splits", []):
                    conn.execute("""
                        INSERT INTO splits (split_id, amount, currency, note, shares_json, requests_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                    """, (sp["split_id"], sp["amount"], sp["currency"], sp["note"], sp["shares_json"], sp["requests_json"], sp["created_at"]))

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
        """Registers new user per §6."""
        if not email or "@" not in email:
            raise PocketfulError(422, "validation_failed", "Invalid email format")
        if not password or len(password) < 8:
            raise PocketfulError(422, "validation_failed", "Password must be at least 8 characters")
        if not display_name:
            raise PocketfulError(422, "validation_failed", "Display name is required")

        handle = derive_handle_from_email(email)

        with self._lock:
            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # Check email taken
                if conn.execute("SELECT id FROM users WHERE email = ?;", (email,)).fetchone():
                    raise PocketfulError(409, "email_taken", "Email is already registered")
                # Check handle taken
                if conn.execute("SELECT id FROM users WHERE handle = ?;", (handle,)).fetchone():
                    raise PocketfulError(409, "handle_taken", f"Handle '{handle}' is already taken")

                user_id = f"u_{uuid.uuid4().hex[:12]}"
                pwd_hash = hash_password(password)
                conn.execute("""
                    INSERT INTO users (id, email, password_hash, display_name, handle, balance)
                    VALUES (?, ?, ?, ?, ?, 0);
                """, (user_id, email, pwd_hash, display_name, handle))

                token = uuid.uuid4().hex + uuid.uuid4().hex
                conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?);", (token, user_id))
                conn.execute("COMMIT;")

                return {
                    "user_id": user_id,
                    "display_name": display_name,
                    "token": token
                }, token
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def login(self, email: str, password: str) -> Tuple[Dict[str, Any], str]:
        """Authenticates user per §6."""
        if not email or not password:
            raise PocketfulError(401, "unauthenticated", "Email and password are required")

        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM users WHERE email = ?;", (email,)).fetchone()
                if not row or not verify_password(password, row["password_hash"]):
                    raise PocketfulError(401, "unauthenticated", "Invalid email or password")

                token = uuid.uuid4().hex + uuid.uuid4().hex
                conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?);", (token, row["id"]))
                return {
                    "user_id": row["id"],
                    "display_name": row["display_name"],
                    "token": token
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
                # Replay returns 200 with identical body
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
        """Executes atomic P2P transfer per §8."""
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
                # Find recipient
                recip = conn.execute("SELECT * FROM users WHERE handle = ?;", (to_handle,)).fetchone()
                if not recip:
                    raise PocketfulError(404, "not_found", f"User with handle '{to_handle}' not found")

                # Check sender balance
                sender = conn.execute("SELECT balance FROM users WHERE id = ?;", (caller["id"],)).fetchone()
                if sender["balance"] < amount:
                    raise PocketfulError(409, "insufficient_funds", "Insufficient wallet balance")

                # Double-entry balance transfer with sum(delta) = 0
                conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?;", (amount, caller["id"]))
                conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (amount, recip["id"]))

                conn.execute("""
                    INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                          amount, currency, note, visibility, request_id, settlement_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
        """Fulfills a pending request per §8."""
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
                    raise PocketfulError(403, "forbidden", "Only the payer can pay this request")

                if rq["status"] != "pending":
                    raise PocketfulError(409, "request_not_pending", f"Request is already {rq['status']}")

                amount = rq["amount"]
                payer = conn.execute("SELECT balance FROM users WHERE id = ?;", (caller["id"],)).fetchone()
                if payer["balance"] < amount:
                    raise PocketfulError(409, "insufficient_funds", "Insufficient funds to pay request")

                # Debit payer, credit requester
                conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?;", (amount, caller["id"]))
                conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (amount, rq["requester_id"]))

                # Create payment record
                conn.execute("""
                    INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                          amount, currency, note, visibility, request_id, settlement_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (pid, caller["id"], caller["handle"], rq["requester_id"], rq["requester_handle"],
                      amount, currency, rq["note"], visibility, request_id, None, now))

                # Update request status to paid
                conn.execute("""
                    UPDATE requests SET status = 'paid', payment_id = ? WHERE request_id = ?;
                """, (pid, request_id))

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
                    "created_at": now
                }
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()

    def decline_request(self, caller: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        """Declines a request per §8."""
        with self._lock:
            conn = self._get_connection()
            try:
                rq = conn.execute("SELECT * FROM requests WHERE request_id = ?;", (request_id,)).fetchone()
                if not rq:
                    raise PocketfulError(404, "not_found", "Request not found")
                if rq["payer_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only the payer can decline this request")
                if rq["status"] == "declined":
                    return dict(rq)
                if rq["status"] in ("paid", "cancelled"):
                    raise PocketfulError(409, "request_not_pending", f"Cannot decline {rq['status']} request")

                conn.execute("UPDATE requests SET status = 'declined' WHERE request_id = ?;", (request_id,))
                updated = conn.execute("SELECT * FROM requests WHERE request_id = ?;", (request_id,)).fetchone()
                return dict(updated)
            finally:
                conn.close()

    def cancel_request(self, caller: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        """Cancels a request per §8."""
        with self._lock:
            conn = self._get_connection()
            try:
                rq = conn.execute("SELECT * FROM requests WHERE request_id = ?;", (request_id,)).fetchone()
                if not rq:
                    raise PocketfulError(404, "not_found", "Request not found")
                if rq["requester_id"] != caller["id"]:
                    raise PocketfulError(403, "forbidden", "Only the requester can cancel this request")
                if rq["status"] == "cancelled":
                    return dict(rq)
                if rq["status"] in ("paid", "declined"):
                    raise PocketfulError(409, "request_not_pending", f"Cannot cancel {rq['status']} request")

                conn.execute("UPDATE requests SET status = 'cancelled' WHERE request_id = ?;", (request_id,))
                updated = conn.execute("SELECT * FROM requests WHERE request_id = ?;", (request_id,)).fetchone()
                return dict(updated)
            finally:
                conn.close()

    def list_requests(self, caller: Dict[str, Any], direction: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], bool]:
        """Queries requests involving the caller per §8."""
        if limit < 1 or limit > 200 or offset < 0:
            raise PocketfulError(422, "validation_failed", "Invalid pagination limit or offset")
        if direction and direction not in ("incoming", "outgoing"):
            raise PocketfulError(422, "validation_failed", "Invalid direction")
        if status and status not in ("pending", "paid", "declined", "cancelled"):
            raise PocketfulError(422, "validation_failed", "Invalid status")

        with self._lock:
            conn = self._get_connection()
            try:
                query = "SELECT * FROM requests WHERE "
                conditions = []
                params: List[Any] = []

                if direction == "incoming":
                    conditions.append("payer_id = ?")
                    params.append(caller["id"])
                elif direction == "outgoing":
                    conditions.append("requester_id = ?")
                    params.append(caller["id"])
                else:
                    conditions.append("(payer_id = ? OR requester_id = ?)")
                    params.extend([caller["id"], caller["id"]])

                if status:
                    conditions.append("status = ?")
                    params.append(status)

                query += " AND ".join(conditions) + " ORDER BY created_at DESC, request_id DESC LIMIT ? OFFSET ?;"
                # Read limit + 1 to check has_more
                params.extend([limit + 1, offset])

                rows = conn.execute(query, params).fetchall()
                has_more = len(rows) > limit
                result = [dict(r) for r in rows[:limit]]
                return result, has_more
            finally:
                conn.close()

    def create_split(self, caller: Dict[str, Any], amount: Any, participant_handles: List[str], note: str = "") -> Dict[str, Any]:
        """Splits an amount and creates requests per §8 & §9."""
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
                # Validate all participant handles exist
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
                    # Create request for every participant EXCEPT caller
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
        """Queries payments visible to caller per §4 feed contract."""
        if limit < 1 or limit > 200 or offset < 0:
            raise PocketfulError(422, "validation_failed", "Invalid pagination limit or offset")

        with self._lock:
            conn = self._get_connection()
            try:
                # Visible if visibility == 'public' OR caller is sender OR caller is receiver
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
        """Executes atomic multi-party batch transfers per §11."""
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
                # 1. Validate transfer syntax and participants
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

                # 2. Check affordability: each wallet's final balance must be >= 0
                for uid, delta in net_deltas.items():
                    current_bal = conn.execute("SELECT balance FROM users WHERE id = ?;", (uid,)).fetchone()["balance"]
                    if current_bal + delta < 0:
                        raise PocketfulError(409, "insufficient_funds", "Settlement leaves one or more wallets with negative balance")

                # 3. Apply balance changes atomically
                for uid, delta in net_deltas.items():
                    conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?;", (delta, uid))

                # 4. Insert payment records
                created_payments = []
                for item in validated_transfers:
                    pid = f"p_{uuid.uuid4().hex[:12]}"
                    conn.execute("""
                        INSERT INTO payments (payment_id, from_user_id, from_handle, to_user_id, to_handle,
                                              amount, currency, note, visibility, request_id, settlement_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
