"""거래(매수/매도 체결) CRUD API (스펙 7 api/trades.py).

OversellError -> 409, InvalidTradeError -> 422 로 매핑한다 (position_engine.py
docstring에 이미 명시된 계약).
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..repositories import instruments as instruments_repo
from ..services import portfolio_service
from ..services.position_engine import InvalidTradeError, OversellError
from .deps import get_conn, require_api_key
from .schemas import TradeCreate, TradeUpdate

router = APIRouter(
    prefix="/api/v1/instruments/{instrument_id}/trades", tags=["trades"], dependencies=[Depends(require_api_key)]
)


@router.post("", status_code=201)
def create_trade(instrument_id: str, body: TradeCreate, conn: sqlite3.Connection = Depends(get_conn)):
    if instruments_repo.get_instrument(conn, instrument_id) is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    try:
        return portfolio_service.record_trade(
            conn, instrument_id, trade_type=body.trade_type, price=body.price, quantity=body.quantity,
            executed_at=body.executed_at, fee=body.fee, tax=body.tax, memo=body.memo,
        )
    except OversellError as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc),
            "requested_quantity": exc.requested_quantity,
            "available_quantity": exc.available_quantity,
        }) from exc
    except InvalidTradeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{trade_id}")
def update_trade(instrument_id: str, trade_id: str, body: TradeUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        updated = portfolio_service.edit_trade(conn, instrument_id, trade_id, **fields)
    except OversellError as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc),
            "requested_quantity": exc.requested_quantity,
            "available_quantity": exc.available_quantity,
        }) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="trade not found")
    return updated


@router.delete("/{trade_id}", status_code=204)
def delete_trade(instrument_id: str, trade_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not portfolio_service.delete_trade(conn, instrument_id, trade_id):
        raise HTTPException(status_code=404, detail="trade not found")
    return None
