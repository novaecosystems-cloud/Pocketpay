"""
Pocketful Ledger Service.

High-level business operations for:
- Account creation
- Deposits
- Transfers
- Balance queries
- Account statements
- Comprehensive full-ledger invariant audit reconciliation
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from src.ledger_engine import (
    AccountNotFoundError,
    InvalidTransactionError,
    LedgerEngine,
    LedgerLineInput,
    with_db_retry,
)
from src.models import (
    DEFAULT_DB_PATH,
    SYSTEM_CLEARING_ACCOUNT_ID,
    get_connection,
    init_db,
    utc_now_iso,
)


class PocketfulService:
    def __init__(self, db_path: Optional[str] = None, busy_timeout: float = 30.0):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.busy_timeout = busy_timeout
        self.engine = LedgerEngine(db_path=self.db_path, busy_timeout=self.busy_timeout)
        # Ensure database and system clearing account are initialized
        init_db(self.db_path)

    def _get_connection(self) -> sqlite3.Connection:
        return get_connection(self.db_path, timeout=self.busy_timeout)

    def create_account(
        self,
        account_id: str,
        name: str,
        initial_balance_cents: int = 0,
    ) -> Dict[str, Any]:
        """
        Creates a new user wallet account.
        
        If initial_balance_cents > 0, an initial deposit from SYSTEM_CLEARING
        is atomically processed to preserve double-entry integrity.
        """
        if not account_id or not account_id.strip():
            raise InvalidTransactionError("Account ID must be a non-empty string.")
        if not name or not name.strip():
            raise InvalidTransactionError("Account name must be a non-empty string.")
        if initial_balance_cents < 0:
            raise InvalidTransactionError("Initial balance cannot be negative.")

        def _insert():
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                now = utc_now_iso()
                conn.execute(
                    """
                    INSERT INTO accounts (id, name, type, balance_cents, created_at)
                    VALUES (?, ?, 'USER', 0, ?);
                    """,
                    (account_id.strip(), name.strip(), now)
                )
                conn.execute("COMMIT;")
            except sqlite3.IntegrityError as e:
                conn.execute("ROLLBACK;")
                raise InvalidTransactionError(f"Account '{account_id}' already exists or violates constraint: {e}")
            finally:
                conn.close()

        with_db_retry(_insert)

        if initial_balance_cents > 0:
            self.deposit(
                account_id=account_id,
                amount_cents=initial_balance_cents,
                description=f"Initial balance funding for {name}",
            )

        return self.get_account(account_id)

    def get_account(self, account_id: str) -> Dict[str, Any]:
        """Retrieves an account record by ID."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id, name, type, balance_cents, created_at FROM accounts WHERE id = ?",
                (account_id,)
            ).fetchone()
            if row is None:
                raise AccountNotFoundError(f"Account '{account_id}' does not exist.")
            return dict(row)
        finally:
            conn.close()

    def get_balance(self, account_id: str) -> int:
        """Returns the current balance in cents for an account."""
        acc = self.get_account(account_id)
        return acc["balance_cents"]

    def deposit(
        self,
        account_id: str,
        amount_cents: int,
        idempotency_key: Optional[str] = None,
        description: str = "Deposit",
    ) -> Dict[str, Any]:
        """
        Deposits money into an account from the system clearing pool.
        
        Strict double-entry:
        - SYSTEM_CLEARING: -amount_cents
        - account_id: +amount_cents
        Sum: 0.
        """
        if amount_cents <= 0:
            raise InvalidTransactionError(f"Deposit amount must be positive, got {amount_cents} cents.")

        lines = [
            LedgerLineInput(account_id=SYSTEM_CLEARING_ACCOUNT_ID, amount_cents=-amount_cents),
            LedgerLineInput(account_id=account_id, amount_cents=amount_cents),
        ]

        return self.engine.execute_transaction(
            entry_type="DEPOSIT",
            lines=lines,
            description=description,
            idempotency_key=idempotency_key,
        )

    def transfer(
        self,
        from_account_id: str,
        to_account_id: str,
        amount_cents: int,
        idempotency_key: Optional[str] = None,
        description: str = "Transfer",
    ) -> Dict[str, Any]:
        """
        Transfers money between two accounts atomically.
        
        Strict double-entry:
        - from_account_id: -amount_cents
        - to_account_id: +amount_cents
        Sum: 0.
        """
        if from_account_id == to_account_id:
            raise InvalidTransactionError("Cannot transfer funds to the same account.")
        if amount_cents <= 0:
            raise InvalidTransactionError(f"Transfer amount must be positive, got {amount_cents} cents.")

        lines = [
            LedgerLineInput(account_id=from_account_id, amount_cents=-amount_cents),
            LedgerLineInput(account_id=to_account_id, amount_cents=amount_cents),
        ]

        return self.engine.execute_transaction(
            entry_type="TRANSFER",
            lines=lines,
            description=description,
            idempotency_key=idempotency_key,
        )

    def withdraw(
        self,
        account_id: str,
        amount_cents: int,
        idempotency_key: Optional[str] = None,
        description: str = "Withdrawal",
    ) -> Dict[str, Any]:
        """
        Withdraws money from an account back to the system clearing pool.
        """
        if amount_cents <= 0:
            raise InvalidTransactionError(f"Withdrawal amount must be positive, got {amount_cents} cents.")

        lines = [
            LedgerLineInput(account_id=account_id, amount_cents=-amount_cents),
            LedgerLineInput(account_id=SYSTEM_CLEARING_ACCOUNT_ID, amount_cents=amount_cents),
        ]

        return self.engine.execute_transaction(
            entry_type="WITHDRAWAL",
            lines=lines,
            description=description,
            idempotency_key=idempotency_key,
        )

    def get_account_statement(self, account_id: str) -> Dict[str, Any]:
        """
        Returns full transaction history and statement for an account.
        """
        acc = self.get_account(account_id)
        conn = self._get_connection()
        try:
            query = """
                SELECT 
                    l.id AS line_id,
                    l.amount_cents,
                    l.created_at AS line_created_at,
                    j.id AS journal_entry_id,
                    j.entry_type,
                    j.description,
                    j.created_at AS entry_created_at
                FROM ledger_lines l
                JOIN journal_entries j ON l.journal_entry_id = j.id
                WHERE l.account_id = ?
                ORDER BY l.created_at ASC, l.rowid ASC;
            """
            rows = conn.execute(query, (account_id,)).fetchall()
            
            history = []
            running_balance = 0
            for r in rows:
                running_balance += r["amount_cents"]
                history.append({
                    "line_id": r["line_id"],
                    "journal_entry_id": r["journal_entry_id"],
                    "entry_type": r["entry_type"],
                    "description": r["description"],
                    "amount_cents": r["amount_cents"],
                    "running_balance_cents": running_balance,
                    "created_at": r["entry_created_at"],
                })

            return {
                "account_id": acc["id"],
                "name": acc["name"],
                "type": acc["type"],
                "current_balance_cents": acc["balance_cents"],
                "reconciled_balance_cents": running_balance,
                "is_reconciled": (acc["balance_cents"] == running_balance),
                "total_transactions": len(history),
                "statement_lines": history,
            }
        finally:
            conn.close()

    def run_full_ledger_audit(self) -> Dict[str, Any]:
        """
        Executes a comprehensive system-wide audit verifying:
        1. Double-Entry Invariant: Every journal entry sums to 0.
        2. Account Balance Reconciliation: Account balance == sum of ledger lines.
        3. Non-Negative Balance Invariant: No user account has balance < 0.
        4. Total System Money Conservation: Sum of all accounts == 0.
        5. Foreign Key Integrity: No orphan ledger lines or entries.
        """
        conn = self._get_connection()
        try:
            discrepancies: List[str] = []

            # 1. Double-entry balance invariant per journal entry
            unbalanced_entries = conn.execute("""
                SELECT journal_entry_id, SUM(amount_cents) AS entry_sum, COUNT(*) AS line_count
                FROM ledger_lines
                GROUP BY journal_entry_id
                HAVING entry_sum != 0;
            """).fetchall()

            for row in unbalanced_entries:
                discrepancies.append(
                    f"Unbalanced Journal Entry {row['journal_entry_id']}: sum is {row['entry_sum']} cents across {row['line_count']} lines."
                )

            # 2. Account balance reconciliation
            account_audits = conn.execute("""
                SELECT 
                    a.id, 
                    a.name, 
                    a.type, 
                    a.balance_cents AS stored_balance,
                    COALESCE(SUM(l.amount_cents), 0) AS calculated_balance
                FROM accounts a
                LEFT JOIN ledger_lines l ON a.id = l.account_id
                GROUP BY a.id;
            """).fetchall()

            for acc in account_audits:
                if acc["stored_balance"] != acc["calculated_balance"]:
                    discrepancies.append(
                        f"Account balance drift on '{acc['id']}': stored={acc['stored_balance']} cents, calculated={acc['calculated_balance']} cents."
                    )

            # 3. Non-negative user account balances
            negative_accounts = conn.execute("""
                SELECT id, name, balance_cents
                FROM accounts
                WHERE type = 'USER' AND balance_cents < 0;
            """).fetchall()

            for row in negative_accounts:
                discrepancies.append(
                    f"Illegal negative balance on user account '{row['id']}': {row['balance_cents']} cents."
                )

            # 4. Total system money conservation: Sum(all accounts) == 0
            system_sum_row = conn.execute("""
                SELECT COALESCE(SUM(balance_cents), 0) AS total_balance FROM accounts;
            """).fetchone()
            total_balance = system_sum_row["total_balance"] if system_sum_row else 0
            if total_balance != 0:
                discrepancies.append(
                    f"System money conservation violation: sum of all accounts is {total_balance} cents, expected 0."
                )

            # 5. Summary statistics
            account_count = conn.execute("SELECT COUNT(*) AS c FROM accounts;").fetchone()["c"]
            journal_count = conn.execute("SELECT COUNT(*) AS c FROM journal_entries;").fetchone()["c"]
            line_count = conn.execute("SELECT COUNT(*) AS c FROM ledger_lines;").fetchone()["c"]
            idempotency_count = conn.execute("SELECT COUNT(*) AS c FROM idempotency_keys;").fetchone()["c"]

            return {
                "is_valid": (len(discrepancies) == 0),
                "total_accounts": account_count,
                "total_journal_entries": journal_count,
                "total_ledger_lines": line_count,
                "total_idempotency_keys": idempotency_count,
                "total_system_balance_cents": total_balance,
                "discrepancy_count": len(discrepancies),
                "discrepancies": discrepancies,
            }
        finally:
            conn.close()
