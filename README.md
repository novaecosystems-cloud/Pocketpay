# Pocketpay 💸
> **A Banking-Grade Autonomous Double-Entry Ledger & Payment Engine**
> Built for the **WeAreDevelopers × BAND: DARK FACTORY Hackathon** (Sep 26 – Oct 5, 2026).

---

## 🌟 Overview

**Pocketpay** is a high-throughput, concurrency-safe digital wallet and double-entry payments engine designed to satisfy the core tenet of the Dark Factory challenge:

> *"Money must never be created, destroyed, or spent twice. Not under concurrent transfers. Not under retries. Not under rounding."*

The codebase is built with zero-drift financial invariants, deterministic deadlock-free locking, atomic idempotency lifecycles, and a built-in chaos fuzzer calibrated on digital payment transaction distributions.

---

## 🏛 Core Architectural Invariants

### 1. Mathematical Double-Entry Conservation ($\sum \Delta = 0$)
Every balance movement is an atomic journal entry comprised of at least two balanced ledger lines:
$$\Delta \text{Sender} = -X, \quad \Delta \text{Receiver} = +X \implies \sum \Delta = 0$$
The database transaction strictly aborts and rolls back if any journal entry does not sum to zero.

### 2. Guaranteed Non-Negative Balances
Every non-system account enforces `CHECK (balance_cents >= 0)` at the SQLite/PostgreSQL schema level. Even under adversarial concurrent execution, an account cannot drop below \$0.00.

### 3. Deterministic Deadlock-Free Lock Ordering
To eliminate lock-ordering deadlocks during simultaneous cross-account transfers ($A \rightarrow B$ and $B \rightarrow A$), accounts are sorted lexicographically before acquiring transaction locks:
$$\text{first} = \min(\text{id}_A, \text{id}_B), \quad \text{second} = \max(\text{id}_A, \text{id}_B)$$

### 4. Atomic Idempotency State Machine
Every write request accepts an `Idempotency-Key` header:
- **`IN_FLIGHT`**: Concurrent duplicate calls are rejected with HTTP 409 Conflict.
- **`COMPLETED`**: Safe replay returns the cached response without re-executing transactions.
- **`FAILED`**: Failed attempts are safely recordable and isolated.

---

## 📂 Project Structure

```
dark-factory/
├── src/
│   ├── models.py             # SQLite WAL mode & strict DB CHECK constraints
│   ├── ledger_engine.py      # Transactional double-entry engine & lock sorter
│   ├── service.py            # High-level wallet services & audit statements
│   ├── api.py                # FastAPI / REST endpoints
│   └── phonepe_loader.py     # PhonePe Pulse telemetry & load sampler
├── tests/
│   └── test_ledger.py        # 15 unit tests covering invariants & edge cases
├── chaos/
│   ├── concurrency_fuzzer.py # 50-worker overdraft, deadlock, & retry fuzzer
│   └── phonepe_replay.py     # Realistic payment load benchmark
└── agents/                   # BAND Desktop room configurations
```

---

## 🚀 Quickstart

### 1. Local Setup
```bash
# Clone repository
git clone https://github.com/novaecosystems-cloud/Pocketpay.git
cd Pocketpay

# Install dependencies
pip install -r requirements.txt

# Launch FastAPI Server
uvicorn src.api:app --reload --port 8000
```
Interactive Swagger docs will be available at `http://localhost:8000/docs`.

### 2. Docker Setup
```bash
# Run API service
docker compose up pocketpay-api

# Run unit tests in container
docker compose run --rm pocketpay-test

# Run PhonePe Pulse chaos benchmark in container
docker compose run --rm pocketpay-chaos
```

---

## 🧪 Verification & Chaos Fuzzing

### Run Unit Tests
```bash
python -m unittest discover tests
```
*Result: 15 / 15 PASSED (100%)*

### Run Chaos Stress Suite
```bash
python chaos/concurrency_fuzzer.py
```
* **Experiment 1 (Overdraft Race - 50 concurrent threads):** 50 threads racing to overdraft a single \$100 account. Exactly 10 succeed, 40 rejected with `InsufficientFundsError`. Ending balance: \$0.00.
* **Experiment 2 (Bidirectional Deadlock Gauntlet - 50 simultaneous transfers):** 50 concurrent cross-transfers ($A \leftrightarrow B$). 50/50 succeed with **0 deadlocks**.
* **Experiment 3 (Idempotency Key Stampede - 20 concurrent duplicate calls):** Exactly 1 transaction executed on the ledger; 20 callers receive identical receipt. 0 duplicate debits.
* **Ledger Audit Check:** System balance sums to 0; 0 discrepancies detected.

---

## 🤖 Dark Factory Multi-Agent Architecture
Developed in **BAND Desktop** with:
- **Architect Agent**: Spec definition & invariant contract enforcement.
- **Builder Agent**: Service & schema implementation.
- **Chaos / Fuzzer Agent**: Microsecond-synchronized concurrency stress harness.
- **Auditor Agent**: Cryptographic ledger integrity checks.

---

## 📜 License
MIT License.
