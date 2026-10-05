"""
Pocketful Stage 1 FastAPI Application.
Implements the full HTTP API specification with strict error contracts,
idempotency guarantees, and double-entry conservation.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.ledger import LedgerEngine
from app.models import (
    PocketfulError,
    canonicalize_json_body,
    compute_body_hash,
    make_error_response,
    validate_amount,
)

app = FastAPI(title="Pocketful Stage 1", version="1.0.0")
ledger = LedgerEngine()


@app.exception_handler(PocketfulError)
async def pocketful_error_handler(request: Request, exc: PocketfulError):
    return make_error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return make_error_response(422, "validation_failed", str(exc))


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    return make_error_response(500, "internal_error", str(exc))


async def parse_json_body(request: Request) -> Dict[str, Any]:
    """Safely extracts JSON body or raises 400 malformed_request per §5."""
    try:
        body = await request.body()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))
    except Exception:
        raise PocketfulError(400, "malformed_request", "Invalid JSON request body")


def extract_bearer_token(request: Request) -> str:
    """Extracts bearer token from Authorization header or raises 401 unauthenticated."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise PocketfulError(401, "unauthenticated", "Missing or invalid bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise PocketfulError(401, "unauthenticated", "Empty bearer token")
    return token


def authenticate_user(request: Request) -> Dict[str, Any]:
    """Resolves authenticated user from token or raises 401 unauthenticated."""
    token = extract_bearer_token(request)
    user = ledger.get_user_by_token(token)
    if not user:
        raise PocketfulError(401, "unauthenticated", "Invalid or expired session token")
    return user


def validate_idempotency_header(request: Request) -> str:
    """Validates presence and length of Idempotency-Key header per §7."""
    key = request.headers.get("Idempotency-Key")
    if key is None or key == "":
        raise PocketfulError(400, "missing_idempotency_key", "Idempotency-Key header is required")
    key = str(key).strip()
    if not key or len(key) > 255:
        raise PocketfulError(422, "validation_failed", "Idempotency-Key must be 1 to 255 characters")
    return key


# ==============================================================================
# 1. Health & Test Endpoints
# ==============================================================================

@app.get("/health")
def get_health():
    """Health readiness check per §3.2."""
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.post("/_test/reset")
async def post_reset(request: Request):
    """Replaces state with fixture per §3.3."""
    body = await parse_json_body(request)
    ledger.reset(body)
    return Response(status_code=204)


@app.get("/_test/export")
def get_export():
    """Exports read-only snapshot per §10."""
    return JSONResponse(status_code=200, content=ledger.export_state())


@app.post("/_test/import")
async def post_import(request: Request):
    """Imports snapshot per §10."""
    body = await parse_json_body(request)
    ledger.import_state(body)
    return Response(status_code=204)


# ==============================================================================
# 2. Authentication Endpoints
# ==============================================================================

@app.post("/auth/signup")
async def post_signup(request: Request):
    """User registration per §6."""
    body = await parse_json_body(request)
    email = body.get("email")
    password = body.get("password")
    display_name = body.get("display_name")

    if not isinstance(email, str) or not isinstance(password, str) or not isinstance(display_name, str):
        raise PocketfulError(422, "validation_failed", "Missing required signup fields")

    res, _ = ledger.signup(email, password, display_name)
    return JSONResponse(status_code=201, content=res)


@app.post("/auth/login")
async def post_login(request: Request):
    """User authentication per §6."""
    body = await parse_json_body(request)
    email = body.get("email")
    password = body.get("password")

    if not isinstance(email, str) or not isinstance(password, str):
        raise PocketfulError(401, "unauthenticated", "Invalid credentials")

    res, _ = ledger.login(email, password)
    return JSONResponse(status_code=200, content=res)


# ==============================================================================
# 3. Core Wallet & Payment Endpoints
# ==============================================================================

@app.get("/me")
def get_me(request: Request):
    """Returns caller profile and wallet balance per §8."""
    caller = authenticate_user(request)
    meta = ledger.get_meta()
    # Re-fetch fresh balance
    conn = ledger._get_connection()
    try:
        row = conn.execute("SELECT balance FROM users WHERE id = ?;", (caller["id"],)).fetchone()
        bal = row["balance"] if row else caller["balance"]
    finally:
        conn.close()

    return JSONResponse(status_code=200, content={
        "user_id": caller["id"],
        "display_name": caller["display_name"],
        "handle": caller["handle"],
        "balance": bal,
        "currency": meta["currency"],
        "minor_units": meta["minor_units"]
    })


@app.post("/payments")
async def post_payments(request: Request):
    """Idempotent P2P transfer per §8."""
    caller = authenticate_user(request)
    idemp_key = validate_idempotency_header(request)
    body = await parse_json_body(request)
    canonical = canonicalize_json_body(body)
    bhash = compute_body_hash(canonical)

    # Check idempotency replay before business logic per §7
    cached = ledger.check_idempotency(caller["id"], idemp_key, "POST", "/payments", bhash)
    if cached is not None:
        status_code, resp_body = cached
        return JSONResponse(status_code=status_code, content=resp_body)

    to_handle = body.get("to_handle")
    amount = validate_amount(body.get("amount"))
    note = body.get("note", "")
    visibility = body.get("visibility", "public")

    res = ledger.create_payment(caller, to_handle, amount, note, visibility)
    ledger.record_idempotency(caller["id"], idemp_key, "POST", "/payments", bhash, 201, res)
    return JSONResponse(status_code=201, content=res)


@app.post("/requests")
async def post_requests(request: Request):
    """Idempotent money request creation per §8."""
    caller = authenticate_user(request)
    idemp_key = validate_idempotency_header(request)
    body = await parse_json_body(request)
    canonical = canonicalize_json_body(body)
    bhash = compute_body_hash(canonical)

    cached = ledger.check_idempotency(caller["id"], idemp_key, "POST", "/requests", bhash)
    if cached is not None:
        status_code, resp_body = cached
        return JSONResponse(status_code=status_code, content=resp_body)

    payer_handle = body.get("payer_handle")
    amount = validate_amount(body.get("amount"))
    note = body.get("note", "")

    res = ledger.create_request(caller, payer_handle, amount, note)
    ledger.record_idempotency(caller["id"], idemp_key, "POST", "/requests", bhash, 201, res)
    return JSONResponse(status_code=201, content=res)


@app.post("/requests/{request_id}/pay")
async def post_request_pay(request_id: str, request: Request):
    """Idempotent payment of a pending request per §8."""
    caller = authenticate_user(request)
    idemp_key = validate_idempotency_header(request)
    body = await parse_json_body(request)
    canonical = canonicalize_json_body(body)
    bhash = compute_body_hash(canonical)
    path = f"/requests/{request_id}/pay"

    cached = ledger.check_idempotency(caller["id"], idemp_key, "POST", path, bhash)
    if cached is not None:
        status_code, resp_body = cached
        return JSONResponse(status_code=status_code, content=resp_body)

    visibility = body.get("visibility", "public")
    res = ledger.pay_request(caller, request_id, visibility)
    ledger.record_idempotency(caller["id"], idemp_key, "POST", path, bhash, 201, res)
    return JSONResponse(status_code=201, content=res)


@app.post("/requests/{request_id}/decline")
async def post_request_decline(request_id: str, request: Request):
    """Declines a request per §8."""
    caller = authenticate_user(request)
    res = ledger.decline_request(caller, request_id)
    return JSONResponse(status_code=200, content=res)


@app.post("/requests/{request_id}/cancel")
async def post_request_cancel(request_id: str, request: Request):
    """Cancels a request per §8."""
    caller = authenticate_user(request)
    res = ledger.cancel_request(caller, request_id)
    return JSONResponse(status_code=200, content=res)


@app.get("/requests")
def get_requests(request: Request, direction: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0):
    """Lists requests involving the caller per §8."""
    caller = authenticate_user(request)
    items, has_more = ledger.list_requests(caller, direction, status, limit, offset)
    return JSONResponse(status_code=200, content={"requests": items, "has_more": has_more})


@app.post("/splits")
async def post_splits(request: Request):
    """Idempotent bill split per §8 & §9."""
    caller = authenticate_user(request)
    idemp_key = validate_idempotency_header(request)
    body = await parse_json_body(request)
    canonical = canonicalize_json_body(body)
    bhash = compute_body_hash(canonical)

    cached = ledger.check_idempotency(caller["id"], idemp_key, "POST", "/splits", bhash)
    if cached is not None:
        status_code, resp_body = cached
        return JSONResponse(status_code=status_code, content=resp_body)

    amount = validate_amount(body.get("amount"))
    handles = body.get("participant_handles")
    note = body.get("note", "")

    res = ledger.create_split(caller, amount, handles, note)
    ledger.record_idempotency(caller["id"], idemp_key, "POST", "/splits", bhash, 201, res)
    return JSONResponse(status_code=201, content=res)


@app.get("/activity")
def get_activity(request: Request, limit: int = 50, offset: int = 0):
    """Activity feed query per §4 & §8."""
    caller = authenticate_user(request)
    items, has_more = ledger.list_activity(caller, limit, offset)
    return JSONResponse(status_code=200, content={"payments": items, "has_more": has_more})


@app.post("/settlements")
async def post_settlements(request: Request):
    """Idempotent multi-party net settlement per §11."""
    caller = authenticate_user(request)
    idemp_key = validate_idempotency_header(request)
    body = await parse_json_body(request)
    canonical = canonicalize_json_body(body)
    bhash = compute_body_hash(canonical)

    cached = ledger.check_idempotency(caller["id"], idemp_key, "POST", "/settlements", bhash)
    if cached is not None:
        status_code, resp_body = cached
        return JSONResponse(status_code=status_code, content=resp_body)

    transfers = body.get("transfers")
    res = ledger.create_settlement(caller, transfers)
    ledger.record_idempotency(caller["id"], idemp_key, "POST", "/settlements", bhash, 201, res)
    return JSONResponse(status_code=201, content=res)
