"""종목 CRUD + 수동 시세/ATR 입력 API (스펙 7 api/instruments.py)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..repositories import instruments as instruments_repo
from ..repositories import trades as trades_repo
from ..services import portfolio_service
from ..utils import utcnow_iso
from .deps import get_conn, require_api_key
from .schemas import (
    InstrumentCreate,
    InstrumentManualUpdate,
    InstrumentSettingsUpdate,
    QuoteCommit,
    AtrCommit,
)

router = APIRouter(prefix="/api/v1/instruments", tags=["instruments"], dependencies=[Depends(require_api_key)])


@router.post("", status_code=201)
def create_instrument(body: InstrumentCreate, conn: sqlite3.Connection = Depends(get_conn)):
    return instruments_repo.create_instrument(
        conn, name=body.name, currency=body.currency,
        buy_multiple=body.buy_multiple, sell_multiple=body.sell_multiple, stop_multiple=body.stop_multiple,
        tranche_amount=body.tranche_amount, kis_code=body.kis_code, kis_market=body.kis_market,
    )


@router.get("")
def list_instruments(conn: sqlite3.Connection = Depends(get_conn)):
    return instruments_repo.list_instruments(conn)


@router.get("/{instrument_id}")
def get_instrument(instrument_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    snapshot = portfolio_service.instrument_snapshot(conn, instrument_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    snapshot["trades"] = trades_repo.list_trades_for_instrument(conn, instrument_id)
    return snapshot


@router.patch("/{instrument_id}")
def update_settings(instrument_id: str, body: InstrumentSettingsUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    updated = instruments_repo.update_settings(
        conn, instrument_id, name=body.name, currency=body.currency,
        buy_multiple=body.buy_multiple, sell_multiple=body.sell_multiple, stop_multiple=body.stop_multiple,
        tranche_amount=body.tranche_amount, kis_code=body.kis_code, kis_market=body.kis_market,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    portfolio_service.recompute_signal(conn, instrument_id)
    return updated


@router.patch("/{instrument_id}/manual")
def update_manual(instrument_id: str, body: InstrumentManualUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    """스펙 13.2 "수동 보정" -- 최고가/자동갱신 여부를 직접 조정."""
    if body.post_entry_high_price is not None:
        updated = portfolio_service.commit_post_high(
            conn, instrument_id, post_entry_high_price=body.post_entry_high_price
        )
    else:
        updated = instruments_repo.get_instrument(conn, instrument_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    if body.auto_update_high is not None:
        updated = instruments_repo.update_manual_fields(conn, instrument_id, auto_update_high=body.auto_update_high)
    return updated


@router.post("/{instrument_id}/quote")
def commit_quote(instrument_id: str, body: QuoteCommit, conn: sqlite3.Connection = Depends(get_conn)):
    if instruments_repo.get_instrument(conn, instrument_id) is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    return portfolio_service.commit_quote(conn, instrument_id, price=body.price)


@router.post("/{instrument_id}/atr")
def commit_atr(instrument_id: str, body: AtrCommit, conn: sqlite3.Connection = Depends(get_conn)):
    if instruments_repo.get_instrument(conn, instrument_id) is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    trade_date = body.trade_date or utcnow_iso()[:10]
    return portfolio_service.commit_manual_atr(conn, instrument_id, atr=body.atr, trade_date=trade_date)


@router.post("/{instrument_id}/reset")
def reset_instrument(instrument_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if instruments_repo.get_instrument(conn, instrument_id) is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    instruments_repo.clear_instrument_history(conn, instrument_id)
    return instruments_repo.get_instrument(conn, instrument_id)


@router.delete("/{instrument_id}", status_code=204)
def delete_instrument(instrument_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not instruments_repo.delete_instrument(conn, instrument_id):
        raise HTTPException(status_code=404, detail="instrument not found")
    return None
