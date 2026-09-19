"""
Pocketpay REST API Interface.
Built with FastAPI to expose banking-grade double-entry wallet operations,
idempotency key protection, and system-wide ledger audit checks.
"""

from typing import Optional
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from src.service import PocketfulService
from src.ledger_engine import (
    AccountNotFoundError,
    IdempotencyConflictError,
    InsufficientFundsError,
    InvalidTransactionError,
)

app = FastAPI(
    title="Pocketpay Banking Ledger API",
    description="Concurrency-safe double-entry wallet engine for WeAreDevelopers x BAND Dark Factory",
    version="1.0.0"
)

service = PocketfulService()


# Request / Response Schemas
class CreateAccountRequest(BaseModel):
    account_id: str = Field(..., example="alice")
    name: str = Field(..., example="Alice Smith")
    initial_balance_cents: int = Field(0, ge=0, example=10000)


class DepositRequest(BaseModel):
    amount_cents: int = Field(..., gt=0, example=5000)
    description: str = Field("Deposit", example="Bank load")


class TransferRequest(BaseModel):
    from_account_id: str = Field(..., example="alice")
    to_account_id: str = Field(..., example="bob")
    amount_cents: int = Field(..., gt=0, example=2500)
    description: str = Field("P2P Transfer", example="Dinner split")


class BatchTransferItem(BaseModel):
    from_account_id: str
    to_account_id: str
    amount_cents: int = Field(..., gt=0)
    description: Optional[str] = None


class BatchTransferRequest(BaseModel):
    transfers: list[BatchTransferItem]
    description: Optional[str] = "Batch Vectorized Transfer"


class CreateHoldRequest(BaseModel):
    from_account_id: str = Field(..., example="alice")
    to_account_id: str = Field(..., example="hotel_chain")
    amount_cents: int = Field(..., gt=0, example=3500)
    timeout_seconds: Optional[int] = Field(None, example=3600)
    description: str = Field("Pre-auth hold", example="Hotel room deposit")


class VoidHoldRequest(BaseModel):
    reason: Optional[str] = Field("Reservation cancelled", example="Customer cancel")


@app.post("/api/v1/accounts", status_code=status.HTTP_201_CREATED)
def create_account(req: CreateAccountRequest):
    try:
        return service.create_account(
            account_id=req.account_id,
            name=req.name,
            initial_balance_cents=req.initial_balance_cents
        )
    except InvalidTransactionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/v1/accounts/{account_id}")
def get_account(account_id: str):
    try:
        acc = service.get_account(account_id)
        balance = acc["balance_cents"]
        pending = acc.get("pending_debit_cents", 0)
        avail = balance - pending
        return {
            "account_id": acc["id"],
            "name": acc["name"],
            "type": acc["type"],
            "balance_cents": balance,
            "balance_formatted": f"${balance / 100:.2f}",
            "pending_debit_cents": pending,
            "available_balance_cents": avail,
            "available_balance_formatted": f"${avail / 100:.2f}",
            "created_at": acc["created_at"]
        }
    except AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/api/v1/accounts/{account_id}/deposit")
def deposit(account_id: str, req: DepositRequest, idempotency_key: Optional[str] = Header(None)):
    try:
        res = service.deposit(
            account_id=account_id,
            amount_cents=req.amount_cents,
            idempotency_key=idempotency_key,
            description=req.description
        )
        return res
    except AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except IdempotencyConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except InvalidTransactionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/v1/transfers")
def transfer(req: TransferRequest, idempotency_key: Optional[str] = Header(None)):
    try:
        res = service.transfer(
            from_account_id=req.from_account_id,
            to_account_id=req.to_account_id,
            amount_cents=req.amount_cents,
            idempotency_key=idempotency_key,
            description=req.description
        )
        return res
    except AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InsufficientFundsError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except IdempotencyConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except InvalidTransactionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/v1/transfers/batch")
def transfer_batch(req: BatchTransferRequest):
    try:
        dict_transfers = [t.model_dump() for t in req.transfers]
        return service.transfer_batch(dict_transfers, description=req.description)
    except AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InsufficientFundsError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except InvalidTransactionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/v1/holds", status_code=status.HTTP_201_CREATED)
def create_hold(req: CreateHoldRequest):
    try:
        return service.authorize_hold(
            from_account_id=req.from_account_id,
            to_account_id=req.to_account_id,
            amount_cents=req.amount_cents,
            timeout_seconds=req.timeout_seconds,
            description=req.description,
        )
    except AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InsufficientFundsError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except InvalidTransactionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/v1/holds/{hold_id}/capture")
def capture_hold(hold_id: str):
    try:
        return service.capture_hold(hold_id)
    except InvalidTransactionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/api/v1/holds/{hold_id}/void")
def void_hold(hold_id: str, req: Optional[VoidHoldRequest] = None):
    try:
        reason = req.reason if req else None
        return service.void_hold(hold_id, reason=reason)
    except InvalidTransactionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.get("/api/v1/accounts/{account_id}/statement")
def get_statement(account_id: str):
    try:
        return service.get_account_statement(account_id)
    except AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/v1/audit")
def run_audit():
    return service.run_full_ledger_audit()

