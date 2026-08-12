"""예금(현금) CRUD API (스펙 7 api/deposits.py)."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from ..repositories import cash_ledger as cash_ledger_repo
from ..repositories import deposits as deposits_repo
from ..repositories.cash_ledger import CashLedgerValidationError
from .deps import get_conn, require_api_key
from .schemas import DepositCreate, DepositUpdate

router = APIRouter(prefix="/api/v1/deposits", tags=["deposits"], dependencies=[Depends(require_api_key)])


@router.post("", status_code=201)
def create_deposit(body: DepositCreate, conn: sqlite3.Connection = Depends(get_conn)):
    return deposits_repo.create_deposit(conn, account_name=body.account_name, amount=body.amount, currency=body.currency)


@router.get("")
def list_deposits(conn: sqlite3.Connection = Depends(get_conn)):
    return deposits_repo.list_deposits(conn)


@router.patch("/{deposit_id}")
def update_deposit(deposit_id: str, body: DepositUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    # v1.7(2026-08-12, 이자소득) -- process_interest=true면 잔액 갱신 전에
    # (새 amount - 기존 amount)를 cash_ledger에 INTEREST_INCOME으로 먼저
    # 기록한다. 발행어음/CMA/RP처럼 매일 이자가 붙는 계좌를 수정할 때 체크.
    # get_conn이 요청 끝에 한 번에 commit하므로(api/deps.py) 이 기록과
    # 잔액 갱신은 같은 트랜잭션으로 묶여 원자적이다.
    if body.process_interest:
        current = deposits_repo.get_deposit(conn, deposit_id)
        if current is None:
            raise HTTPException(status_code=404, detail="deposit not found")
        if body.amount is None:
            raise HTTPException(status_code=400, detail="process_interest는 amount와 함께 보내야 합니다.")
        diff = body.amount - current["amount"]
        if diff <= 0:
            raise HTTPException(
                status_code=400,
                detail="이자처리는 잔액이 늘어난 경우에만 가능합니다(새 금액이 기존 금액보다 커야 함).",
            )
        try:
            cash_ledger_repo.create_entry(
                conn,
                occurred_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                deposit_account_id=deposit_id,
                entry_type="INTEREST_INCOME",
                amount=diff,
                currency=body.currency or current["currency"],
                memo="계좌 수정 시 자동 기록(이자처리)",
            )
        except CashLedgerValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    updated = deposits_repo.update_deposit(
        conn, deposit_id, account_name=body.account_name, amount=body.amount, currency=body.currency
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="deposit not found")
    return updated


@router.delete("/{deposit_id}", status_code=204)
def delete_deposit(deposit_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not deposits_repo.delete_deposit(conn, deposit_id):
        raise HTTPException(status_code=404, detail="deposit not found")
    return None
