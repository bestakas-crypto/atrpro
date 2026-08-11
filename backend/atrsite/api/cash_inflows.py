"""backend/atrsite/api/cash_inflows.py -- 현금 입금기록(v1.4) CRUD + 합산 API.

withdrawals.py와 동일한 관례를 그대로 따른다: URL 프리픽스 /api/v1/<resource>,
부분수정은 PATCH, /summary와 /sources는 반드시 /{inflow_id}보다 먼저 등록.
"""
from __future__ import annotations

import csv
import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..repositories import cash_inflows as cash_inflows_repo
from ..repositories.cash_inflows import CashInflowFilter, CashInflowValidationError
from .deps import get_conn, require_api_key
from .schemas import CashInflowCreate, CashInflowUpdate

# withdrawals.py와 동일한 CSV 수식 삽입 방지.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    if value and value[0] in _CSV_FORMULA_PREFIXES:
        return "\t" + value
    return value

router = APIRouter(prefix="/api/v1/cash-inflows", tags=["cash-inflows"], dependencies=[Depends(require_api_key)])


def _filter_from_query(
    start_date: str | None, end_date: str | None, source: str | None,
    deposit_account_id: str | None, currency: str | None, flow_type: str | None,
) -> CashInflowFilter:
    return CashInflowFilter(
        start_date=start_date, end_date=end_date, source=source,
        deposit_account_id=deposit_account_id, currency=currency, flow_type=flow_type,
    )


@router.get("/summary")
def get_summary(conn: sqlite3.Connection = Depends(get_conn)):
    """오늘/이번주/이번달/YTD, 통화별 합산(환율 환산 없음)."""
    return cash_inflows_repo.period_summary(conn)


@router.get("/sources")
def get_recent_sources(conn: sqlite3.Connection = Depends(get_conn)):
    """최근/자주 쓴 출처 추천 목록."""
    return {"sources": cash_inflows_repo.recent_sources(conn)}


@router.get("/export.csv")
def export_csv(
    start_date: str | None = None,
    end_date: str | None = None,
    source: str | None = None,
    deposit_account_id: str | None = None,
    currency: str | None = None,
    flow_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """현재 필터 조건 그대로 CSV 내보내기. 내부 식별자/API 키는 포함하지 않는다."""
    f = _filter_from_query(start_date, end_date, source, deposit_account_id, currency, flow_type)
    rows = cash_inflows_repo.list_all_matching(conn, f)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["입금일시", "입금계좌", "출처", "금액", "통화", "구분", "메모"])
    for r in rows:
        amount_str = f"{r['amount']:.0f}" if r["currency"] in ("KRW", "JPY") else f"{r['amount']:.2f}"
        writer.writerow([
            _csv_safe(r["deposited_at"].replace("T", " ")),
            _csv_safe(r["account_name_snapshot"]),
            _csv_safe(r["source"]),
            amount_str,
            r["currency"],
            r["flow_type"],
            _csv_safe(r["memo"] or ""),
        ])

    csv_bytes = "﻿" + buf.getvalue()
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=cash_inflows.csv"},
    )


@router.get("")
def list_cash_inflows(
    start_date: str | None = None,
    end_date: str | None = None,
    source: str | None = None,
    deposit_account_id: str | None = None,
    currency: str | None = None,
    flow_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="deposited_at_desc"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    f = _filter_from_query(start_date, end_date, source, deposit_account_id, currency, flow_type)
    sort_desc = sort != "deposited_at_asc"
    items, total = cash_inflows_repo.list_cash_inflows(conn, f, limit=limit, offset=offset, sort_desc=sort_desc)
    return {
        "items": items,
        "total": total,
        "sum_by_currency": cash_inflows_repo.sum_by_currency(conn, f),
        "sum_by_account": cash_inflows_repo.sum_by_account(conn, f),
    }


@router.post("", status_code=201)
def create_cash_inflow(body: CashInflowCreate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return cash_inflows_repo.create_cash_inflow(
            conn,
            deposited_at=body.deposited_at,
            deposit_account_id=body.deposit_account_id,
            source=body.source,
            amount=body.amount,
            currency=body.currency,
            flow_type=body.flow_type,
            memo=body.memo,
        )
    except CashInflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/check-duplicate")
def check_duplicate(body: CashInflowCreate, conn: sqlite3.Connection = Depends(get_conn)):
    """저장 전 미리보기용. 실제 저장을 막지 않고 중복 가능성만 알려준다."""
    dup = cash_inflows_repo.find_possible_duplicate(
        conn,
        deposit_account_id=body.deposit_account_id,
        amount=body.amount,
        currency=body.currency,
        deposited_at=body.deposited_at,
        source=body.source,
    )
    return {"duplicate": dup}


@router.get("/{inflow_id}")
def get_cash_inflow(inflow_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    result = cash_inflows_repo.get_cash_inflow(conn, inflow_id)
    if result is None:
        raise HTTPException(status_code=404, detail="cash inflow not found")
    return result


@router.patch("/{inflow_id}")
def update_cash_inflow(inflow_id: str, body: CashInflowUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        updated = cash_inflows_repo.update_cash_inflow(
            conn,
            inflow_id,
            deposited_at=body.deposited_at,
            deposit_account_id=body.deposit_account_id,
            source=body.source,
            amount=body.amount,
            currency=body.currency,
            flow_type=body.flow_type,
            memo=body.memo,
        )
    except CashInflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="cash inflow not found")
    return updated


@router.delete("/{inflow_id}", status_code=204)
def delete_cash_inflow(inflow_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not cash_inflows_repo.delete_cash_inflow(conn, inflow_id):
        raise HTTPException(status_code=404, detail="cash inflow not found")
    return None
