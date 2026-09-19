"""
Pocketpay Engine Scalability Benchmark & TigerBeetle Comparison.

Demonstrates:
1. Unbatched vs Vectorized Batch Group Commits (Throughput Scaling)
2. TigerBeetle Two-Phase Authorizations & Settlements (Hold -> Capture / Void)
3. High-Velocity PaySim Mobile Money Ingestion
4. Complete Invariant Audit Reconciliation
"""

import os
import sys
import time
from typing import List, Dict, Any

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.models import get_connection, init_db
from src.service import PocketfulService
from src.paysim_loader import replay_paysim_stream


BENCH_DB_PATH = "data/benchmark_scalability.db"


def clean_benchmark_db():
    if os.path.exists(BENCH_DB_PATH):
        try:
            os.remove(BENCH_DB_PATH)
        except OSError:
            pass


def benchmark_unbatched_vs_batch(service: PocketfulService, count: int = 10000):
    print("=" * 80)
    print("EXPERIMENT 1: UNBATCHED VS VECTORIZED GROUP-COMMIT BATCHING")
    print("=" * 80)

    conn = get_connection(BENCH_DB_PATH)

    # Provision accounts
    acc_from = "BENCH_SOURCE"
    acc_to = "BENCH_TARGET"
    service.create_account(acc_from, "Source Liquidity", initial_balance_cents=50_000_000, conn=conn)
    service.create_account(acc_to, "Target Liquidity", initial_balance_cents=0, conn=conn)

    # 1. Unbatched Sequential Transfers (1,000 txs sample)
    sample_unbatched = 2000
    print(f"\n[1A] Measuring Unbatched Single Transfers ({sample_unbatched:,} iterations)...")
    t0 = time.time()
    for _ in range(sample_unbatched):
        service.transfer(acc_from, acc_to, 10, conn=conn)
    dt_unbatched = time.time() - t0
    tps_unbatched = sample_unbatched / dt_unbatched
    print(f"     -> Completed in {dt_unbatched:.2f}s ({tps_unbatched:,.1f} tx/sec)")

    # 2. Vectorized Batch Commits (Batch size: 250)
    batch_size = 250
    total_batch_tx = count
    print(f"\n[1B] Measuring Vectorized Group-Commits ({total_batch_tx:,} transfers, Batch Size {batch_size})...")
    
    t0 = time.time()
    num_batches = total_batch_tx // batch_size
    for b_idx in range(num_batches):
        batch = [
            {"from_account_id": acc_from, "to_account_id": acc_to, "amount_cents": 10}
            for _ in range(batch_size)
        ]
        service.transfer_batch(batch, conn=conn)
    dt_batch = time.time() - t0
    tps_batch = total_batch_tx / dt_batch
    speedup = tps_batch / tps_unbatched if tps_unbatched > 0 else 0

    print(f"     -> Completed in {dt_batch:.2f}s ({tps_batch:,.1f} tx/sec)")
    print(f"     -> BATCH COMMIT SPEEDUP: {speedup:.1f}x FASTER than unbatched execution!")

    conn.close()


def benchmark_two_phase_holds(service: PocketfulService, count: int = 2000):
    print("\n" + "=" * 80)
    print("EXPERIMENT 2: TIGERBEETLE TWO-PHASE HOLDS (AUTH -> CAPTURE / VOID)")
    print("=" * 80)

    conn = get_connection(BENCH_DB_PATH)

    service.create_account("HOTEL_GUEST", "Hotel Guest", initial_balance_cents=10_000_000, conn=conn)
    service.create_account("HOTEL_CHAIN", "Grand Hotel Chain", initial_balance_cents=0, conn=conn)

    print(f"\nExecuting {count:,} Two-Phase Cycles (50% Auth->Capture, 50% Auth->Void)...")
    t0 = time.time()

    captured = 0
    voided = 0

    for i in range(count):
        # Phase 1: Authorize Hold
        hold = service.authorize_hold("HOTEL_GUEST", "HOTEL_CHAIN", 100, conn=conn)
        
        # Phase 2: Settle (Capture) or Release (Void)
        if i % 2 == 0:
            service.capture_hold(hold["id"], conn=conn)
            captured += 1
        else:
            service.void_hold(hold["id"], reason="Reservation cancelled", conn=conn)
            voided += 1

    dt = time.time() - t0
    rate = (count * 2) / dt  # 2 operations per cycle (Hold + Settle/Void)
    print(f"     -> Completed {count:,} cycles ({count * 2:,} operations) in {dt:.2f}s ({rate:,.1f} ops/sec)")
    print(f"     -> Successfully Captured: {captured:,} holds")
    print(f"     -> Successfully Voided  : {voided:,} holds")
    print(f"     -> Active Pending Holds : {service.get_account('HOTEL_GUEST', conn=conn)['pending_debit_cents']} cents")

    conn.close()


def run_full_scalability_suite():
    clean_benchmark_db()
    service = PocketfulService(db_path=BENCH_DB_PATH)

    # Run Benchmark 1
    benchmark_unbatched_vs_batch(service, count=10000)

    # Run Benchmark 2
    benchmark_two_phase_holds(service, count=2000)

    # Run Benchmark 3: PaySim Stream
    print("\n" + "=" * 80)
    print("EXPERIMENT 3: PAYSIM MOBILE MONEY REALISTIC LOAD INGESTION")
    print("=" * 80)
    paysim_res = replay_paysim_stream(service, num_records=15000, batch_size=250, db_path=BENCH_DB_PATH)

    # Final Audit
    print("\n" + "=" * 80)
    print("FINAL CRYPTOGRAPHIC LEDGER AUDIT RECONCILIATION")
    print("=" * 80)
    audit = service.run_full_ledger_audit()
    print(f"  * Audit Valid              : {audit['is_valid']}")
    print(f"  * Discrepancies Found      : {audit['discrepancy_count']}")
    print(f"  * Total System Balance     : {audit['total_system_balance_cents']} cents ($0.00 drift)")
    print(f"  * Active Pending Holds     : {audit['total_active_pending_transfers']}")
    print(f"  * Total Journal Entries    : {audit['total_journal_entries']:,}")
    print(f"  * Total Ledger Lines       : {audit['total_ledger_lines']:,}")
    print("=" * 80)

    if not audit["is_valid"]:
        raise SystemExit("Benchmark Audit FAILED!")


if __name__ == "__main__":
    run_full_scalability_suite()
