# Task Specification: Stage 2 Implementation

**Assigned Seat**: `@implementer`  
**Reviewer Seat**: `@reviewer`  
**Supervising Seat**: `@architect`  
**Target Stage**: `stage-2/`  
**Specification Reference**: `pocketful/spec/stage-2.md`  

---

## 1. Objectives & Deliverables

1. **Two-Phase Payment Authorizations (Holds & Captures)**:
   - `POST /authorizations`: Create hold for recipient. Checks `available >= amount`. Expiration TTL defaults to 600 seconds.
   - `POST /authorizations/{id}/capture`: Receiver captures all or part of hold. Full capture or final capture releases uncaptured remainder.
   - `POST /authorizations/{id}/void`: Payer releases hold.
   - `GET /authorizations`: List user's incoming/outgoing authorizations.
   - `GET /me`: Returns `total`, `available`, `held`. Ensures `available = total - held`.
   - Update `POST /payments`, `POST /requests/{id}/pay`, and `POST /settlements` to deduct and validate against `available` funds.

2. **Web UI & Server-Side Rendering (SSR)**:
   - Serve HTML on `/`, `/requests`, `/split`, `/signup`, `/login`, and `/authorizations` when `Accept: text/html` is requested.
   - Strictly implement all required `data-testid` attributes:
     - Auth: `login-email`, `login-password`, `login-submit`, `auth-error`, `signup-email`, `signup-password`, `signup-display-name`, `signup-submit`, `current-user`, `current-handle`, `logout-button`.
     - Balance & Pay (`/`): `wallet-balance`, `wallet-available`, `wallet-held`, `wallet-refresh`, `pay-handle`, `pay-amount`, `pay-note`, `pay-visibility`, `pay-submit`, `pay-error`, `request-handle`, `request-amount`, `request-note`, `request-submit`, `request-error`.
     - Activity Feed (`/`): `activity-list`, `activity-item-{pid}`, `activity-parties-{pid}`, `activity-amount-{pid}`, `activity-note-{pid}`, `empty-activity`.
     - Requests (`/requests`): `incoming-list`, `outgoing-list`, `empty-requests`, `request-item-{rid}`, `request-amount-{rid}`, `request-pay-{rid}`, `request-decline-{rid}`, `request-cancel-{rid}`, `request-error`.
     - Split (`/split`): `split-amount`, `split-handles`, `split-preview`, `split-share-{handle}`, `split-submit`, `split-error`.
     - Authorizations (`/authorizations`): `authorization-list`, `authorization-item-{id}`, `authorization-amount-{id}`, `authorization-captured-{id}`, `authorization-expires-{id}`, `authorization-capture-amount-{id}`, `authorization-capture-{id}`, `authorization-void-{id}`, `authorization-error`, `empty-authorizations`.

3. **Stage Progression & Verification**:
   - `stage-2/` must pass Stage 1 and Stage 2 test suites.
   - `stage-2/` must NOT pass Stage 3 tests (stage overshoot gate).
