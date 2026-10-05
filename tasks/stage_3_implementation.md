# Stage 3 Task Specification: Statements and Payment Corrections

## Context & Objectives
You are the `@implementer` seat operating in the Band Desktop factory pipeline.
Following the certified completion of Stage 1 (147/147) and Stage 2 (35/35), your task is to construct `stage-3/` compliant with `pocketful/spec/stage-3.md`.

Target Stage: 3
Contiguous Stages Goal: 3 (Pass Stages 1, 2, and 3; deliberate fail on Stage 4 to prevent overshooting Gate 3).

---

## Technical Deliverables

### 1. File Structure
- Copy `stage-2/` to `stage-3/`.
- Ensure `stage-3/Dockerfile` and `stage-3/RUN.md` are present.
- Implement code in `stage-3/app/`.

### 2. Database Schema: Bitemporal Revisions Table
Add to `stage-3/app/ledger.py`:
```sql
CREATE TABLE IF NOT EXISTS payment_revisions (
    payment_id TEXT NOT NULL REFERENCES payments(payment_id),
    revision INTEGER NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (payment_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_revisions_payment ON payment_revisions(payment_id, revision);
CREATE INDEX IF NOT EXISTS idx_revisions_effective ON payment_revisions(effective_at);
CREATE INDEX IF NOT EXISTS idx_revisions_recorded ON payment_revisions(recorded_at);
```

### 3. Payment Creation Integration
- When any payment is created (direct P2P, split fulfillment, request pay), insert revision 1:
  - `revision = 1`
  - `amount = original amount`
  - `effective_at = created_at`
  - `recorded_at = created_at`
  - `reason = ""`
- Seeded payments loaded during `POST /_test/reset`:
  - If `created_at` in future: return 422 `validation_failed` with zero state changes.
  - Insert revision 1 for each seeded payment.

### 4. Point-in-Time Balances: `GET /me?as_of=T&known_at=K`
- Query parameters `as_of` and `known_at` are optional RFC 3339 strings with timezone offsets.
- Validation: any naive timestamp, invalid date format, or empty string returns 422 `validation_failed`.
- Revision Selection:
  - For each payment involving the caller, pick the latest revision where `recorded_at <= known_at` (if `known_at` is omitted, latest recorded).
  - If no revision was recorded by `known_at`, the payment contributes nothing.
- Effective Balance:
  - Sum the selected revisions whose `effective_at <= as_of` (inclusive).
  - An `as_of` at or after latest payment returns current balance.
  - An `as_of` before earliest payment returns opening balance.
  - Echo back `as_of` and `known_at` in the JSON response if supplied.
- Holds:
  - Account for active authorizations and captures up to `as_of` to populate `total`, `available`, and `held`.

### 5. Statements: `GET /statement`
- Parameters: `from`, `to`, `known_at`, `limit`, `offset`, `snapshot`.
- Window: half-open `[from, to)` (defaults: `from` = beginning of time, `to` = now).
- Selection:
  - Only payments sent or received by caller.
  - Ordered by selected `effective_at` ascending, then `payment_id` ascending.
- Fields:
  - `opening_balance`: balance immediately before `from`.
  - `closing_balance`: balance immediately before `to`.
  - Invariant: `opening_balance + sum(e.delta for e in entries) == closing_balance`.
  - Entries: list of `{ payment, delta, balance_after, revision, effective_at, recorded_at }`.
    - Sent payment: `delta = -selected_amount`.
    - Received payment: `delta = +selected_amount`.
    - If `selected_amount == 0`, entry still appears with `delta = 0`.
- Pagination:
  - First read returns an opaque `snapshot` token.
  - `GET /statement?snapshot=<token>&limit=...&offset=...` returns the exact frozen entries and balances.
  - If `from`, `to`, or `known_at` are passed with `snapshot`, return 422 `validation_failed`.
  - Unknown or expired snapshot returns 404 `not_found`.

### 6. Payment Corrections: `POST /payments/{payment_id}/corrections`
- Header: `Idempotency-Key` (required, 1-255 characters).
- Caller: must be original sender of `payment_id`. Otherwise 403 `forbidden`. Unknown payment: 404 `not_found`.
- Immutable links: if payment has `settlement_id` or `authorization_id`, return 422 `linked_payment_immutable`.
- Body:
  - `expected_revision`: positive integer.
  - `amount`: integer `0 <= amount <= 1,000,000,000`.
  - `effective_at`: RFC 3339 timestamp `<= now`.
  - `reason`: string of 1 to 200 characters.
  - All fields strictly required; invalid is 422 `validation_failed`.
- Concurrency & Idempotency:
  - If current revision != `expected_revision`, return 409 `stale_revision`.
  - Replay with same idempotency key and body returns original revision with 200 OK.
- Historical Overdraft Check:
  - Calculate balance at every historical boundary for both sender and receiver.
  - If balance would drop below zero at any point in history under the latest revisions, rollback and return 409 `historical_overdraft`.
  - Current insufficient funds returns 409 `insufficient_funds`.
- State update:
  - Insert revision `N + 1`.
  - Atomically transfer delta between sender and receiver wallets.
  - Return 201 with `{ payment_id, revision, amount, effective_at, recorded_at, reason }`.

### 7. Revisions History: `GET /payments/{payment_id}/revisions`
- Return `{"revisions": [...]}` in revision order.
- Allowed only for sender and receiver. Non-party returns 404 `not_found`. Missing auth returns 401.
