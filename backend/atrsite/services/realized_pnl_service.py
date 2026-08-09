"""기간별 확정손익(실현손익) 조회 서비스.

기존 평균원가법 계산은 position_engine.replay_trades()가 검증된 단일 출처다.
이 모듈은 종목별 전체 거래를 처음부터 replay한 뒤, 사용자가 지정한 기간 안의
매도 step만 필터링해서 합산한다.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from ..repositories import fx as fx_repo
from ..repositories import instruments as instruments_repo
from ..repositories import trades as trades_repo
from .portfolio_service import _convert
from .position_engine import Trade as EngineTrade, replay_trades

KST = ZoneInfo("Asia/Seoul")


def realized_pnl_for_period(
    conn: sqlite3.Connection, start_date: str, end_date: str, base_currency: str | None = None
) -> dict[str, Any]:
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date must be before or equal to end_date")

    period_start = datetime.combine(start, time.min, tzinfo=KST)
    period_end = datetime.combine(end, time.max, tzinfo=KST)
    display_currency = base_currency or fx_repo.get_display_currency(conn)
    rates = fx_repo.list_rates(conn)

    items: list[dict[str, Any]] = []
    total_converted = 0.0
    missing_fx_count = 0

    for instrument in instruments_repo.list_instruments(conn, active_only=False):
        trades = trades_repo.list_trades_for_instrument(conn, instrument["id"])
        if not trades:
            continue
        engine_trades = [_to_engine_trade(row) for row in trades]
        _, steps = replay_trades(engine_trades)

        sell_rows = {row["id"]: row for row in trades if row["trade_type"] == "sell"}
        sells: list[dict[str, Any]] = []
        realized_native = 0.0

        for step in steps:
            if step.realized_pnl is None or step.trade.trade_id is None:
                continue
            row = sell_rows.get(step.trade.trade_id)
            if row is None:
                continue
            executed_at = _parse_executed_at(row["executed_at"])
            if not (period_start <= executed_at <= period_end):
                continue
            pnl = step.realized_pnl
            realized_native += pnl
            sells.append({
                "trade_id": row["id"],
                "executed_at": row["executed_at"],
                "quantity": row["quantity"],
                "price": row["price"],
                "fee": row["fee"] or 0.0,
                "tax": row["tax"] or 0.0,
                "realized_pnl_native": pnl,
            })

        if not sells:
            continue

        converted = _convert(realized_native, instrument["currency"], display_currency, rates)
        if converted is None:
            missing_fx_count += 1
        else:
            total_converted += converted

        items.append({
            "instrument_id": instrument["id"],
            "instrument_name": instrument["name"],
            "currency": instrument["currency"],
            "realized_pnl_native": realized_native,
            "realized_pnl_converted": converted,
            "sell_count": len(sells),
            "sells": sells,
        })

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "base_currency": display_currency,
        "total_realized_pnl": total_converted,
        "missing_fx_count": missing_fx_count,
        "items": items,
        "note": "환산은 조회 시점 환율 기준입니다. 매도 시점 환율이 아닙니다.",
    }


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD") from exc


def _parse_executed_at(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _to_engine_trade(row: dict[str, Any]) -> EngineTrade:
    return EngineTrade(
        trade_type=row["trade_type"],
        price=row["price"],
        quantity=row["quantity"],
        fee=row["fee"] or 0.0,
        tax=row["tax"] or 0.0,
        trade_id=row["id"],
    )
