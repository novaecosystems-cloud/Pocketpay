"""
Pocketful Stage 2 FastAPI Application.
Integrates full HTTP API with Server-Side Rendered (SSR) HTML consumer interfaces
and Two-Phase Payment Authorizations (Holds & Captures).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.ledger import LedgerEngine
from app.models import (
    PocketfulError,
    canonicalize_json_body,
    compute_body_hash,
    make_error_response,
    validate_amount,
)
from app import ui

app = FastAPI(title="Pocketful Stage 2", version="2.0.0")
ledger = LedgerEngine()


@app.exception_handler(PocketfulError)
async def pocketful_error_handler(request: Request, exc: PocketfulError):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and request.method == "GET":
        # Render error page if needed
        return HTMLResponse(status_code=exc.status_code, content=f"<h2>Error {exc.status_code}</h2><p>{exc.message}</p>")
    return make_error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return make_error_response(422, "validation_failed", str(exc))


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    return make_error_response(500, "internal_error", str(exc))


# ==============================================================================
# Auth & Body Helpers
# ==============================================================================

def get_session_token(request: Request) -> Optional[str]:
    auth_hdr = request.headers.get("authorization", "")
    if auth_hdr.lower().startswith("bearer "):
        return auth_hdr[7:].strip()
    cookie_token = request.cookies.get("token")
    if cookie_token:
        return cookie_token.strip()
    return None


def authenticate_user(request: Request) -> Dict[str, Any]:
    token = get_session_token(request)
    if not token:
        raise PocketfulError(401, "unauthenticated", "Bearer token or session cookie required")
    user = ledger.get_user_by_token(token)
    if not user:
        raise PocketfulError(401, "unauthenticated", "Invalid session token")
    return user


def try_authenticate_user(request: Request) -> Optional[Dict[str, Any]]:
    token = get_session_token(request)
    if not token:
        return None
    return ledger.get_user_by_token(token)


def validate_idempotency_header(request: Request) -> str:
    key = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    if key is None or key == "":
        raise PocketfulError(400, "missing_idempotency_key", "Valid Idempotency-Key header is required")
    key = str(key).strip()
    if not key or len(key) > 255:
        raise PocketfulError(422, "validation_failed", "Idempotency-Key must be 1 to 255 characters")
    return key


async def parse_json_body(request: Request) -> Dict[str, Any]:
    try:
        raw_body = await request.body()
        if not raw_body:
            return {}
        data = json.loads(raw_body.decode("utf-8"))
        if not isinstance(data, dict):
            raise PocketfulError(422, "validation_failed", "Request body must be a JSON object")
        return data
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise PocketfulError(400, "malformed_request", "Failed to parse JSON body")


# ==============================================================================
# System & Testing Endpoints (§3)
# ==============================================================================

@app.get("/health")
def get_health():
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.post("/_test/reset")
async def post_test_reset(request: Request):
    body = await parse_json_body(request)
    ledger.reset_fixture(body)
    return Response(status_code=204)


@app.get("/_test/export")
def get_test_export():
    return JSONResponse(status_code=200, content=ledger.export_state())


@app.post("/_test/import")
async def post_test_import(request: Request):
    body = await parse_json_body(request)
    ledger.import_state(body)
    return Response(status_code=204)


# ==============================================================================
# UI Web Routes (SSR HTML)
# ==============================================================================

@app.get("/login", response_class=HTMLResponse)
def get_login(request: Request):
    return HTMLResponse(ui.render_login())


@app.post("/auth/login")
@app.post("/login")
async def post_login_form(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type or not content_type:
        body = await parse_json_body(request)
        if body or "application/json" in content_type:
            email = body.get("email")
            password = body.get("password")
            if not isinstance(email, str) or not isinstance(password, str):
                raise PocketfulError(401, "unauthenticated", "Invalid credentials")
            res, token = ledger.login(email, password)
            resp = JSONResponse(status_code=200, content=res)
            resp.set_cookie("token", token, path="/", samesite="lax")
            return resp

    # Form submission
    form = await request.form()
    email = str(form.get("email", ""))
    password = str(form.get("password", ""))
    try:
        res, token = ledger.login(email, password)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("token", token, path="/", samesite="lax")
        return resp
    except PocketfulError as e:
        return HTMLResponse(ui.render_login(error=e.message), status_code=401)


@app.get("/signup", response_class=HTMLResponse)
def get_signup(request: Request):
    return HTMLResponse(ui.render_signup())


@app.post("/auth/signup")
@app.post("/signup")
async def post_signup_form(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type or not content_type:
        body = await parse_json_body(request)
        if body or "application/json" in content_type:
            email = body.get("email")
            password = body.get("password")
            display_name = body.get("display_name")
            if not isinstance(email, str) or not isinstance(password, str) or not isinstance(display_name, str):
                raise PocketfulError(422, "validation_failed", "Missing required signup fields")
            res, token = ledger.signup(email, password, display_name)
            resp = JSONResponse(status_code=201, content=res)
            resp.set_cookie("token", token, path="/", samesite="lax")
            return resp

    form = await request.form()
    email = str(form.get("email", ""))
    password = str(form.get("password", ""))
    display_name = str(form.get("display_name", ""))
    try:
        res, token = ledger.signup(email, password, display_name)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("token", token, path="/", samesite="lax")
        return resp
    except PocketfulError as e:
        return HTMLResponse(ui.render_signup(error=e.message), status_code=e.status_code)


@app.get("/", response_class=HTMLResponse)
def get_root_dashboard(request: Request):
    user = try_authenticate_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    meta = ledger.get_meta()
    now = compute_body_hash(user["id"]) # dummy
    from app.models import utc_now_iso
    now_iso = utc_now_iso()

    conn = ledger._get_connection()
    try:
        total, available, held = ledger.get_user_balances(conn, user["id"], now_iso)
    finally:
        conn.close()

    payments, _ = ledger.list_activity(user, limit=50, offset=0)
    html = ui.render_dashboard(
        user=user,
        total=total,
        available=available,
        held=held,
        currency=meta["currency"],
        minor_units=meta["minor_units"],
        payments=payments
    )
    return HTMLResponse(html)


@app.get("/split", response_class=HTMLResponse)
def get_split_page(request: Request):
    user = try_authenticate_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    meta = ledger.get_meta()
    return HTMLResponse(ui.render_split(user, meta["currency"], meta["minor_units"]))


# ==============================================================================
# Shared UI & JSON Endpoints (§8 & Stage 2)
# ==============================================================================

@app.get("/requests")
def get_requests(request: Request, direction: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        user = try_authenticate_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        meta = ledger.get_meta()
        inc, _ = ledger.list_requests(user, direction="incoming", status=None, limit=50, offset=0)
        out, _ = ledger.list_requests(user, direction="outgoing", status=None, limit=50, offset=0)
        return HTMLResponse(ui.render_requests(user, inc, out, meta["currency"], meta["minor_units"]))

    # JSON API response
    caller = authenticate_user(request)
    items, has_more = ledger.list_requests(caller, direction, status, limit, offset)
    return JSONResponse(status_code=200, content={"requests": items, "has_more": has_more})


@app.get("/authorizations")
def get_authorizations(request: Request, direction: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        user = try_authenticate_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        meta = ledger.get_meta()
        items, _ = ledger.list_authorizations(user, direction=None, status=None, limit=50, offset=0)
        return HTMLResponse(ui.render_authorizations(user, items, meta["currency"], meta["minor_units"]))

    caller = authenticate_user(request)
    items, has_more = ledger.list_authorizations(caller, direction, status, limit, offset)
    return JSONResponse(status_code=200, content={"authorizations": items, "has_more": has_more})


# ==============================================================================
# Core Wallet & Payments Endpoints (§8)
# ==============================================================================

@app.get("/me")
def get_me(request: Request):
    caller = authenticate_user(request)
    meta = ledger.get_meta()
    from app.models import utc_now_iso
    now_iso = utc_now_iso()

    conn = ledger._get_connection()
    try:
        total, available, held = ledger.get_user_balances(conn, caller["id"], now_iso)
    finally:
        conn.close()

    return JSONResponse(status_code=200, content={
        "user_id": caller["id"],
        "display_name": caller["display_name"],
        "handle": caller["handle"],
        "balance": total,
        "total": total,
        "available": available,
        "held": held,
        "currency": meta["currency"],
        "minor_units": meta["minor_units"]
    })


@app.post("/payments")
async def post_payments(request: Request):
    caller = authenticate_user(request)
    idemp_key = validate_idempotency_header(request)
    body = await parse_json_body(request)
    canonical = canonicalize_json_body(body)
    bhash = compute_body_hash(canonical)

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
    caller = authenticate_user(request)
    res = ledger.decline_request(caller, request_id)
    return JSONResponse(status_code=200, content=res)


@app.post("/requests/{request_id}/cancel")
async def post_request_cancel(request_id: str, request: Request):
    caller = authenticate_user(request)
    res = ledger.cancel_request(caller, request_id)
    return JSONResponse(status_code=200, content=res)


@app.post("/splits")
async def post_splits(request: Request):
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
    caller = authenticate_user(request)
    items, has_more = ledger.list_activity(caller, limit, offset)
    return JSONResponse(status_code=200, content={"payments": items, "has_more": has_more})


@app.post("/settlements")
async def post_settlements(request: Request):
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


# ==============================================================================
# Stage 2: Payment Authorizations Endpoints
# ==============================================================================

@app.post("/authorizations")
async def post_authorizations(request: Request):
    caller = authenticate_user(request)
    idemp_key = validate_idempotency_header(request)
    body = await parse_json_body(request)
    canonical = canonicalize_json_body(body)
    bhash = compute_body_hash(canonical)

    cached = ledger.check_idempotency(caller["id"], idemp_key, "POST", "/authorizations", bhash)
    if cached is not None:
        status_code, resp_body = cached
        return JSONResponse(status_code=status_code, content=resp_body)

    to_handle = body.get("to_handle")
    amount = body.get("amount")
    note = body.get("note", "")
    visibility = body.get("visibility", "public")

    res = ledger.create_authorization(caller, to_handle, amount, note, visibility)
    ledger.record_idempotency(caller["id"], idemp_key, "POST", "/authorizations", bhash, 201, res)
    return JSONResponse(status_code=201, content=res)


@app.post("/authorizations/{authorization_id}/capture")
async def post_authorization_capture(authorization_id: str, request: Request):
    caller = authenticate_user(request)
    idemp_key = validate_idempotency_header(request)
    body = await parse_json_body(request)
    canonical = canonicalize_json_body(body)
    bhash = compute_body_hash(canonical)
    path = f"/authorizations/{authorization_id}/capture"

    cached = ledger.check_idempotency(caller["id"], idemp_key, "POST", path, bhash)
    if cached is not None:
        status_code, resp_body = cached
        return JSONResponse(status_code=status_code, content=resp_body)

    amount = body.get("amount")
    final = body.get("final", True)
    if not isinstance(final, bool):
        raise PocketfulError(422, "validation_failed", "final must be a boolean")

    res = ledger.capture_authorization(caller, authorization_id, amount=amount, final=final)
    ledger.record_idempotency(caller["id"], idemp_key, "POST", path, bhash, 201, res)
    return JSONResponse(status_code=201, content=res)


@app.post("/authorizations/{authorization_id}/void")
async def post_authorization_void(authorization_id: str, request: Request):
    caller = authenticate_user(request)
    res = ledger.void_authorization(caller, authorization_id)
    return JSONResponse(status_code=200, content=res)
