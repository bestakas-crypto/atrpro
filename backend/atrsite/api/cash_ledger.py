"""backend/atrsite/api/cash_ledger.py -- 입출금 통합 장부(v1.5) CRUD + 요약 API.

옛 /api/v1/withdrawals + /api/v1/cash-inflows를 대체. URL 프리픽스/PATCH
관례는 그대로 유지(/api/v1/<resource>). /summary는 반드시 /{entry_id}보다
먼저 등록해야 한다(정적 경로 우선 -- withdrawals.py와 동일한 이유).
"""
from __future__ import annotations

import csv
import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..repositories import cash_ledger as cash_ledger_repo
from ..repositories.cash_ledger import CashLedgerFilter, CashLedgerValidationError
from .deps import get_conn, require_api_key
from .schemas import CashLedgerEntryCreate, CashLedgerEntryUpdate

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_ENTRY_TYPE_LABEL = {
    "EXTERNAL_IN": "외부입금",
    "EXTERNAL_OUT": "소비출금",
    "INTERNAL_IN": "내부이체입금",
    "INTERNAL_OUT": "내부이체출금",
    "INTEREST_INCOME": "이자소득",
}


def _csv_safe(value: str) -> str:
    if value and value[0] in _CSV_FORMULA_PREFIXES:
        return "\t" + value
    return value

router = APIRouter(prefix="/api/v1/cash-ledger", tags=["cash-ledger"], dependencies=[Depends(require_api_key)])


def _filter_from_query(
    start_date: str | None, end_date: str | None, deposit_account_id: str | None,
    currency: str | None, entry_type: str | None,
) -> CashLedgerFilter:
    return CashLedgerFilter(
        start_date=start_date, end_date=end_date,
        deposit_account_id=deposit_account_id, currency=currency, entry_type=entry_type,
    )


@router.get("/summary")
def get_summary(conn: sqlite3.Connection = Depends(get_conn)):
    """오늘/이번주/이번달/YTD, 통화별 {in, out, net}."""
    return cash_ledger_repo.period_summary(conn)


@router.get("/export.csv")
def export_csv(
    start_date: str | None = None,
    end_date: str | None = None,
    deposit_account_id: str | None = None,
    currency: str | None = None,
    entry_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    f = _filter_from_query(start_date, end_date, deposit_account_id, currency, entry_type)
    rows = cash_ledger_repo.list_all_matching(conn, f)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["일시", "계좌", "구분", "금액", "통화", "메모"])
    for r in rows:
        amount_str = f"{r['amount']:.0f}" if r["currency"] in ("KRW", "JPY") else f"{r['amount']:.2f}"
        writer.writerow([
            _csv_safe(r["occurred_at"].replace("T", " ")),
            _csv_safe(r["account_name_snapshot"]),
            _ENTRY_TYPE_LABEL.get(r["entry_type"], r["entry_type"]),
            amount_str,
            r["currency"],
            _csv_safe(r["memo"] or ""),
        ])

    csv_bytes = "﻿" + buf.getvalue()
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=cash_ledger.csv"},
    )


@router.get("")
def list_entries(
    start_date: str | None = None,
    end_date: str | None = None,
    deposit_account_id: str | None = None,
    currency: str | None = None,
    entry_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="occurred_at_desc"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    f = _filter_from_query(start_date, end_date, deposit_account_id, currency, entry_type)
    sort_desc = sort != "occurred_at_asc"
    items, total = cash_ledger_repo.list_entries(conn, f, limit=limit, offset=offset, sort_desc=sort_desc)
    return {
        "items": items,
        "total": total,
        "sum_by_currency": cash_ledger_repo.sum_by_currency(conn, f),
    }


@router.post("", status_code=201)
def create_entry(body: CashLedgerEntryCreate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return cash_ledger_repo.create_entry(
            conn,
            occurred_at=body.occurred_at,
            deposit_account_id=body.deposit_account_id,
            entry_type=body.entry_type,
            amount=body.amount,
            currency=body.currency,
            memo=body.memo,
        )
    except CashLedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{entry_id}")
def get_entry(entry_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    result = cash_ledger_repo.get_entry(conn, entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail="cash ledger entry not found")
    return result


@router.patch("/{entry_id}")
def update_entry(entry_id: str, body: CashLedgerEntryUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        updated = cash_ledger_repo.update_entry(
            conn,
            entry_id,
            occurred_at=body.occurred_at,
            deposit_account_id=body.deposit_account_id,
            entry_type=body.entry_type,
            amount=body.amount,
            currency=body.currency,
            memo=body.memo,
        )
    except CashLedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="cash ledger entry not found")
    return updated


@router.delete("/{entry_id}", status_code=204)
def delete_entry(entry_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not cash_ledger_repo.delete_entry(conn, entry_id):
        raise HTTPException(status_code=404, detail="cash ledger entry not found")
    return None
