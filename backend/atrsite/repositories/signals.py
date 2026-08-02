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
        "latest_event_id": row["latest_event_id"],
        "acknowledged_event_id": row["acknowledged_event_id"],
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
    latest_event_id: int | None = None,
) -> dict[str, Any]:
    """latest_event_id=None(기본값)이면 기존 값을 그대로 유지한다 -- 이번
    호출에서 새 signal_events가 안 생겼다는 뜻이므로(같은 상태 반복) "확인"
    여부 판정 기준이 되는 값을 건드리면 안 된다."""
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO signal_state
            (instrument_id, status, data_status, next_buy_price, take_profit_price,
             trailing_stop_price, reason, computed_at, latest_event_id, acknowledged_event_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(instrument_id) DO UPDATE SET
            status = excluded.status, data_status = excluded.data_status,
            next_buy_price = excluded.next_buy_price, take_profit_price = excluded.take_profit_price,
            trailing_stop_price = excluded.trailing_stop_price, reason = excluded.reason,
            computed_at = excluded.computed_at,
            latest_event_id = COALESCE(excluded.latest_event_id, signal_state.latest_event_id)
        """,
        (instrument_id, status, data_status, next_buy_price, take_profit_price,
         trailing_stop_price, reason, now, latest_event_id),
    )
    return get_signal_state(conn, instrument_id)  # type: ignore[return-value]


def acknowledge_signal(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any] | None:
    """현재 latest_event_id를 acknowledged_event_id에 그대로 복사해 배너를 끈다.
    이후 상태가 실제로 또 바뀌어 latest_event_id가 갱신되기 전까지는 다시 뜨지 않는다."""
    conn.execute(
        "UPDATE signal_state SET acknowledged_event_id = latest_event_id WHERE instrument_id = ?",
        (instrument_id,),
    )
    return get_signal_state(conn, instrument_id)


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
