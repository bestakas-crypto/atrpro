"""기간별 확정손익(실현손익) 조회 API."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ..services import realized_pnl_service
from .deps import get_conn, require_api_key

router = APIRouter(prefix="/api/v1/realized-pnl", tags=["realized-pnl"], dependencies=[Depends(require_api_key)])


@router.get("")
def get_realized_pnl(
    start_date: str = Query(...),
    end_date: str = Query(...),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        return realized_pnl_service.realized_pnl_for_period(conn, start_date, end_date, currency)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
