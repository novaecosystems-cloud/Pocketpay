"""
Unit tests for Pocketful Double-Entry Ledger.

Validates:
- Double-entry balance invariant enforcement (sum == 0)
- Overdraft prevention & SQLite CHECK constraint defense-in-depth
- Deterministic locking & transaction atomicity
- Idempotency lifecycle & duplicate request prevention
- Boundary conditions (1 cent, exact zero balance, large amounts)
- System-wide ledger audit reconciliation
"""

import os
import sqlite3
import tempfile
import unittest

from src.ledger_engine import (
    AccountNotFoundError,
    IdempotencyConflictError,
    InsufficientFundsError,
    InvalidTransactionError,
    LedgerEngine,
    LedgerError,
    LedgerLineInput,
    UnbalancedLedgerEntryError,
)
from src.models import SYSTEM_CLEARING_ACCOUNT_ID, get_connection, init_db
from src.service import PocketfulService


class TestPocketfulLedger(unittest.TestCase):
    def setUp(self):
        # Create a unique temporary database file for each test
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_pocketful.db")
        self.service = PocketfulService(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_account_zero_balance(self):
        acc = self.service.create_account("acc_alice", "Alice")
        self.assertEqual(acc["id"], "acc_alice")
        self.assertEqual(acc["name"], "Alice")
        self.assertEqual(acc["balance_cents"], 0)
        self.assertEqual(self.service.get_balance("acc_alice"), 0)

    def test_create_account_with_initial_balance(self):
        acc = self.service.create_account("acc_bob", "Bob", initial_balance_cents=5000)
        self.assertEqual(acc["balance_cents"], 5000)
        self.assertEqual(self.service.get_balance("acc_bob"), 5000)

        # Verify audit passes
        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["total_system_balance_cents"], 0)

    def test_create_account_invalid_inputs(self):
        with self.assertRaises(InvalidTransactionError):
            self.service.create_account("", "Alice")
        with self.assertRaises(InvalidTransactionError):
            self.service.create_account("acc_alice", "")
        with self.assertRaises(InvalidTransactionError):
            self.service.create_account("acc_alice", "Alice", initial_balance_cents=-100)

        # Duplicate account creation must fail
        self.service.create_account("acc_alice", "Alice")
        with self.assertRaises(InvalidTransactionError):
            self.service.create_account("acc_alice", "Alice Duplicate")

    def test_deposit_success(self):
        self.service.create_account("acc_alice", "Alice")
        result = self.service.deposit("acc_alice", 10000, description="Salary deposit")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(self.service.get_balance("acc_alice"), 10000)

        # System clearing balance should be -10000
        self.assertEqual(self.service.get_balance(SYSTEM_CLEARING_ACCOUNT_ID), -10000)

        # Double-entry invariant audit
        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["discrepancies"], [])

    def test_deposit_invalid_amount(self):
        self.service.create_account("acc_alice", "Alice")
        with self.assertRaises(InvalidTransactionError):
            self.service.deposit("acc_alice", 0)
        with self.assertRaises(InvalidTransactionError):
            self.service.deposit("acc_alice", -500)

    def test_transfer_success(self):
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=10000)
        self.service.create_account("acc_bob", "Bob", initial_balance_cents=2000)

        result = self.service.transfer("acc_alice", "acc_bob", 4000, description="Rent share")
        self.assertEqual(result["status"], "COMPLETED")

        self.assertEqual(self.service.get_balance("acc_alice"), 6000)
        self.assertEqual(self.service.get_balance("acc_bob"), 6000)

        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])

    def test_transfer_insufficient_funds(self):
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=3000)
        self.service.create_account("acc_bob", "Bob", initial_balance_cents=1000)

        with self.assertRaises(InsufficientFundsError):
            self.service.transfer("acc_alice", "acc_bob", 3001)

        # Balances must be untouched
        self.assertEqual(self.service.get_balance("acc_alice"), 3000)
        self.assertEqual(self.service.get_balance("acc_bob"), 1000)

    def test_transfer_to_same_account_fails(self):
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=5000)
        with self.assertRaises(InvalidTransactionError):
            self.service.transfer("acc_alice", "acc_alice", 1000)

    def test_transfer_nonexistent_account(self):
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=5000)
        with self.assertRaises(AccountNotFoundError):
            self.service.transfer("acc_alice", "acc_ghost", 1000)
        with self.assertRaises(AccountNotFoundError):
            self.service.transfer("acc_ghost", "acc_alice", 1000)

    def test_db_check_constraint_prevents_negative_balance(self):
        """Defense-in-depth: SQLite CHECK constraint physically rejects negative balances."""
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=1000)
        conn = get_connection(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE accounts SET balance_cents = -1 WHERE id = 'acc_alice';")
        finally:
            conn.close()

    def test_unbalanced_ledger_lines_rejected(self):
        """Engine must reject any journal entry where sum(amount_cents) != 0."""
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=5000)
        self.service.create_account("acc_bob", "Bob", initial_balance_cents=5000)

        unbalanced_lines = [
            LedgerLineInput(account_id="acc_alice", amount_cents=-1000),
            LedgerLineInput(account_id="acc_bob", amount_cents=900),  # Net -100 cents!
        ]

        with self.assertRaises(UnbalancedLedgerEntryError):
            self.service.engine.execute_transaction(
                entry_type="TRANSFER",
                lines=unbalanced_lines,
                description="Malicious unbalanced attempt"
            )

    def test_idempotency_key_duplicate_transfer(self):
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=10000)
        self.service.create_account("acc_bob", "Bob", initial_balance_cents=0)

        idempotency_key = "idemp_unique_tx_12345"

        # First execution
        res1 = self.service.transfer(
            "acc_alice", "acc_bob", 2500,
            idempotency_key=idempotency_key,
            description="Payment"
        )
        self.assertEqual(res1["status"], "COMPLETED")
        self.assertEqual(self.service.get_balance("acc_alice"), 7500)
        self.assertEqual(self.service.get_balance("acc_bob"), 2500)

        # Duplicate execution with exact same key
        res2 = self.service.transfer(
            "acc_alice", "acc_bob", 2500,
            idempotency_key=idempotency_key,
            description="Payment"
        )
        self.assertEqual(res2["status"], "COMPLETED")
        self.assertEqual(res1["journal_entry_id"], res2["journal_entry_id"])

        # Balances must NOT have changed again (no double-charging)
        self.assertEqual(self.service.get_balance("acc_alice"), 7500)
        self.assertEqual(self.service.get_balance("acc_bob"), 2500)

        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])

    def test_idempotency_key_failed_transaction(self):
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=500)
        self.service.create_account("acc_bob", "Bob", initial_balance_cents=0)

        idempotency_key = "idemp_fail_tx_999"

        # Attempt that exceeds balance
        with self.assertRaises(InsufficientFundsError):
            self.service.transfer(
                "acc_alice", "acc_bob", 1000,
                idempotency_key=idempotency_key
            )

        # Repeating the request with the failed idempotency key raises LedgerError
        with self.assertRaises(LedgerError):
            self.service.transfer(
                "acc_alice", "acc_bob", 1000,
                idempotency_key=idempotency_key
            )

    def test_account_statement(self):
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=10000)
        self.service.create_account("acc_bob", "Bob", initial_balance_cents=5000)

        self.service.transfer("acc_alice", "acc_bob", 2000, description="Tx 1")
        self.service.transfer("acc_bob", "acc_alice", 500, description="Tx 2")

        statement = self.service.get_account_statement("acc_alice")
        self.assertTrue(statement["is_reconciled"])
        self.assertEqual(statement["current_balance_cents"], 8500)
        self.assertEqual(statement["reconciled_balance_cents"], 8500)
        self.assertEqual(statement["total_transactions"], 3)  # Initial deposit + 2 transfers

    def test_boundary_conditions(self):
        # 1 cent transfer
        self.service.create_account("acc_alice", "Alice", initial_balance_cents=1)
        self.service.create_account("acc_bob", "Bob", initial_balance_cents=0)
        self.service.transfer("acc_alice", "acc_bob", 1)
        self.assertEqual(self.service.get_balance("acc_alice"), 0)
        self.assertEqual(self.service.get_balance("acc_bob"), 1)

        # Emptying to exact zero balance
        self.service.create_account("acc_charlie", "Charlie", initial_balance_cents=54321)
        self.service.transfer("acc_charlie", "acc_bob", 54321)
        self.assertEqual(self.service.get_balance("acc_charlie"), 0)
        self.assertEqual(self.service.get_balance("acc_bob"), 54322)

        # Large amount transfer ($1,000,000.00 = 100,000,000 cents)
        self.service.create_account("acc_whale_1", "Whale 1", initial_balance_cents=100000000)
        self.service.create_account("acc_whale_2", "Whale 2", initial_balance_cents=0)
        self.service.transfer("acc_whale_1", "acc_whale_2", 100000000)
        self.assertEqual(self.service.get_balance("acc_whale_1"), 0)
        self.assertEqual(self.service.get_balance("acc_whale_2"), 100000000)

        # Verify full ledger audit passes
        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["discrepancies"], [])


if __name__ == "__main__":
    unittest.main()
