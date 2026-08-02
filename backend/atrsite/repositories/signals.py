"""signal_state / signal_events 리포지토리."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import utcnow_iso


def get_signal_state(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM signal_state WHERE instrument_id = ?", (instrument_id,)).fetchone()
    if row is None:
        return None
    return {
        "instrument_id": row["instrument_id"],
        "status": row["status"],
        "data_status": row["data_status"],
        "next_buy_price": row["next_buy_price"],
        "take_profit_price": row["take_profit_price"],
        "trailing_stop_price": row["trailing_stop_price"],
        "reason": row["reason"],
        "computed_at": row["computed_at"],
    }


def upsert_signal_state(
    conn: sqlite3.Connection,
    instrument_id: str,
    *,
    status: str,
    data_status: str,
    next_buy_price: float | None,
    take_profit_price: float | None,
    trailing_stop_price: float | None,
    reason: str,
) -> dict[str, Any]:
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO signal_state
            (instrument_id, status, data_status, next_buy_price, take_profit_price,
             trailing_stop_price, reason, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id) DO UPDATE SET
            status = excluded.status, data_status = excluded.data_status,
            next_buy_price = excluded.next_buy_price, take_profit_price = excluded.take_profit_price,
            trailing_stop_price = excluded.trailing_stop_price, reason = excluded.reason,
            computed_at = excluded.computed_at
        """,
        (instrument_id, status, data_status, next_buy_price, take_profit_price,
         trailing_stop_price, reason, now),
    )
    return get_signal_state(conn, instrument_id)  # type: ignore[return-value]


def record_signal_event(
    conn: sqlite3.Connection, instrument_id: str, *, previous_status: str | None, new_status: str
) -> int:
    now = utcnow_iso()
    cur = conn.execute(
        "INSERT INTO signal_events (instrument_id, previous_status, new_status, created_at) "
        "VALUES (?, ?, ?, ?)",
        (instrument_id, previous_status, new_status, now),
    )
    return cur.lastrowid


def list_signal_events(conn: sqlite3.Connection, instrument_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM signal_events WHERE instrument_id = ? ORDER BY created_at DESC LIMIT ?",
        (instrument_id, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "instrument_id": r["instrument_id"],
            "previous_status": r["previous_status"],
            "new_status": r["new_status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
