"""backend/atrsite/api/withdrawals.py -- 현금 출금기록(v1.1) CRUD + 합산 API.

URL 프리픽스는 기존 프로젝트 관례(/api/v1/<resource>)를 따른다 -- v1.1 스펙
초안의 /api/withdrawals가 아니라 deposits/instruments와 동일한 /api/v1/withdrawals.
수정 메서드도 스펙 초안은 PUT을 제안했지만, 이 프로젝트는 부분수정에 전부
PATCH를 쓰므로(예: PATCH /api/v1/deposits/{id}) 동일하게 PATCH로 맞춘다.

/summary, /purposes는 반드시 /{withdrawal_id}보다 먼저 등록해야 한다 --
안 그러면 "summary"/"purposes" 문자열이 {withdrawal_id}로 잡혀버린다
(FastAPI/Starlette 라우팅은 등록 순서대로 첫 매치를 쓴다).
"""
from __future__ import annotations

import csv
import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..repositories import withdrawals as withdrawals_repo
from ..repositories.withdrawals import WithdrawalFilter, WithdrawalValidationError
from .deps import get_conn, require_api_key
from .schemas import WithdrawalCreate, WithdrawalUpdate

# 스펙 9.3 "CSV 수식 삽입 방지" -- 셀 값이 이 문자로 시작하면 스프레드시트가
# 수식으로 해석할 수 있어(CSV Injection) 앞에 탭 문자를 붙여 무력화한다.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    if value and value[0] in _CSV_FORMULA_PREFIXES:
        return "\t" + value
    return value

router = APIRouter(prefix="/api/v1/withdrawals", tags=["withdrawals"], dependencies=[Depends(require_api_key)])


def _filter_from_query(
    start_date: str | None, end_date: str | None, purpose: str | None,
    deposit_account_id: str | None, currency: str | None, flow_type: str | None = None,
) -> WithdrawalFilter:
    return WithdrawalFilter(
        start_date=start_date, end_date=end_date, purpose=purpose,
        deposit_account_id=deposit_account_id, currency=currency, flow_type=flow_type,
    )


@router.get("/summary")
def get_summary(conn: sqlite3.Connection = Depends(get_conn)):
    """스펙 6 -- 오늘/이번주/이번달/YTD, 통화별 합산(환율 환산 없음)."""
    return withdrawals_repo.period_summary(conn)


@router.get("/purposes")
def get_recent_purposes(conn: sqlite3.Connection = Depends(get_conn)):
    """스펙 9.1 -- 최근/자주 쓴 용도 추천 목록."""
    return {"purposes": withdrawals_repo.recent_purposes(conn)}


@router.get("/export.csv")
def export_csv(
    start_date: str | None = None,
    end_date: str | None = None,
    purpose: str | None = None,
    deposit_account_id: str | None = None,
    currency: str | None = None,
    flow_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """스펙 9.3 -- 현재 필터 조건 그대로 CSV 내보내기. id/deposit_account_id
    같은 내부 식별자와 API 키는 CSV에 절대 포함하지 않는다(스펙 13 보안)."""
    f = _filter_from_query(start_date, end_date, purpose, deposit_account_id, currency, flow_type)
    rows = withdrawals_repo.list_all_matching(conn, f)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["출금일시", "출금계좌", "용도", "금액", "통화", "메모"])
    for r in rows:
        # KRW/JPY는 소수점 없이, 그 외(USD)는 소수점 유지 -- 60000.0처럼 파이썬
        # float 그대로 찍히지 않게 통화별로 자릿수를 맞춘다.
        amount_str = f"{r['amount']:.0f}" if r["currency"] in ("KRW", "JPY") else f"{r['amount']:.2f}"
        writer.writerow([
            _csv_safe(r["withdrawn_at"].replace("T", " ")),
            _csv_safe(r["account_name_snapshot"]),
            _csv_safe(r["purpose"]),
            amount_str,
            r["currency"],
            _csv_safe(r["memo"] or ""),
        ])

    # UTF-8 BOM -- Windows 엑셀에서 한글이 깨지지 않게(스펙 9.3).
    csv_bytes = "﻿" + buf.getvalue()
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=cash_withdrawals.csv"},
    )


@router.get("")
def list_withdrawals(
    start_date: str | None = None,
    end_date: str | None = None,
    purpose: str | None = None,
    deposit_account_id: str | None = None,
    currency: str | None = None,
    flow_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="withdrawn_at_desc"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    f = _filter_from_query(start_date, end_date, purpose, deposit_account_id, currency, flow_type)
    sort_desc = sort != "withdrawn_at_asc"
    items, total = withdrawals_repo.list_withdrawals(conn, f, limit=limit, offset=offset, sort_desc=sort_desc)
    # 스펙 5.2/7 -- 목록과 합계를 같은 필터 조건으로, 페이지 무관 전체 기준 계산.
    return {
        "items": items,
        "total": total,
        "sum_by_currency": withdrawals_repo.sum_by_currency(conn, f),
        "sum_by_account": withdrawals_repo.sum_by_account(conn, f),
    }


@router.post("", status_code=201)
def create_withdrawal(body: WithdrawalCreate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return withdrawals_repo.create_withdrawal(
            conn,
            withdrawn_at=body.withdrawn_at,
            deposit_account_id=body.deposit_account_id,
            purpose=body.purpose,
            amount=body.amount,
            currency=body.currency,
            flow_type=body.flow_type,
            memo=body.memo,
        )
    except WithdrawalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/check-duplicate")
def check_duplicate(body: WithdrawalCreate, conn: sqlite3.Connection = Depends(get_conn)):
    """스펙 9.2 -- 저장 전 미리보기용. 실제 저장을 막지 않고 중복 가능성만 알려준다.
    /{withdrawal_id}보다 먼저 등록(위 주석 참고 -- 정적 경로 우선)."""
    dup = withdrawals_repo.find_possible_duplicate(
        conn,
        deposit_account_id=body.deposit_account_id,
        amount=body.amount,
        currency=body.currency,
        withdrawn_at=body.withdrawn_at,
        purpose=body.purpose,
    )
    return {"duplicate": dup}


@router.get("/{withdrawal_id}")
def get_withdrawal(withdrawal_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    result = withdrawals_repo.get_withdrawal(conn, withdrawal_id)
    if result is None:
        raise HTTPException(status_code=404, detail="withdrawal not found")
    return result


@router.patch("/{withdrawal_id}")
def update_withdrawal(withdrawal_id: str, body: WithdrawalUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        updated = withdrawals_repo.update_withdrawal(
            conn,
            withdrawal_id,
            withdrawn_at=body.withdrawn_at,
            deposit_account_id=body.deposit_account_id,
            purpose=body.purpose,
            amount=body.amount,
            currency=body.currency,
            flow_type=body.flow_type,
            memo=body.memo,
        )
    except WithdrawalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="withdrawal not found")
    return updated


@router.delete("/{withdrawal_id}", status_code=204)
def delete_withdrawal(withdrawal_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not withdrawals_repo.delete_withdrawal(conn, withdrawal_id):
        raise HTTPException(status_code=404, detail="withdrawal not found")
    return None
