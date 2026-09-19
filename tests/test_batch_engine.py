"""
Unit tests for TigerBeetle-style Vectorized Group-Commit Batch Transfers.
"""

import os
import tempfile
import unittest

from src.service import PocketfulService
from src.ledger_engine import InsufficientFundsError, AccountNotFoundError, InvalidTransactionError


class TestBatchEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_batch.db")
        self.service = PocketfulService(db_path=self.db_path)

        # Create test accounts
        self.service.create_account("ACC_A", "Alice", initial_balance_cents=10000)  # $100
        self.service.create_account("ACC_B", "Bob", initial_balance_cents=5000)     # $50
        self.service.create_account("ACC_C", "Charlie", initial_balance_cents=2000) # $20
        self.service.create_account("ACC_D", "David", initial_balance_cents=0)      # $0

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_successful_batch_transfer(self):
        """Verifies multi-party transfers commit atomically in a single batch."""
        transfers = [
            {"from_account_id": "ACC_A", "to_account_id": "ACC_B", "amount_cents": 1500, "description": "A to B"},
            {"from_account_id": "ACC_B", "to_account_id": "ACC_C", "amount_cents": 2000, "description": "B to C"},
            {"from_account_id": "ACC_C", "to_account_id": "ACC_D", "amount_cents": 500, "description": "C to D"},
        ]

        result = self.service.transfer_batch(transfers, description="Multi-party test batch")
        self.assertEqual(result["batch_size"], 3)
        self.assertEqual(result["total_cents"], 4000)
        self.assertEqual(len(result["receipts"]), 3)

        # Verify balances:
        # A: 10000 - 1500 = 8500
        # B: 5000 + 1500 - 2000 = 4500
        # C: 2000 + 2000 - 500 = 3500
        # D: 0 + 500 = 500
        self.assertEqual(self.service.get_balance("ACC_A"), 8500)
        self.assertEqual(self.service.get_balance("ACC_B"), 4500)
        self.assertEqual(self.service.get_balance("ACC_C"), 3500)
        self.assertEqual(self.service.get_balance("ACC_D"), 500)

        # Audit check
        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["discrepancy_count"], 0)

    def test_batch_all_or_nothing_atomicity_on_insufficient_funds(self):
        """Verifies that if ONE transfer in a batch exceeds balance, the ENTIRE batch rolls back."""
        # Initial balances: A=$100, B=$50, C=$20, D=$0
        transfers = [
            {"from_account_id": "ACC_A", "to_account_id": "ACC_B", "amount_cents": 1000},
            {"from_account_id": "ACC_C", "to_account_id": "ACC_D", "amount_cents": 50000}, # C only has $20! Overdraft!
            {"from_account_id": "ACC_B", "to_account_id": "ACC_D", "amount_cents": 500},
        ]

        with self.assertRaises(InsufficientFundsError):
            self.service.transfer_batch(transfers)

        # Ensure NO balance changes took place (Alice must still have full $100)
        self.assertEqual(self.service.get_balance("ACC_A"), 10000)
        self.assertEqual(self.service.get_balance("ACC_B"), 5000)
        self.assertEqual(self.service.get_balance("ACC_C"), 2000)
        self.assertEqual(self.service.get_balance("ACC_D"), 0)

    def test_batch_atomicity_on_nonexistent_account(self):
        """Verifies batch rollback if an invalid account is in the batch."""
        transfers = [
            {"from_account_id": "ACC_A", "to_account_id": "ACC_B", "amount_cents": 500},
            {"from_account_id": "ACC_A", "to_account_id": "NON_EXISTENT", "amount_cents": 500},
        ]

        with self.assertRaises(AccountNotFoundError):
            self.service.transfer_batch(transfers)

        self.assertEqual(self.service.get_balance("ACC_A"), 10000)
        self.assertEqual(self.service.get_balance("ACC_B"), 5000)

    def test_circular_batch_transfer(self):
        """Verifies circular chained transfers (A->B, B->C, C->A) in batch without deadlocks."""
        transfers = [
            {"from_account_id": "ACC_A", "to_account_id": "ACC_B", "amount_cents": 1000},
            {"from_account_id": "ACC_B", "to_account_id": "ACC_C", "amount_cents": 1000},
            {"from_account_id": "ACC_C", "to_account_id": "ACC_A", "amount_cents": 1000},
        ]

        result = self.service.transfer_batch(transfers)
        self.assertEqual(result["status"], "COMPLETED")

        # Balances net change is zero
        self.assertEqual(self.service.get_balance("ACC_A"), 10000)
        self.assertEqual(self.service.get_balance("ACC_B"), 5000)
        self.assertEqual(self.service.get_balance("ACC_C"), 2000)

    def test_empty_batch_rejected(self):
        """Verifies that an empty batch is rejected."""
        with self.assertRaises(InvalidTransactionError):
            self.service.transfer_batch([])

    def test_transfer_to_self_in_batch_rejected(self):
        """Verifies that transfer to same account in batch is rejected."""
        with self.assertRaises(InvalidTransactionError):
            self.service.transfer_batch([
                {"from_account_id": "ACC_A", "to_account_id": "ACC_A", "amount_cents": 500}
            ])


if __name__ == "__main__":
    unittest.main()
