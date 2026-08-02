"""대시보드(종목 목록 총계) + 환율 API (스펙 7 api/dashboard.py)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from ..repositories import fx as fx_repo
from ..services import portfolio_service
from .deps import get_conn, require_api_key
from .schemas import FxRateUpdate

router = APIRouter(prefix="/api/v1", tags=["dashboard"], dependencies=[Depends(require_api_key)])


@router.get("/dashboard")
def get_dashboard(conn: sqlite3.Connection = Depends(get_conn)):
    return portfolio_service.build_dashboard(conn)


@router.get("/fx")
def get_fx(conn: sqlite3.Connection = Depends(get_conn)):
    return {"rates": fx_repo.list_rates(conn), "display_currency": fx_repo.get_display_currency(conn)}


@router.put("/fx")
def update_fx(body: FxRateUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    for currency, rate in body.rates.items():
        fx_repo.upsert_rate(conn, currency, rate, source="manual")
    if body.display_currency:
        fx_repo.set_display_currency(conn, body.display_currency)
    return {"rates": fx_repo.list_rates(conn), "display_currency": fx_repo.get_display_currency(conn)}
