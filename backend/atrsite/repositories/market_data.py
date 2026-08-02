"""daily_bars / atr_values / quote_latest 리포지토리.

2단계(수동 입력)에서는 quote_latest.source == 'manual'만 사용한다. 3단계에서
KIS worker가 'kis' source로 값을 채우기 시작해도 스키마 변경은 필요 없다.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import utcnow_iso


def upsert_quote(
    conn: sqlite3.Connection,
    instrument_id: str,
    *,
    price: float,
    source: str = "manual",
    data_status: str = "MANUAL_OVERRIDE",
    quoted_at: str | None = None,
) -> dict[str, Any]:
    ts = quoted_at or utcnow_iso()
    conn.execute(
        """
        INSERT INTO quote_latest (instrument_id, price, quoted_at, source, data_status)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id) DO UPDATE SET
            price = excluded.price, quoted_at = excluded.quoted_at,
            source = excluded.source, data_status = excluded.data_status
        """,
        (instrument_id, price, ts, source, data_status),
    )
    return get_quote(conn, instrument_id)  # type: ignore[return-value]


def get_quote(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM quote_latest WHERE instrument_id = ?", (instrument_id,)).fetchone()
    if row is None:
        return None
    return {
        "instrument_id": row["instrument_id"],
        "price": row["price"],
        "quoted_at": row["quoted_at"],
        "source": row["source"],
        "data_status": row["data_status"],
    }


def delete_quote(conn: sqlite3.Connection, instrument_id: str) -> None:
    conn.execute("DELETE FROM quote_latest WHERE instrument_id = ?", (instrument_id,))


def upsert_manual_atr(
    conn: sqlite3.Connection,
    instrument_id: str,
    *,
    atr: float,
    trade_date: str,
    period: int = 14,
    source: str = "manual",
) -> dict[str, Any]:
    conn.execute(
        """
        INSERT INTO atr_values (instrument_id, trade_date, period, atr, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id, trade_date, period) DO UPDATE SET
            atr = excluded.atr, source = excluded.source
        """,
        (instrument_id, trade_date, period, atr, source),
    )
    return get_latest_atr(conn, instrument_id, period=period)  # type: ignore[return-value]


def get_latest_atr(conn: sqlite3.Connection, instrument_id: str, *, period: int = 14) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM atr_values WHERE instrument_id = ? AND period = ? "
        "ORDER BY trade_date DESC LIMIT 1",
        (instrument_id, period),
    ).fetchone()
    if row is None:
        return None
    return {
        "instrument_id": row["instrument_id"],
        "trade_date": row["trade_date"],
        "period": row["period"],
        "atr": row["atr"],
        "source": row["source"],
    }


def list_daily_bars(conn: sqlite3.Connection, instrument_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM daily_bars WHERE instrument_id = ? ORDER BY trade_date ASC",
        (instrument_id,),
    ).fetchall()
    return [
        {"trade_date": r["trade_date"], "high": r["high"], "low": r["low"], "close": r["close"]}
        for r in rows
    ]


def upsert_daily_bar(
    conn: sqlite3.Connection, instrument_id: str, *, trade_date: str, high: float, low: float, close: float
) -> None:
    conn.execute(
        """
        INSERT INTO daily_bars (instrument_id, trade_date, high, low, close)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id, trade_date) DO UPDATE SET
            high = excluded.high, low = excluded.low, close = excluded.close
        """,
        (instrument_id, trade_date, high, low, close),
    )
