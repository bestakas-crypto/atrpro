"""instruments + strategy_settings 리포지토리.

배수(buy/sell/stop multiple) 변경은 strategy_settings에 새 버전을 추가하는
방식으로 기록한다(스펙 10.1 "ATR 배수와 설정 버전"). 현재 유효한 설정은
`version`이 가장 큰 행이다.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import new_id, utcnow_iso


def _latest_settings_row(conn: sqlite3.Connection, instrument_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM strategy_settings WHERE instrument_id = ? ORDER BY version DESC LIMIT 1",
        (instrument_id,),
    ).fetchone()


def _row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    settings_row = _latest_settings_row(conn, row["id"])
    return {
        "id": row["id"],
        "name": row["name"],
        "currency": row["currency"],
        "kis_code": row["kis_code"],
        "kis_market": row["kis_market"],
        "auto_update_high": bool(row["auto_update_high"]),
        "post_entry_high_price": row["post_entry_high_price"],
        "trailing_stop_price": row["trailing_stop_price"],
        "is_active": bool(row["is_active"]),
        "buy_multiple": settings_row["buy_multiple"] if settings_row else 1.0,
        "sell_multiple": settings_row["sell_multiple"] if settings_row else 1.5,
        "stop_multiple": settings_row["stop_multiple"] if settings_row else 2.0,
        "tranche_amount": settings_row["tranche_amount"] if settings_row else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_instrument(
    conn: sqlite3.Connection,
    *,
    name: str,
    currency: str = "KRW",
    buy_multiple: float = 1.0,
    sell_multiple: float = 1.5,
    stop_multiple: float = 2.0,
    tranche_amount: float | None = None,
    kis_code: str | None = None,
    kis_market: str | None = None,
) -> dict[str, Any]:
    instrument_id = new_id()
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO instruments
            (id, name, currency, kis_code, kis_market, auto_update_high,
             post_entry_high_price, trailing_stop_price, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, 1, ?, ?)
        """,
        (instrument_id, name, currency, kis_code, kis_market, now, now),
    )
    conn.execute(
        """
        INSERT INTO strategy_settings
            (instrument_id, buy_multiple, sell_multiple, stop_multiple, tranche_amount, version, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        (instrument_id, buy_multiple, sell_multiple, stop_multiple, tranche_amount, now),
    )
    conn.execute(
        "INSERT INTO position_state (instrument_id, quantity, avg_price, cost_basis, updated_at) "
        "VALUES (?, 0, 0, 0, ?)",
        (instrument_id, now),
    )
    return get_instrument(conn, instrument_id)  # type: ignore[return-value]


def get_instrument(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(conn, row)


def list_instruments(conn: sqlite3.Connection, *, active_only: bool = True) -> list[dict[str, Any]]:
    query = "SELECT * FROM instruments"
    params: tuple[Any, ...] = ()
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY created_at ASC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(conn, row) for row in rows]


def update_settings(
    conn: sqlite3.Connection,
    instrument_id: str,
    *,
    name: str | None = None,
    currency: str | None = None,
    buy_multiple: float | None = None,
    sell_multiple: float | None = None,
    stop_multiple: float | None = None,
    tranche_amount: float | None = None,
    kis_code: str | None = None,
    kis_market: str | None = None,
) -> dict[str, Any] | None:
    current = get_instrument(conn, instrument_id)
    if current is None:
        return None
    now = utcnow_iso()

    if any(v is not None for v in (name, currency, kis_code, kis_market)):
        conn.execute(
            "UPDATE instruments SET name = ?, currency = ?, kis_code = ?, kis_market = ?, "
            "updated_at = ? WHERE id = ?",
            (name if name is not None else current["name"],
             currency if currency is not None else current["currency"],
             kis_code if kis_code is not None else current["kis_code"],
             kis_market if kis_market is not None else current["kis_market"],
             now, instrument_id),
        )

    multiples_changed = any(v is not None for v in (buy_multiple, sell_multiple, stop_multiple, tranche_amount))
    if multiples_changed:
        latest = _latest_settings_row(conn, instrument_id)
        next_version = (latest["version"] + 1) if latest else 1
        conn.execute(
            """
            INSERT INTO strategy_settings
                (instrument_id, buy_multiple, sell_multiple, stop_multiple, tranche_amount, version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instrument_id,
                buy_multiple if buy_multiple is not None else current["buy_multiple"],
                sell_multiple if sell_multiple is not None else current["sell_multiple"],
                stop_multiple if stop_multiple is not None else current["stop_multiple"],
                tranche_amount if tranche_amount is not None else current["tranche_amount"],
                next_version,
                now,
            ),
        )
        conn.execute("UPDATE instruments SET updated_at = ? WHERE id = ?", (now, instrument_id))

    return get_instrument(conn, instrument_id)


def update_manual_fields(
    conn: sqlite3.Connection,
    instrument_id: str,
    *,
    post_entry_high_price: float | None = ...,  # type: ignore[assignment]
    trailing_stop_price: float | None = ...,  # type: ignore[assignment]
    auto_update_high: bool | None = None,
) -> dict[str, Any] | None:
    """`...`(Ellipsis)는 '값을 바꾸지 않음'을 뜻하고 None은 '명시적으로 NULL로
    설정'을 뜻한다 -- 두 상태를 구분해야 하는 필드이므로 기본값을 None 대신
    Ellipsis로 둔다."""
    current = get_instrument(conn, instrument_id)
    if current is None:
        return None
    now = utcnow_iso()
    new_high = current["post_entry_high_price"] if post_entry_high_price is ... else post_entry_high_price
    new_stop = current["trailing_stop_price"] if trailing_stop_price is ... else trailing_stop_price
    new_auto = current["auto_update_high"] if auto_update_high is None else auto_update_high
    conn.execute(
        "UPDATE instruments SET post_entry_high_price = ?, trailing_stop_price = ?, "
        "auto_update_high = ?, updated_at = ? WHERE id = ?",
        (new_high, new_stop, 1 if new_auto else 0, now, instrument_id),
    )
    return get_instrument(conn, instrument_id)


def delete_instrument(conn: sqlite3.Connection, instrument_id: str) -> bool:
    cur = conn.execute("DELETE FROM instruments WHERE id = ?", (instrument_id,))
    return cur.rowcount > 0


def clear_instrument_history(conn: sqlite3.Connection, instrument_id: str) -> None:
    """"초기화" -- 거래 이력과 파생 상태를 전부 지우되 종목 자체(설정 포함)는
    남긴다 (기존 앱의 handleResetStock과 동일한 의미)."""
    now = utcnow_iso()
    conn.execute("DELETE FROM trades WHERE instrument_id = ?", (instrument_id,))
    conn.execute("DELETE FROM signal_events WHERE instrument_id = ?", (instrument_id,))
    conn.execute("DELETE FROM signal_state WHERE instrument_id = ?", (instrument_id,))
    conn.execute("DELETE FROM quote_latest WHERE instrument_id = ?", (instrument_id,))
    conn.execute(
        "UPDATE instruments SET post_entry_high_price = NULL, trailing_stop_price = NULL, "
        "updated_at = ? WHERE id = ?",
        (now, instrument_id),
    )
    conn.execute(
        "UPDATE position_state SET quantity = 0, avg_price = 0, cost_basis = 0, updated_at = ? "
        "WHERE instrument_id = ?",
        (now, instrument_id),
    )
