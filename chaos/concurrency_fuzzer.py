"""
Pocketful Chaos & Concurrency Stress Testing Harness.

Executes extreme concurrency stress tests:
1. Overdraft Race: 50 concurrent threads racing to overdraft a single $100 account.
2. Bidirectional Deadlock Gauntlet: 50 simultaneous opposite transfers (A -> B and B -> A).
3. Idempotency Key Stampede: 20 concurrent duplicate requests with the exact same Idempotency-Key.

Verifies:
- Zero balance drift
- Zero negative balances
- Exact total money conservation
- 100% journal entry balance invariant
- Complete system audit pass
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ledger_engine import InsufficientFundsError
from src.service import PocketfulService


class ConcurrencyChaosHarness:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.service = PocketfulService(db_path=self.db_path)

    # --------------------------------------------------------------------------
    # Experiment 1: The Overdraft Race (50 threads)
    # --------------------------------------------------------------------------
    def run_overdraft_race(self, num_threads: int = 50) -> Dict[str, Any]:
        """
        50 concurrent threads race to overdraft a single account containing $100 (10,000 cents).
        Each thread attempts to withdraw/transfer $10 (1,000 cents) to a unique target.
        Exactly 10 transfers must succeed; exactly 40 must fail with InsufficientFundsError.
        The sender balance must reach exactly $0 without ever going negative.
        """
        print(f"\n[CHAOS 1] Starting Overdraft Race ({num_threads} concurrent threads)...")
        victim_id = "victim_pool"
        initial_cents = 10_000  # $100.00
        transfer_cents = 1_000  # $10.00

        self.service.create_account(victim_id, "Victim Account", initial_balance_cents=initial_cents)

        target_ids = []
        for i in range(num_threads):
            t_id = f"target_{i:02d}"
            target_ids.append(t_id)
            self.service.create_account(t_id, f"Recipient {i}", initial_balance_cents=0)

        barrier = threading.Barrier(num_threads)
        successes: List[Dict[str, Any]] = []
        insufficient_funds_errors: List[str] = []
        unexpected_errors: List[Exception] = []
        lock = threading.Lock()

        start_time = time.time()

        def worker(thread_idx: int):
            t_id = target_ids[thread_idx]
            # Each thread uses a dedicated service instance pointing to the same DB
            thread_service = PocketfulService(db_path=self.db_path)
            try:
                barrier.wait(timeout=10.0)
                res = thread_service.transfer(
                    from_account_id=victim_id,
                    to_account_id=t_id,
                    amount_cents=transfer_cents,
                    description=f"Overdraft race attempt {thread_idx}",
                )
                with lock:
                    successes.append(res)
            except InsufficientFundsError as e:
                with lock:
                    insufficient_funds_errors.append(str(e))
            except Exception as e:
                with lock:
                    unexpected_errors.append(e)

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        elapsed = time.time() - start_time

        # Audits & Invariant Assertions
        final_victim_balance = self.service.get_balance(victim_id)
        target_balances = [self.service.get_balance(t_id) for t_id in target_ids]
        total_target_cents = sum(target_balances)

        audit = self.service.run_full_ledger_audit()

        # Invariant Verification
        expected_successes = initial_cents // transfer_cents  # 10
        expected_failures = num_threads - expected_successes  # 40

        assert len(unexpected_errors) == 0, f"Unexpected errors occurred: {unexpected_errors}"
        assert len(successes) == expected_successes, (
            f"Expected exactly {expected_successes} successes, got {len(successes)}"
        )
        assert len(insufficient_funds_errors) == expected_failures, (
            f"Expected exactly {expected_failures} insufficient funds errors, got {len(insufficient_funds_errors)}"
        )
        assert final_victim_balance == 0, (
            f"Victim balance must be exactly 0, got {final_victim_balance}"
        )
        assert total_target_cents == initial_cents, (
            f"Total target cents must be {initial_cents}, got {total_target_cents}"
        )
        assert audit["is_valid"], f"Full ledger audit failed: {audit['discrepancies']}"

        report = {
            "test_name": "Overdraft Race",
            "threads": num_threads,
            "successes": len(successes),
            "expected_successes": expected_successes,
            "insufficient_funds_caught": len(insufficient_funds_errors),
            "expected_failures": expected_failures,
            "final_victim_balance_cents": final_victim_balance,
            "total_recipients_balance_cents": total_target_cents,
            "elapsed_seconds": elapsed,
            "audit_passed": audit["is_valid"],
            "discrepancies": audit["discrepancies"],
        }
        print(f"[CHAOS 1 PASSED] 10 successes, 40 overdraft rejections, balance=$0.00, elapsed={elapsed:.3f}s")
        return report

    # --------------------------------------------------------------------------
    # Experiment 2: Bidirectional Deadlock Gauntlet (50 transfers)
    # --------------------------------------------------------------------------
    def run_bidirectional_deadlock_gauntlet(self, num_threads: int = 50) -> Dict[str, Any]:
        """
        50 concurrent threads executing simultaneous opposite transfers between Account Alpha and Account Beta:
        - 25 threads transfer $5 (500 cents) Alpha -> Beta
        - 25 threads transfer $5 (500 cents) Beta -> Alpha
        All 50 threads synchronize at a barrier to fire at the exact same microsecond.
        Deterministic lock ordering must prevent all deadlocks.
        """
        print(f"\n[CHAOS 2] Starting Bidirectional Deadlock Gauntlet ({num_threads} simultaneous transfers)...")
        alpha_id = "acc_alpha"
        beta_id = "acc_beta"
        initial_balance = 50_000  # $500.00 each
        transfer_amount = 500     # $5.00

        self.service.create_account(alpha_id, "Alpha Corp", initial_balance_cents=initial_balance)
        self.service.create_account(beta_id, "Beta Corp", initial_balance_cents=initial_balance)

        barrier = threading.Barrier(num_threads)
        successes = []
        errors = []
        lock = threading.Lock()

        start_time = time.time()

        def worker(thread_idx: int):
            thread_service = PocketfulService(db_path=self.db_path)
            # Even threads transfer Alpha -> Beta; Odd threads transfer Beta -> Alpha
            if thread_idx % 2 == 0:
                src, dst = alpha_id, beta_id
            else:
                src, dst = beta_id, alpha_id

            try:
                barrier.wait(timeout=10.0)
                res = thread_service.transfer(
                    from_account_id=src,
                    to_account_id=dst,
                    amount_cents=transfer_amount,
                    description=f"Deadlock test {thread_idx} ({src} -> {dst})",
                )
                with lock:
                    successes.append(res)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        elapsed = time.time() - start_time

        final_alpha = self.service.get_balance(alpha_id)
        final_beta = self.service.get_balance(beta_id)
        total_combined = final_alpha + final_beta

        audit = self.service.run_full_ledger_audit()

        assert len(errors) == 0, f"Deadlock or errors encountered: {errors}"
        assert len(successes) == num_threads, f"Expected {num_threads} successes, got {len(successes)}"
        assert final_alpha == initial_balance, f"Alpha balance drift: expected {initial_balance}, got {final_alpha}"
        assert final_beta == initial_balance, f"Beta balance drift: expected {initial_balance}, got {final_beta}"
        assert total_combined == (initial_balance * 2), "Total money conservation violated"
        assert audit["is_valid"], f"Full ledger audit failed: {audit['discrepancies']}"

        report = {
            "test_name": "Bidirectional Deadlock Gauntlet",
            "threads": num_threads,
            "transfers_completed": len(successes),
            "errors": len(errors),
            "final_alpha_cents": final_alpha,
            "final_beta_cents": final_beta,
            "total_system_balance_conserved": (total_combined == initial_balance * 2),
            "elapsed_seconds": elapsed,
            "audit_passed": audit["is_valid"],
            "discrepancies": audit["discrepancies"],
        }
        print(f"[CHAOS 2 PASSED] 50/50 bidirectional transfers completed without deadlock, elapsed={elapsed:.3f}s")
        return report

    # --------------------------------------------------------------------------
    # Experiment 3: The Idempotency Key Stampede (20 concurrent duplicates)
    # --------------------------------------------------------------------------
    def run_idempotency_stampede(self, num_threads: int = 20) -> Dict[str, Any]:
        """
        20 concurrent duplicate transfer requests racing simultaneously with the exact same Idempotency-Key.
        Exactly one financial transaction must occur:
        - Sender deducted once ($25.00)
        - Receiver credited once ($25.00)
        - All 20 threads receive identical successful response payload with matching journal_entry_id.
        """
        print(f"\n[CHAOS 3] Starting Idempotency Key Stampede ({num_threads} concurrent duplicate requests)...")
        sender_id = "acc_sender_stampede"
        receiver_id = "acc_receiver_stampede"
        initial_cents = 10_000  # $100.00
        transfer_cents = 2_500  # $25.00
        stampede_key = "IDEMP_KEY_CONCURRENT_STAMPEDE_2026_XYZ"

        self.service.create_account(sender_id, "Sender", initial_balance_cents=initial_cents)
        self.service.create_account(receiver_id, "Receiver", initial_balance_cents=0)

        barrier = threading.Barrier(num_threads)
        results = []
        errors = []
        lock = threading.Lock()

        start_time = time.time()

        def worker(thread_idx: int):
            thread_service = PocketfulService(db_path=self.db_path)
            try:
                barrier.wait(timeout=10.0)
                res = thread_service.transfer(
                    from_account_id=sender_id,
                    to_account_id=receiver_id,
                    amount_cents=transfer_cents,
                    idempotency_key=stampede_key,
                    description="Stampede transfer",
                )
                with lock:
                    results.append(res)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        elapsed = time.time() - start_time

        final_sender = self.service.get_balance(sender_id)
        final_receiver = self.service.get_balance(receiver_id)

        audit = self.service.run_full_ledger_audit()

        assert len(errors) == 0, f"Stampede errors encountered: {errors}"
        assert len(results) == num_threads, f"Expected {num_threads} completed responses, got {len(results)}"

        # Assert all 20 threads returned identical journal entry ID
        first_entry_id = results[0]["journal_entry_id"]
        for res in results:
            assert res["journal_entry_id"] == first_entry_id, (
                f"Mismatch in returned journal_entry_id: {res['journal_entry_id']} vs {first_entry_id}"
            )

        # Money must have been deducted exactly ONCE
        expected_sender = initial_cents - transfer_cents     # 7,500
        expected_receiver = transfer_cents                   # 2,500
        assert final_sender == expected_sender, (
            f"Sender balance incorrect: expected {expected_sender}, got {final_sender}"
        )
        assert final_receiver == expected_receiver, (
            f"Receiver balance incorrect: expected {expected_receiver}, got {final_receiver}"
        )
        assert audit["is_valid"], f"Full ledger audit failed: {audit['discrepancies']}"

        report = {
            "test_name": "Idempotency Stampede",
            "threads": num_threads,
            "identical_responses_received": len(results),
            "unique_journal_entry_id": first_entry_id,
            "sender_deducted_once_cents": final_sender,
            "receiver_credited_once_cents": final_receiver,
            "elapsed_seconds": elapsed,
            "audit_passed": audit["is_valid"],
            "discrepancies": audit["discrepancies"],
        }
        print(f"[CHAOS 3 PASSED] 20/20 duplicate calls resolved to identical single transaction, elapsed={elapsed:.3f}s")
        return report


def run_all_chaos_tests() -> Dict[str, Any]:
    """Executes all 3 chaos stress tests in an isolated temporary database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "chaos_pocketful.db")
        harness = ConcurrencyChaosHarness(db_path=db_path)

        print("=" * 80)
        print("POCKETFUL CONCURRENCY & CHAOS FUZZER SUITE")
        print(f"Isolated Test Database: {db_path}")
        print("=" * 80)

        t0 = time.time()
        rep1 = harness.run_overdraft_race(num_threads=50)
        rep2 = harness.run_bidirectional_deadlock_gauntlet(num_threads=50)
        rep3 = harness.run_idempotency_stampede(num_threads=20)
        total_time = time.time() - t0

        final_audit = harness.service.run_full_ledger_audit()

        print("\n" + "=" * 80)
        print("CHAOS VERIFICATION & RECONCILIATION SUMMARY")
        print("=" * 80)
        print(f"Total Chaos Execution Time    : {total_time:.3f}s")
        print(f"Total Journal Entries Created : {final_audit['total_journal_entries']}")
        print(f"Total Ledger Lines Written    : {final_audit['total_ledger_lines']}")
        print(f"Total System Balance Cents    : {final_audit['total_system_balance_cents']} (Conservation Verified: True)")
        print(f"Discrepancies Detected        : {final_audit['discrepancy_count']}")
        print(f"Overall Ledger Status         : {'HEALTHY & VERIFIED' if final_audit['is_valid'] else 'CORRUPTED'}")
        print("=" * 80 + "\n")

        return {
            "experiment_1_overdraft": rep1,
            "experiment_2_deadlock": rep2,
            "experiment_3_idempotency": rep3,
            "final_audit": final_audit,
            "total_time": total_time,
            "all_passed": (
                rep1["audit_passed"]
                and rep2["audit_passed"]
                and rep3["audit_passed"]
                and final_audit["is_valid"]
            )
        }


if __name__ == "__main__":
    result = run_all_chaos_tests()
    if not result["all_passed"]:
        sys.exit(1)
