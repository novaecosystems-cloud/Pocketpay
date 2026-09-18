"""
PhonePe Realistic Traffic Replay & Stress Benchmark for Pocketpay.
Simulates high-velocity digital payment traffic across accounts using
PhonePe Pulse payment category distributions and transaction amount curves.
"""

import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.models import init_db
from src.service import PocketfulService
from src.phonepe_loader import PhonePeLoadSampler


def run_phonepe_traffic_benchmark(num_users: int = 10, num_transactions: int = 100, concurrency: int = 16):
    print("=" * 80)
    print("PHONEPE REALISTIC TRAFFIC REPLAY BENCHMARK")
    print("Calibrated with PhonePe Pulse Transaction Distributions")
    print("=" * 80)

    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "phonepe_benchmark.db")
    init_db(db_path)
    service = PocketfulService(db_path=db_path)

    # 1. Provision user pool
    users = []
    initial_funding_each = 100000  # $1000.00 funding per user
    print(f"Provisioning {num_users} accounts with ${initial_funding_each / 100:.2f} each...")

    for i in range(num_users):
        u_id = f"user_phonepe_{i+1:02d}"
        service.create_account(
            account_id=u_id,
            name=f"PhonePe User {i+1}",
            initial_balance_cents=initial_funding_each
        )
        users.append(u_id)

    total_deposited = initial_funding_each * num_users
    print(f"Total Initial Funds: ${total_deposited / 100:.2f}\n")

    # 2. Sample PhonePe transactions
    sampler = PhonePeLoadSampler(user_pool=users, seed=2026)
    transfers = sampler.generate_batch(num_transactions)

    print(f"Executing {num_transactions} realistic P2P & merchant transfers across {concurrency} concurrent threads...")
    start_time = time.time()

    success_count = 0
    rejected_count = 0

    def execute_single_transfer(t):
        try:
            res = service.transfer(
                from_account_id=t.sender_id,
                to_account_id=t.receiver_id,
                amount_cents=t.amount_cents,
                description=f"[{t.category}] P2P Transfer",
                idempotency_key=t.idempotency_key
            )
            return True, t.amount_cents
        except Exception:
            return False, t.amount_cents

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(execute_single_transfer, t) for t in transfers]
        for f in as_completed(futures):
            ok, amt = f.result()
            if ok:
                success_count += 1
            else:
                rejected_count += 1

    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.3f}s ({num_transactions / elapsed:.1f} tx/sec)")
    print(f"Successful Transfers: {success_count}")
    print(f"Rejected Transfers  : {rejected_count}")

    # 3. Audit reconciliation
    print("\nRunning End-of-Traffic Ledger Audit...")
    audit = service.run_full_ledger_audit()

    print(f"Total Journal Entries: {audit['total_journal_entries']}")
    print(f"Total Ledger Lines   : {audit['total_ledger_lines']}")
    print(f"Discrepancies        : {audit['discrepancy_count']}")
    print(f"Audit Is Valid       : {audit['is_valid']}")

    # Assert money conservation
    user_balances = sum(service.get_balance(u) for u in users)
    assert user_balances == total_deposited, f"Money leaked! Expected {total_deposited}, found {user_balances}"
    assert audit["is_valid"] is True, f"Discrepancies found: {audit['discrepancies']}"

    print(f"User Balance Conservation Verified: Total ${user_balances / 100:.2f} == Initial ${total_deposited / 100:.2f}")
    print("=" * 80)
    print("PHONEPE BENCHMARK COMPLETED SUCCESSFULLY: 100% INVARIANT PRESERVATION")
    print("=" * 80)


if __name__ == "__main__":
    run_phonepe_traffic_benchmark()
