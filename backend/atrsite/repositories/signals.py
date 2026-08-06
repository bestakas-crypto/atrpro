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


def update_data_status_only(
    conn: sqlite3.Connection,
    instrument_id: str,
    *,
    data_status: str,
    next_buy_price: float | None,
    take_profit_price: float | None,
    trailing_stop_price: float | None,
) -> dict[str, Any] | None:
    """데이터 장애(STALE/API_ERROR/INSUFFICIENT_DATA) 중에만 쓰는 부분 갱신.

    2026-08-06 실전 버그 수정: 기존엔 upsert_signal_state()를 그대로 써서
    status/reason/latest_event_id까지 전부 새 계산값(항상 NEUTRAL/NO_POSITION,
    signal_engine.determine_signal()이 데이터 장애 중엔 그렇게만 반환하므로)으로
    덮어썼다 -- 그 결과 손절/익절 경고가 떠 있다가 KIS 장애나 시세 지연이
    겹치면 화면에서 조용히 사라지는 사고가 실제로 재현됐다(GPT 코드리뷰 계기로
    발견). 이 함수는 status/reason/latest_event_id/acknowledged_event_id를
    전혀 건드리지 않고 data_status와 가격 무관 기준선(ATR/마지막매수가/최고가
    기반이라 시세 신선도와 무관하게 항상 재계산 가능)만 갱신한다.

    이미 signal_state 행이 있는 종목에만 쓴다 -- 없으면(최초 계산) status 등
    필수 컬럼을 채울 값이 없으므로 호출자가 일반 경로(upsert_signal_state)로
    보내야 한다.
    """
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE signal_state SET
            data_status = ?, next_buy_price = ?, take_profit_price = ?,
            trailing_stop_price = ?, computed_at = ?
        WHERE instrument_id = ?
        """,
        (data_status, next_buy_price, take_profit_price, trailing_stop_price, now, instrument_id),
    )
    return get_signal_state(conn, instrument_id)


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
