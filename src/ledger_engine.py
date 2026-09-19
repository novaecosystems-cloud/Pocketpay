"""
Pocketful Ledger Engine.

Core transaction execution engine implementing:
- Deterministic lock ordering (min/max account IDs sorted)
- Strict double-entry balance invariant enforcement (sum(ledger_lines) == 0)
- Atomic idempotency lifecycle management (IN_FLIGHT, COMPLETED, FAILED)
- Concurrency retry harness for SQLite WAL mode
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.models import (
    get_connection,
    utc_now_iso,
)


class LedgerError(Exception):
    """Base exception for all ledger operations."""
    pass


class InsufficientFundsError(LedgerError):
    """Raised when an account does not have sufficient balance for a debit."""
    pass


class UnbalancedLedgerEntryError(LedgerError):
    """Raised when the sum of ledger lines does not equal zero."""
    pass


class AccountNotFoundError(LedgerError):
    """Raised when a referenced account does not exist."""
    pass


class IdempotencyConflictError(LedgerError):
    """Raised when an idempotency key conflict occurs or an in-flight operation times out."""
    pass


class InvalidTransactionError(LedgerError):
    """Raised when transaction parameters are logically invalid."""
    pass


@dataclass(frozen=True)
class LedgerLineInput:
    account_id: str
    amount_cents: int


@dataclass(frozen=True)
class BatchTransferInput:
    from_account_id: str
    to_account_id: str
    amount_cents: int
    description: Optional[str] = None
    idempotency_key: Optional[str] = None


def with_db_retry(
    fn: Callable[..., Any],
    max_retries: int = 20,
    base_delay: float = 0.01,
    max_delay: float = 0.25,
) -> Any:
    """
    Executes a callable with exponential backoff and jitter for SQLite lock contention.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except sqlite3.OperationalError as e:
            err_msg = str(e).lower()
            if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries:
                attempt += 1
                sleep_time = min(max_delay, base_delay * (2 ** (attempt - 1)))
                sleep_time *= (0.75 + 0.5 * random.random())
                time.sleep(sleep_time)
            else:
                raise


class LedgerEngine:
    def __init__(self, db_path: Optional[str] = None, busy_timeout: float = 30.0):
        self.db_path = db_path
        self.busy_timeout = busy_timeout

    def _get_connection(self) -> sqlite3.Connection:
        return get_connection(self.db_path, timeout=self.busy_timeout)

    def execute_transaction(
        self,
        entry_type: str,
        lines: List[LedgerLineInput],
        description: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """
        Executes an atomic double-entry transaction.
        
        Guarantees:
        1. sum(lines.amount_cents) == 0.
        2. Deterministic lock order of accounts to prevent deadlocks.
        3. All user accounts retain balance_cents >= 0.
        4. Atomic idempotency processing (IN_FLIGHT -> COMPLETED or FAILED).
        """
        if not lines:
            raise InvalidTransactionError("Transaction must contain at least one ledger line.")

        # 1. Verify Double-Entry Balance Invariant upfront
        total_sum = sum(line.amount_cents for line in lines)
        if total_sum != 0:
            raise UnbalancedLedgerEntryError(
                f"Double-entry balance violation: sum of lines is {total_sum} cents, must be 0."
            )

        # Helper that does the actual work on a connection
        def _attempt():
            c = conn if conn is not None else self._get_connection()
            try:
                return self._execute_on_conn(
                    conn=c,
                    entry_type=entry_type,
                    lines=lines,
                    description=description,
                    idempotency_key=idempotency_key,
                )
            finally:
                if conn is None:
                    c.close()

        # Handle idempotency polling if another concurrent thread is currently IN_FLIGHT
        if idempotency_key:
            return self._execute_with_idempotency_coordination(_attempt, idempotency_key, conn=conn)
        else:
            return with_db_retry(_attempt)

    def _execute_with_idempotency_coordination(
        self,
        attempt_fn: Callable[[], Dict[str, Any]],
        idempotency_key: str,
        poll_timeout: float = 15.0,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """
        Coordinates concurrent executions sharing the same idempotency key.
        If IN_FLIGHT, polls until COMPLETED or timeout.
        """
        deadline = time.time() + poll_timeout
        while time.time() < deadline:
            try:
                return with_db_retry(attempt_fn)
            except IdempotencyConflictError:
                # Key is currently in-flight by another thread; wait and poll
                time.sleep(0.02 + 0.03 * random.random())
                cached = self._get_completed_idempotency(idempotency_key, conn=conn)
                if cached is not None:
                    return cached
                continue

        # Timed out waiting for in-flight request
        cached = self._get_completed_idempotency(idempotency_key, conn=conn)
        if cached is not None:
            return cached
        raise IdempotencyConflictError(
            f"Timed out waiting for concurrent in-flight request with idempotency key: {idempotency_key}"
        )

    def _get_completed_idempotency(
        self,
        idempotency_key: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Optional[Dict[str, Any]]:
        """Checks if an idempotency key has completed and returns its response."""
        c = conn if conn is not None else self._get_connection()
        try:
            row = c.execute(
                "SELECT status, response_json FROM idempotency_keys WHERE key = ?",
                (idempotency_key,)
            ).fetchone()
            if row:
                if row["status"] == "COMPLETED":
                    return json.loads(row["response_json"])
                elif row["status"] == "FAILED":
                    raise LedgerError(f"Previous request with idempotency key '{idempotency_key}' failed.")
            return None
        finally:
            if conn is None:
                c.close()

    def _execute_on_conn(
        self,
        conn: sqlite3.Connection,
        entry_type: str,
        lines: List[LedgerLineInput],
        description: Optional[str],
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        """Executes a single transaction attempt on a connection."""
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # 1. Idempotency Key Handling
            if idempotency_key:
                row = conn.execute(
                    "SELECT status, response_json FROM idempotency_keys WHERE key = ?",
                    (idempotency_key,)
                ).fetchone()

                if row is not None:
                    status = row["status"]
                    if status == "COMPLETED":
                        conn.execute("COMMIT;")
                        return json.loads(row["response_json"])
                    elif status == "IN_FLIGHT":
                        conn.execute("ROLLBACK;")
                        raise IdempotencyConflictError(f"Idempotency key '{idempotency_key}' is currently IN_FLIGHT.")
                    else:  # FAILED
                        conn.execute("ROLLBACK;")
                        raise LedgerError(f"Previous transaction with idempotency key '{idempotency_key}' failed.")

                # Register key as IN_FLIGHT atomically
                now_str = utc_now_iso()
                conn.execute(
                    """
                    INSERT INTO idempotency_keys (key, status, created_at, updated_at)
                    VALUES (?, 'IN_FLIGHT', ?, ?);
                    """,
                    (idempotency_key, now_str, now_str)
                )

            # 2. Deterministic Lock Ordering
            # Aggregate net balance changes per account and sort account IDs lexicographically.
            # Sorting prevents circular wait conditions and database deadlocks.
            net_by_account: Dict[str, int] = {}
            for line in lines:
                net_by_account[line.account_id] = net_by_account.get(line.account_id, 0) + line.amount_cents

            ordered_account_ids = sorted(net_by_account.keys())

            # 3. Read and lock accounts in deterministic order
            account_rows: Dict[str, sqlite3.Row] = {}
            for acc_id in ordered_account_ids:
                row = conn.execute(
                    "SELECT id, name, type, balance_cents, pending_debit_cents, pending_credit_cents FROM accounts WHERE id = ?",
                    (acc_id,)
                ).fetchone()
                if row is None:
                    self._fail_idempotency(conn, idempotency_key, f"Account '{acc_id}' not found.")
                    conn.execute("COMMIT;")
                    raise AccountNotFoundError(f"Account '{acc_id}' does not exist.")
                account_rows[acc_id] = row

            # 4. Invariant Check: Verify non-negative available balance for all USER accounts
            for acc_id in ordered_account_ids:
                acc = account_rows[acc_id]
                net_change = net_by_account[acc_id]
                new_balance = acc["balance_cents"] + net_change
                pending_debits = acc["pending_debit_cents"] if "pending_debit_cents" in acc.keys() else 0
                if acc["type"] == "USER" and (new_balance - pending_debits) < 0:
                    err_msg = (
                        f"Insufficient funds for account '{acc_id}' ({acc['name']}): "
                        f"current balance = {acc['balance_cents']} cents, held = {pending_debits} cents, "
                        f"available = {acc['balance_cents'] - pending_debits} cents, change = {net_change} cents."
                    )
                    self._fail_idempotency(conn, idempotency_key, err_msg)
                    conn.execute("COMMIT;")
                    raise InsufficientFundsError(err_msg)

            # 5. Apply balance updates in deterministic order
            for acc_id in ordered_account_ids:
                net_change = net_by_account[acc_id]
                conn.execute(
                    "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
                    (net_change, acc_id)
                )

            # 6. Insert Journal Entry
            journal_entry_id = f"entry_{uuid.uuid4().hex}"
            now_iso = utc_now_iso()
            conn.execute(
                """
                INSERT INTO journal_entries (id, entry_type, description, created_at)
                VALUES (?, ?, ?, ?);
                """,
                (journal_entry_id, entry_type, description or "", now_iso)
            )

            # 7. Insert Ledger Lines
            ledger_line_results = []
            for line in lines:
                line_id = f"line_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO ledger_lines (id, journal_entry_id, account_id, amount_cents, created_at)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (line_id, journal_entry_id, line.account_id, line.amount_cents, now_iso)
                )
                ledger_line_results.append({
                    "id": line_id,
                    "account_id": line.account_id,
                    "amount_cents": line.amount_cents,
                })

            # 8. Prepare result payload
            result = {
                "journal_entry_id": journal_entry_id,
                "entry_type": entry_type,
                "description": description or "",
                "created_at": now_iso,
                "lines": ledger_line_results,
                "idempotency_key": idempotency_key,
                "status": "COMPLETED",
            }

            # 9. Mark Idempotency Key as COMPLETED
            if idempotency_key:
                conn.execute(
                    """
                    UPDATE idempotency_keys
                    SET status = 'COMPLETED',
                        journal_entry_id = ?,
                        response_json = ?,
                        updated_at = ?
                    WHERE key = ?;
                    """,
                    (journal_entry_id, json.dumps(result), utc_now_iso(), idempotency_key)
                )

            conn.execute("COMMIT;")
            return result

        except (InsufficientFundsError, AccountNotFoundError, UnbalancedLedgerEntryError, IdempotencyConflictError):
            # Known domain validation errors where transaction was cleanly committed or rolled back
            raise
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise

    def _fail_idempotency(self, conn: sqlite3.Connection, idempotency_key: Optional[str], reason: str) -> None:
        """Marks an idempotency key as FAILED inside the current active transaction."""
        if idempotency_key:
            err_json = json.dumps({"error": reason, "status": "FAILED"})
            conn.execute(
                """
                UPDATE idempotency_keys
                SET status = 'FAILED',
                    response_json = ?,
                    updated_at = ?
                WHERE key = ?;
                """,
                (err_json, utc_now_iso(), idempotency_key)
            )

    def execute_batch_transactions(
        self,
        transfers: List[BatchTransferInput],
        description: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """
        TigerBeetle-inspired Vectorized Group-Commit Batch Execution.
        
        Executes N transfers atomically within a single database transaction.
        Enforces:
        - Global deterministic account sorting to eliminate deadlocks across batch.
        - Balance invariants: ensures available balance (balance - pending_debit) >= 0.
        - Vectorized bulk updates for accounts and bulk inserts for journal & lines.
        - All-or-nothing atomicity: if any transfer fails, entire batch rolls back.
        """
        if not transfers:
            raise InvalidTransactionError("Batch must contain at least one transfer.")

        def _attempt():
            c = conn if conn is not None else self._get_connection()
            try:
                return self._execute_batch_on_conn(
                    conn=c,
                    transfers=transfers,
                    description=description,
                )
            finally:
                if conn is None:
                    c.close()

        return with_db_retry(_attempt)

    def _execute_batch_on_conn(
        self,
        conn: sqlite3.Connection,
        transfers: List[BatchTransferInput],
        description: Optional[str],
    ) -> Dict[str, Any]:
        # Validate individual transfers
        for t in transfers:
            if t.from_account_id == t.to_account_id:
                raise InvalidTransactionError(f"Cannot transfer to self in batch: {t.from_account_id}")
            if t.amount_cents <= 0:
                raise InvalidTransactionError(f"Transfer amount must be positive, got {t.amount_cents} cents.")

        # Compute net changes per account
        net_by_account: Dict[str, int] = {}
        for t in transfers:
            net_by_account[t.from_account_id] = net_by_account.get(t.from_account_id, 0) - t.amount_cents
            net_by_account[t.to_account_id] = net_by_account.get(t.to_account_id, 0) + t.amount_cents

        ordered_account_ids = sorted(net_by_account.keys())

        conn.execute("BEGIN IMMEDIATE;")
        try:
            # Fetch all affected accounts in deterministic order
            account_rows: Dict[str, sqlite3.Row] = {}
            for acc_id in ordered_account_ids:
                row = conn.execute(
                    "SELECT id, name, type, balance_cents, pending_debit_cents FROM accounts WHERE id = ?",
                    (acc_id,)
                ).fetchone()
                if row is None:
                    raise AccountNotFoundError(f"Account '{acc_id}' in batch does not exist.")
                account_rows[acc_id] = row

            # Verify balance invariants across batch
            for acc_id in ordered_account_ids:
                acc = account_rows[acc_id]
                net_change = net_by_account[acc_id]
                new_balance = acc["balance_cents"] + net_change
                # Available balance must remain >= 0
                if acc["type"] == "USER" and (new_balance - acc["pending_debit_cents"]) < 0:
                    raise InsufficientFundsError(
                        f"Insufficient funds in batch for account '{acc_id}' ({acc['name']}): "
                        f"balance = {acc['balance_cents']} cents, pending_debits = {acc['pending_debit_cents']} cents, "
                        f"net change = {net_change} cents."
                    )

            # Apply vectorized balance updates
            update_data = [(net_by_account[acc_id], acc_id) for acc_id in ordered_account_ids]
            conn.executemany(
                "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
                update_data
            )

            # Prepare vectorized journal entries & ledger lines
            now_iso = utc_now_iso()
            journal_records = []
            ledger_records = []
            receipts = []

            for t in transfers:
                entry_id = f"entry_{uuid.uuid4().hex}"
                line1_id = f"line_{uuid.uuid4().hex}"
                line2_id = f"line_{uuid.uuid4().hex}"
                desc = t.description or description or "Batch Transfer"

                journal_records.append((entry_id, "TRANSFER", desc, now_iso))
                ledger_records.append((line1_id, entry_id, t.from_account_id, -t.amount_cents, now_iso))
                ledger_records.append((line2_id, entry_id, t.to_account_id, t.amount_cents, now_iso))

                receipts.append({
                    "journal_entry_id": entry_id,
                    "from_account_id": t.from_account_id,
                    "to_account_id": t.to_account_id,
                    "amount_cents": t.amount_cents,
                    "status": "COMPLETED",
                })

            conn.executemany(
                "INSERT INTO journal_entries (id, entry_type, description, created_at) VALUES (?, ?, ?, ?);",
                journal_records
            )
            conn.executemany(
                "INSERT INTO ledger_lines (id, journal_entry_id, account_id, amount_cents, created_at) VALUES (?, ?, ?, ?, ?);",
                ledger_records
            )

            conn.execute("COMMIT;")

            return {
                "batch_size": len(transfers),
                "total_cents": sum(t.amount_cents for t in transfers),
                "receipts": receipts,
                "status": "COMPLETED",
            }
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise

    def create_pending_transfer(
        self,
        from_account_id: str,
        to_account_id: str,
        amount_cents: int,
        timeout_seconds: Optional[int] = None,
        description: Optional[str] = None,
        transfer_id: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """
        Phase 1 of TigerBeetle Two-Phase Transfer: PENDING (Authorization / Hold).
        
        Reserves funds on sender without crediting receiver's cleared balance yet.
        Guarantees sender cannot double-spend reserved funds.
        """
        if from_account_id == to_account_id:
            raise InvalidTransactionError("Cannot create pending transfer to the same account.")
        if amount_cents <= 0:
            raise InvalidTransactionError(f"Pending transfer amount must be positive, got {amount_cents} cents.")

        def _attempt():
            c = conn if conn is not None else self._get_connection()
            try:
                ordered_ids = sorted([from_account_id, to_account_id])
                c.execute("BEGIN IMMEDIATE;")
                try:
                    account_rows: Dict[str, sqlite3.Row] = {}
                    for acc_id in ordered_ids:
                        row = c.execute(
                            "SELECT id, name, type, balance_cents, pending_debit_cents, pending_credit_cents FROM accounts WHERE id = ?",
                            (acc_id,)
                        ).fetchone()
                        if row is None:
                            raise AccountNotFoundError(f"Account '{acc_id}' does not exist.")
                        account_rows[acc_id] = row

                    sender = account_rows[from_account_id]
                    available = sender["balance_cents"] - sender["pending_debit_cents"]
                    if sender["type"] == "USER" and available < amount_cents:
                        raise InsufficientFundsError(
                            f"Insufficient available funds for hold on '{from_account_id}': "
                            f"balance = {sender['balance_cents']} cents, held = {sender['pending_debit_cents']} cents, "
                            f"available = {available} cents, requested = {amount_cents} cents."
                        )

                    c.execute(
                        "UPDATE accounts SET pending_debit_cents = pending_debit_cents + ? WHERE id = ?",
                        (amount_cents, from_account_id)
                    )
                    c.execute(
                        "UPDATE accounts SET pending_credit_cents = pending_credit_cents + ? WHERE id = ?",
                        (amount_cents, to_account_id)
                    )

                    pend_id = transfer_id or f"pend_{uuid.uuid4().hex}"
                    now_str = utc_now_iso()
                    expires_str = None
                    if timeout_seconds:
                        expires_str = datetime.fromtimestamp(time.time() + timeout_seconds, timezone.utc).isoformat()

                    c.execute(
                        """
                        INSERT INTO pending_transfers (id, from_account_id, to_account_id, amount_cents, status, description, timeout_seconds, created_at, expires_at)
                        VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, ?);
                        """,
                        (pend_id, from_account_id, to_account_id, amount_cents, description or "", timeout_seconds, now_str, expires_str)
                    )

                    c.execute("COMMIT;")

                    return {
                        "id": pend_id,
                        "from_account_id": from_account_id,
                        "to_account_id": to_account_id,
                        "amount_cents": amount_cents,
                        "status": "PENDING",
                        "description": description or "",
                        "timeout_seconds": timeout_seconds,
                        "created_at": now_str,
                        "expires_at": expires_str,
                    }
                except Exception:
                    try:
                        c.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            finally:
                if conn is None:
                    c.close()

        return with_db_retry(_attempt)

    def post_pending_transfer(
        self,
        pending_transfer_id: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """
        Phase 2a of TigerBeetle Two-Phase Transfer: POST (Capture / Settle).
        
        Releases the hold and commits the double-entry transfer into cleared balances.
        """
        def _attempt():
            c = conn if conn is not None else self._get_connection()
            try:
                c.execute("BEGIN IMMEDIATE;")
                try:
                    pend = c.execute(
                        "SELECT * FROM pending_transfers WHERE id = ?",
                        (pending_transfer_id,)
                    ).fetchone()
                    if pend is None:
                        raise InvalidTransactionError(f"Pending transfer '{pending_transfer_id}' does not exist.")
                    if pend["status"] != "PENDING":
                        raise InvalidTransactionError(
                            f"Cannot post transfer '{pending_transfer_id}' with status '{pend['status']}'."
                        )

                    from_acc = pend["from_account_id"]
                    to_acc = pend["to_account_id"]
                    amount = pend["amount_cents"]

                    ordered_ids = sorted([from_acc, to_acc])
                    for acc_id in ordered_ids:
                        c.execute("SELECT id FROM accounts WHERE id = ?", (acc_id,))

                    c.execute(
                        "UPDATE accounts SET balance_cents = balance_cents - ?, pending_debit_cents = pending_debit_cents - ? WHERE id = ?",
                        (amount, amount, from_acc)
                    )
                    c.execute(
                        "UPDATE accounts SET balance_cents = balance_cents + ?, pending_credit_cents = pending_credit_cents - ? WHERE id = ?",
                        (amount, amount, to_acc)
                    )

                    entry_id = f"entry_{uuid.uuid4().hex}"
                    now_str = utc_now_iso()
                    desc = f"Settlement of hold {pending_transfer_id}: {pend['description']}"

                    c.execute(
                        "INSERT INTO journal_entries (id, entry_type, description, created_at) VALUES (?, 'TRANSFER', ?, ?)",
                        (entry_id, desc, now_str)
                    )
                    c.execute(
                        "INSERT INTO ledger_lines (id, journal_entry_id, account_id, amount_cents, created_at) VALUES (?, ?, ?, ?, ?)",
                        (f"line_{uuid.uuid4().hex}", entry_id, from_acc, -amount, now_str)
                    )
                    c.execute(
                        "INSERT INTO ledger_lines (id, journal_entry_id, account_id, amount_cents, created_at) VALUES (?, ?, ?, ?, ?)",
                        (f"line_{uuid.uuid4().hex}", entry_id, to_acc, amount, now_str)
                    )

                    c.execute(
                        "UPDATE pending_transfers SET status = 'POSTED', completed_at = ?, journal_entry_id = ? WHERE id = ?",
                        (now_str, entry_id, pending_transfer_id)
                    )

                    c.execute("COMMIT;")

                    return {
                        "id": pending_transfer_id,
                        "journal_entry_id": entry_id,
                        "from_account_id": from_acc,
                        "to_account_id": to_acc,
                        "amount_cents": amount,
                        "status": "POSTED",
                        "completed_at": now_str,
                    }
                except Exception:
                    try:
                        c.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            finally:
                if conn is None:
                    c.close()

        return with_db_retry(_attempt)

    def void_pending_transfer(
        self,
        pending_transfer_id: str,
        reason: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """
        Phase 2b of TigerBeetle Two-Phase Transfer: VOID (Cancel / Release Hold).
        
        Releases reserved funds back to available balance without moving cleared money.
        """
        def _attempt():
            c = conn if conn is not None else self._get_connection()
            try:
                c.execute("BEGIN IMMEDIATE;")
                try:
                    pend = c.execute(
                        "SELECT * FROM pending_transfers WHERE id = ?",
                        (pending_transfer_id,)
                    ).fetchone()
                    if pend is None:
                        raise InvalidTransactionError(f"Pending transfer '{pending_transfer_id}' does not exist.")
                    if pend["status"] != "PENDING":
                        raise InvalidTransactionError(
                            f"Cannot void transfer '{pending_transfer_id}' with status '{pend['status']}'."
                        )

                    from_acc = pend["from_account_id"]
                    to_acc = pend["to_account_id"]
                    amount = pend["amount_cents"]

                    c.execute(
                        "UPDATE accounts SET pending_debit_cents = pending_debit_cents - ? WHERE id = ?",
                        (amount, from_acc)
                    )
                    c.execute(
                        "UPDATE accounts SET pending_credit_cents = pending_credit_cents - ? WHERE id = ?",
                        (amount, to_acc)
                    )

                    now_str = utc_now_iso()
                    desc_update = pend["description"] or ""
                    if reason:
                        desc_update = f"{desc_update} [VOID REASON: {reason}]".strip()

                    c.execute(
                        "UPDATE pending_transfers SET status = 'VOIDED', completed_at = ?, description = ? WHERE id = ?",
                        (now_str, desc_update, pending_transfer_id)
                    )

                    c.execute("COMMIT;")

                    return {
                        "id": pending_transfer_id,
                        "from_account_id": from_acc,
                        "to_account_id": to_acc,
                        "amount_cents": amount,
                        "status": "VOIDED",
                        "completed_at": now_str,
                        "reason": reason or "",
                    }
                except Exception:
                    try:
                        c.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            finally:
                if conn is None:
                    c.close()

        return with_db_retry(_attempt)

    def get_pending_transfer(
        self,
        pending_transfer_id: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves a pending transfer by ID."""
        c = conn if conn is not None else self._get_connection()
        try:
            row = c.execute(
                "SELECT * FROM pending_transfers WHERE id = ?",
                (pending_transfer_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            if conn is None:
                c.close()

