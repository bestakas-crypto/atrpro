"""trades 리포지토리 -- 실제 수동 체결 기록.

스펙 4.5: 거래는 날짜만이 아니라 `executed_at`(체결일시)과 `sequence_no`(동일
시각 충돌 시 정렬값)로 순서를 관리한다. sequence_no는 종목별로 삽입 시점에
자동 증가시켜 부여하며, 이후 수정으로는 바뀌지 않는다 -- 그래야 "오전 매수 →
오후 부분매도 → 마감 전 재매수"처럼 같은 날 여러 거래가 있어도 재생 순서가
항상 안정적이다.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import new_id, utcnow_iso


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "instrument_id": row["instrument_id"],
        "trade_type": row["trade_type"],
        "price": row["price"],
        "quantity": row["quantity"],
        "executed_at": row["executed_at"],
        "sequence_no": row["sequence_no"],
        "fee": row["fee"],
        "tax": row["tax"],
        "memo": row["memo"],
        "atr_at_execution": row["atr_at_execution"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _next_sequence_no(conn: sqlite3.Connection, instrument_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next FROM trades WHERE instrument_id = ?",
        (instrument_id,),
    ).fetchone()
    return row["next"]


def create_trade(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    trade_type: str,
    price: float,
    quantity: float,
    executed_at: str,
    fee: float | None = None,
    tax: float | None = None,
    memo: str | None = None,
    atr_at_execution: float | None = None,
) -> dict[str, Any]:
    trade_id = new_id()
    now = utcnow_iso()
    sequence_no = _next_sequence_no(conn, instrument_id)
    conn.execute(
        """
        INSERT INTO trades
            (id, instrument_id, trade_type, price, quantity, executed_at, sequence_no,
             fee, tax, memo, atr_at_execution, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trade_id, instrument_id, trade_type, price, quantity, executed_at, sequence_no,
         fee, tax, memo, atr_at_execution, now, now),
    )
    return get_trade(conn, trade_id)  # type: ignore[return-value]


def get_trade(conn: sqlite3.Connection, trade_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_trades_for_instrument(conn: sqlite3.Connection, instrument_id: str) -> list[dict[str, Any]]:
    """재생(replay) 순서 -- executed_at 다음 sequence_no 오름차순."""
    rows = conn.execute(
        "SELECT * FROM trades WHERE instrument_id = ? ORDER BY executed_at ASC, sequence_no ASC",
        (instrument_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_trade(
    conn: sqlite3.Connection,
    trade_id: str,
    *,
    price: float | None = None,
    quantity: float | None = None,
    executed_at: str | None = None,
    fee: float | None = None,
    tax: float | None = None,
    memo: str | None = None,
) -> dict[str, Any] | None:
    current = get_trade(conn, trade_id)
    if current is None:
        return None
    now = utcnow_iso()
    conn.execute(
        "UPDATE trades SET price = ?, quantity = ?, executed_at = ?, fee = ?, tax = ?, memo = ?, "
        "updated_at = ? WHERE id = ?",
        (
            price if price is not None else current["price"],
            quantity if quantity is not None else current["quantity"],
            executed_at if executed_at is not None else current["executed_at"],
            fee if fee is not None else current["fee"],
            tax if tax is not None else current["tax"],
            memo if memo is not None else current["memo"],
            now,
            trade_id,
        ),
    )
    return get_trade(conn, trade_id)


def delete_trade(conn: sqlite3.Connection, trade_id: str) -> bool:
    cur = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    return cur.rowcount > 0
