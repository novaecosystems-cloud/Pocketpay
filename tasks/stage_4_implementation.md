# Stage 4 Task Specification: Refunds & Batch Corrections

## Overview
Stage 4 widens the bitemporal ledger built in Stage 3 to support:
1. **Refunds** (`POST /payments/{payment_id}/refunds`):
   - Receiver refunds an original payment (partially or fully).
   - `refund_of` field attached to all payments (null for non-refunds, target `payment_id` for refunds).
   - Strict validation: target exists (404), caller is original receiver (403), target is not a refund (422 `invalid_refund_target`), total refunded does not exceed current corrected payment amount (422 `refund_exceeds_payment`).
   - Moves money from receiver's available balance to sender's balance (409 `insufficient_funds`).
   - Replay idempotency: returns 200 with original refund payment object.
2. **Correction Batches** (`POST /correction-batches`):
   - Only settlement operators (`settlement_operator_ids`) can invoke (403 `forbidden`).
   - 1..32 corrections in atomic batch.
   - Distinct payment IDs, target existence (404), valid expected revision (409 `stale_revision`), no captures or refunds (422 `linked_payment_immutable`).
   - Settlement completeness: if any member of a settlement is in the batch, all members must be included (422 `incomplete_settlement`).
   - Settlement members must share identical effective instants (422 `validation_failed`).
   - Check `refund_exceeds_payment` for all targets.
   - Check available funds across all affected users (409 `insufficient_funds`).
   - Check historical overdraft at every boundary across both users' timelines under combined proposed revisions (409 `historical_overdraft`).
   - Atomic application with shared `recorded_at` strictly later than previous recorded_at of every member, assigning `correction_batch_id`.
   - Returns 201 with `correction_batch_id`, `recorded_at`, `revisions`.
3. **Database & Persistence**:
   - `pocketful_stage4.db` in `stage-4/app/ledger.py`.
   - Update `export_state` and `import_state` to preserve `refund_of` and `correction_batch_id`.

## Required Files
- `stage-4/app/models.py`: validation for refunds, batch corrections, and `refund_of` serialization.
- `stage-4/app/ledger.py`: database schema with `refund_of` on payments, `correction_batch_id` on revisions, `create_refund`, `create_correction_batch`.
- `stage-4/app/main.py`: endpoints for `/payments/{payment_id}/refunds` and `/correction-batches`.
- `stage-4/RUN.md`: updated container instructions for `pocketful-stage-4`.
