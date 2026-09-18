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
        return {
            "account_id": acc["id"],
            "name": acc["name"],
            "type": acc["type"],
            "balance_cents": acc["balance_cents"],
            "balance_formatted": f"${acc['balance_cents'] / 100:.2f}",
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


@app.get("/api/v1/accounts/{account_id}/statement")
def get_statement(account_id: str):
    try:
        return service.get_account_statement(account_id)
    except AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/v1/audit")
def run_audit():
    return service.run_full_ledger_audit()
