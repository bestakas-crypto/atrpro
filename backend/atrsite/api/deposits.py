"""예금(현금) CRUD API (스펙 7 api/deposits.py)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..repositories import deposits as deposits_repo
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
