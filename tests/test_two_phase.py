"""
Unit tests for TigerBeetle Two-Phase Transfers (PENDING -> POSTED / VOIDED).
"""

import os
import tempfile
import unittest

from src.service import PocketfulService
from src.ledger_engine import InsufficientFundsError, InvalidTransactionError


class TestTwoPhaseTransfers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_two_phase.db")
        self.service = PocketfulService(db_path=self.db_path)

        # Alice: $100.00, Bob: $0.00
        self.service.create_account("ACC_ALICE", "Alice", initial_balance_cents=10000)
        self.service.create_account("ACC_BOB", "Bob", initial_balance_cents=0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_pending_transfer_hold(self):
        """Verifies Phase 1: pending hold reserves funds without changing cleared balance."""
        hold = self.service.authorize_hold(
            from_account_id="ACC_ALICE",
            to_account_id="ACC_BOB",
            amount_cents=3000, # $30 hold
            description="Hotel reservation security hold",
        )

        self.assertEqual(hold["status"], "PENDING")
        self.assertEqual(hold["amount_cents"], 3000)

        # Cleared balance unchanged
        self.assertEqual(self.service.get_balance("ACC_ALICE"), 10000)
        self.assertEqual(self.service.get_balance("ACC_BOB"), 0)

        # Available balance decremented by held amount ($100 - $30 = $70)
        self.assertEqual(self.service.get_available_balance("ACC_ALICE"), 7000)
        self.assertEqual(self.service.get_available_balance("ACC_BOB"), 0)

        # Full audit should be valid with 1 active hold
        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["total_active_pending_transfers"], 1)
        self.assertEqual(audit["total_pending_held_cents"], 3000)

    def test_overdraft_prevention_during_hold(self):
        """Verifies that user cannot spend funds currently locked in a pending hold."""
        # Place $80 hold on Alice ($100 balance -> $20 available)
        self.service.authorize_hold(
            from_account_id="ACC_ALICE",
            to_account_id="ACC_BOB",
            amount_cents=8000,
        )

        # Alice tries to transfer $30 to Bob. Although cleared balance is $100, available is only $20!
        with self.assertRaises(InsufficientFundsError):
            self.service.transfer("ACC_ALICE", "ACC_BOB", 3000)

        # But Alice CAN transfer $15 ($15 <= $20 available)
        tx = self.service.transfer("ACC_ALICE", "ACC_BOB", 1500)
        self.assertEqual(tx["status"], "COMPLETED")
        self.assertEqual(self.service.get_available_balance("ACC_ALICE"), 500) # $5 left

    def test_post_pending_transfer_capture(self):
        """Verifies Phase 2a: posting/capturing a hold commits funds to cleared balances."""
        hold = self.service.authorize_hold(
            from_account_id="ACC_ALICE",
            to_account_id="ACC_BOB",
            amount_cents=4500, # $45 hold
            description="E-commerce pre-auth",
        )

        # Settle / Capture hold
        posted = self.service.capture_hold(hold["id"])
        self.assertEqual(posted["status"], "POSTED")

        # Cleared balances updated!
        self.assertEqual(self.service.get_balance("ACC_ALICE"), 5500) # $55
        self.assertEqual(self.service.get_balance("ACC_BOB"), 4500)   # $45

        # Available balances equal cleared balances (hold released)
        self.assertEqual(self.service.get_available_balance("ACC_ALICE"), 5500)
        self.assertEqual(self.service.get_available_balance("ACC_BOB"), 4500)

        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["total_active_pending_transfers"], 0)

    def test_void_pending_transfer_release(self):
        """Verifies Phase 2b: voiding/canceling a hold restores available balance without moving cleared money."""
        hold = self.service.authorize_hold(
            from_account_id="ACC_ALICE",
            to_account_id="ACC_BOB",
            amount_cents=4000,
            description="Ride-hail temporary auth",
        )

        self.assertEqual(self.service.get_available_balance("ACC_ALICE"), 6000)

        # Void / Cancel hold
        voided = self.service.void_hold(hold["id"], reason="Ride cancelled by rider")
        self.assertEqual(voided["status"], "VOIDED")

        # Cleared balances untouched
        self.assertEqual(self.service.get_balance("ACC_ALICE"), 10000)
        self.assertEqual(self.service.get_balance("ACC_BOB"), 0)

        # Available balance 100% restored
        self.assertEqual(self.service.get_available_balance("ACC_ALICE"), 10000)

        audit = self.service.run_full_ledger_audit()
        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["total_active_pending_transfers"], 0)

    def test_cannot_double_post_or_void(self):
        """Verifies terminal state protection: cannot post or void an already finalized hold."""
        hold = self.service.authorize_hold(
            from_account_id="ACC_ALICE",
            to_account_id="ACC_BOB",
            amount_cents=2000,
        )

        self.service.capture_hold(hold["id"])

        # Attempting to post again must raise InvalidTransactionError
        with self.assertRaises(InvalidTransactionError):
            self.service.capture_hold(hold["id"])

        # Attempting to void an already posted hold must raise InvalidTransactionError
        with self.assertRaises(InvalidTransactionError):
            self.service.void_hold(hold["id"])


if __name__ == "__main__":
    unittest.main()
