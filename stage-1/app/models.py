"""
Pocketful Data Models & Helpers.
Compliant with Pocketful Stage 1 specification.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from fastapi.responses import JSONResponse


def utc_now_iso() -> str:
    """Returns current UTC timestamp in strict RFC 3339 format with +00:00 offset."""
    return datetime.now(timezone.utc).isoformat()


def make_error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Builds the uniform error envelope required by §5."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        media_type="application/json; charset=utf-8"
    )


class PocketfulError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"[{status_code}] {code}: {message}")


def derive_handle_from_email(email: str) -> str:
    """
    Derives unique handle from email local-part per §4 & §6:
    Take local part, lowercase it, replace every character outside [a-z0-9_] with '_',
    and truncate to 20 characters.
    """
    if "@" not in email:
        raise PocketfulError(422, "validation_failed", "Invalid email format")
    local_part = email.split("@", 1)[0].lower()
    clean_handle = re.sub(r"[^a-z0-9_]", "_", local_part)
    clean_handle = clean_handle[:20]
    if not clean_handle:
        clean_handle = "user"
    return clean_handle


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Secure PBKDF2-HMAC-SHA256 password hasher with salt."""
    if salt is None:
        salt = hashlib.sha256(password.encode("utf-8") + b":salt:pocketful").hexdigest()[:16]
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}:{key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a password against the stored salt:hash string."""
    try:
        salt, key_hex = stored_hash.split(":", 1)
        expected_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
        return hmac.compare_digest(key_hex, expected_key.hex())
    except Exception:
        return False


def canonicalize_json_body(body_dict: Any) -> str:
    """Produces a deterministic, canonical JSON string for idempotency checking."""
    return json.dumps(body_dict, sort_keys=True, separators=(",", ":"))


def compute_body_hash(canonical_body: str) -> str:
    """Computes SHA-256 hash of canonical request body."""
    return hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()


def equal_split(amount: int, n: int) -> List[int]:
    """
    Equal-split rule per §9:
    Shares are whole minor units, sum exactly to amount, and differ by at most one unit.
    When amount does not divide evenly, larger shares go to the first participants in input order.
    """
    if n <= 0:
        return []
    base = amount // n if amount >= 0 else -((-amount) // n)
    remainder = amount - base * n
    return [base + (1 if i < remainder else 0) for i in range(n)]


def validate_amount(amount: Any) -> int:
    """
    Validates and converts an amount to an integer minor-unit per §4 & §8:
    API amounts must have an integral numeric value: JSON 1000, 1000.0 and 1e3
    all represent the same valid minor-unit amount. Booleans and strings are not numbers.
    Must be between 1 and 1,000,000,000.
    """
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise PocketfulError(422, "validation_failed", "Amount must be a numeric integer")
    try:
        f = float(amount)
        if not f.is_integer():
            raise PocketfulError(422, "validation_failed", "Amount must be an integer value")
        amt = int(f)
        if amt < 1 or amt > 1_000_000_000:
            raise PocketfulError(422, "validation_failed", "Amount must be between 1 and 1,000,000,000")
        return amt
    except (OverflowError, ValueError):
        raise PocketfulError(422, "validation_failed", "Amount overflow or invalid")


def validate_handle(handle: Any, field_name: str = "handle") -> str:
    """Validates handle matching ^[a-z0-9_]{1,20}$ per §4."""
    if not isinstance(handle, str) or not re.match(r"^[a-z0-9_]{1,20}$", handle):
        raise PocketfulError(422, "validation_failed", f"Invalid {field_name} format")
    return handle
