"""position_state 리포지토리 -- position_engine 재생 결과의 캐시.

거래가 생성/수정/삭제될 때마다 portfolio_service가 전체 거래를 재생해서 이
테이블을 덮어쓴다. 이 리포지토리 자체는 계산을 하지 않는다.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import utcnow_iso


def get_position(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM position_state WHERE instrument_id = ?", (instrument_id,)).fetchone()
    if row is None:
        return None
    return {
        "instrument_id": row["instrument_id"],
        "quantity": row["quantity"],
        "avg_price": row["avg_price"],
        "cost_basis": row["cost_basis"],
        "last_buy_price": row["last_buy_price"],
        "last_sell_price": row["last_sell_price"],
        "last_trade_price": row["last_trade_price"],
        "updated_at": row["updated_at"],
    }


def upsert_position(
    conn: sqlite3.Connection,
    instrument_id: str,
    *,
    quantity: float,
    avg_price: float,
    cost_basis: float,
    last_buy_price: float | None,
    last_sell_price: float | None,
    last_trade_price: float | None,
) -> dict[str, Any]:
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO position_state
            (instrument_id, quantity, avg_price, cost_basis, last_buy_price, last_sell_price,
             last_trade_price, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id) DO UPDATE SET
            quantity = excluded.quantity, avg_price = excluded.avg_price,
            cost_basis = excluded.cost_basis, last_buy_price = excluded.last_buy_price,
            last_sell_price = excluded.last_sell_price, last_trade_price = excluded.last_trade_price,
            updated_at = excluded.updated_at
        """,
        (instrument_id, quantity, avg_price, cost_basis, last_buy_price, last_sell_price,
         last_trade_price, now),
    )
    return get_position(conn, instrument_id)  # type: ignore[return-value]
