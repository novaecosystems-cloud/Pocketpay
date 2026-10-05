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


def money_format(minor: int, minor_units: int = 2, currency: str = "EUR") -> str:
    """Format minor units as human-readable string: 100.00 EUR or 1200 JPY."""
    if minor_units == 0:
        return f"{minor} {currency}"
    text = str(minor).rjust(minor_units + 1, "0")
    return f"{text[:-minor_units]}.{text[-minor_units:]} {currency}"


def parse_decimal_to_minor(text: str, minor_units: int = 2) -> int:
    """
    Parses a human typed decimal string (e.g. '15.00', '15.5', '15') to integer minor units.
    Rejects inputs with more decimal places than minor_units (e.g. '15.005').
    Rejects negative or non-numeric inputs.
    """
    text = str(text).strip()
    if not text:
        raise PocketfulError(422, "validation_failed", "Amount cannot be empty")
    if minor_units == 0:
        if "." in text:
            raise PocketfulError(422, "validation_failed", "Zero-decimal currency does not permit decimal points")
        if not text.isdigit():
            raise PocketfulError(422, "validation_failed", "Amount must be a whole number")
        val = int(text)
        if val < 1:
            raise PocketfulError(422, "validation_failed", "Amount must be positive")
        return val

    # minor_units > 0
    if "." in text:
        parts = text.split(".")
        if len(parts) != 2:
            raise PocketfulError(422, "validation_failed", "Malformed decimal amount")
        whole, frac = parts[0], parts[1]
        if not whole.isdigit() or not frac.isdigit():
            raise PocketfulError(422, "validation_failed", "Amount contains non-digits")
        if len(frac) > minor_units:
            raise PocketfulError(422, "validation_failed", f"Too many decimal places for minor_units {minor_units}")
        frac_padded = frac.ljust(minor_units, "0")
        val = int(whole) * (10 ** minor_units) + int(frac_padded)
    else:
        if not text.isdigit():
            raise PocketfulError(422, "validation_failed", "Amount contains non-digits")
        val = int(text) * (10 ** minor_units)

    if val < 1:
        raise PocketfulError(422, "validation_failed", "Amount must be positive")
    return val


def parse_rfc3339(val: Any, field_name: str = "timestamp") -> datetime:
    """
    Parses and validates strict RFC 3339 instant with timezone offset.
    Rejects naive timestamps, empty values, or malformed strings with 422 validation_failed.
    """
    if not isinstance(val, str) or not val.strip():
        raise PocketfulError(422, "validation_failed", f"Empty or non-string {field_name}")
    val = val.strip()
    try:
        dt = datetime.fromisoformat(val)
    except Exception:
        raise PocketfulError(422, "validation_failed", f"Invalid RFC 3339 format for {field_name}")
    if dt.tzinfo is None:
        raise PocketfulError(422, "validation_failed", f"{field_name} must include a timezone offset")
    return dt


def validate_correction_body(body: Dict[str, Any]) -> Tuple[int, int, str, str]:
    """Validates payment correction payload per Stage 3 specification."""
    if not isinstance(body, dict):
        raise PocketfulError(422, "validation_failed", "Body must be a JSON object")

    for f in ("expected_revision", "amount", "effective_at", "reason"):
        if f not in body:
            raise PocketfulError(422, "validation_failed", f"Missing required field '{f}'")

    exp_rev = body.get("expected_revision")
    if isinstance(exp_rev, bool) or not isinstance(exp_rev, int) or exp_rev < 1:
        raise PocketfulError(422, "validation_failed", "expected_revision must be a positive integer")

    amt_raw = body.get("amount")
    if isinstance(amt_raw, bool) or not isinstance(amt_raw, (int, float)):
        raise PocketfulError(422, "validation_failed", "amount must be numeric")
    try:
        f_amt = float(amt_raw)
        if not f_amt.is_integer():
            raise PocketfulError(422, "validation_failed", "amount must be an integer")
        amt = int(f_amt)
        if amt < 0 or amt > 1_000_000_000:
            raise PocketfulError(422, "validation_failed", "amount must be between 0 and 1,000,000,000")
    except (ValueError, OverflowError):
        raise PocketfulError(422, "validation_failed", "amount overflow or invalid")

    eff_at_raw = body.get("effective_at")
    eff_dt = parse_rfc3339(eff_at_raw, "effective_at")
    now_dt = datetime.now(timezone.utc)
    if eff_dt > now_dt:
        raise PocketfulError(422, "validation_failed", "effective_at cannot be in the future")

    reason_raw = body.get("reason")
    if not isinstance(reason_raw, str) or len(reason_raw) < 1 or len(reason_raw) > 200:
        raise PocketfulError(422, "validation_failed", "reason must be a string between 1 and 200 characters")

    return exp_rev, amt, eff_at_raw, reason_raw

