"""backend/atrsite/api/cash_flow.py -- 순외부현금흐름 요약 API, v1.4
(analyze.kunoh.top 1단계, 2026-08-12 추가).

analyze.kunoh.top이 TWR/Modified Dietz 계산 입력값으로 읽어갈 용도.
읽기 전용(집계만) -- 쓰기는 cash_inflows/withdrawals 라우터를 그대로 쓴다.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..repositories import cash_flow as cash_flow_repo
from .deps import get_conn, require_api_key

router = APIRouter(prefix="/api/v1/cash-flow", tags=["cash-flow"], dependencies=[Depends(require_api_key)])


@router.get("/net")
def get_net_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """통화별 {inflow, outflow, net}(EXTERNAL만). 기간 생략 시 전체."""
    return cash_flow_repo.net_summary(conn, start_date=start_date, end_date=end_date)


@router.get("/daily")
def get_daily_net_flow(
    start_date: str,
    end_date: str,
    conn: sqlite3.Connection = Depends(get_conn),
):
    """일별/통화별 순외부현금흐름(EXTERNAL만, 출금은 음수) -- 자산 스냅샷과
    날짜 기준으로 조인해 TWR/Modified Dietz를 계산할 때 쓴다."""
    if not start_date or not end_date:
        raise HTTPException(status_code=400, detail="start_date, end_date는 필수입니다.")
    return {"items": cash_flow_repo.daily_net_flow(conn, start_date=start_date, end_date=end_date)}
