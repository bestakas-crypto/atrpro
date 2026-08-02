"""fx_rates + app_settings(표시 통화) 리포지토리."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import utcnow_iso

DISPLAY_CURRENCY_KEY = "display_currency"
DEFAULT_DISPLAY_CURRENCY = "KRW"


def list_rates(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT * FROM fx_rates").fetchall()
    result = {
        r["currency"]: {"rate_to_krw": r["rate_to_krw"], "source": r["source"], "updated_at": r["updated_at"]}
        for r in rows
    }
    result.setdefault("KRW", {"rate_to_krw": 1.0, "source": "fixed", "updated_at": None})
    return result


def upsert_rate(
    conn: sqlite3.Connection, currency: str, rate_to_krw: float, *, source: str = "manual"
) -> None:
    if currency == "KRW":
        return
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO fx_rates (currency, rate_to_krw, source, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(currency) DO UPDATE SET
            rate_to_krw = excluded.rate_to_krw, source = excluded.source, updated_at = excluded.updated_at
        """,
        (currency, rate_to_krw, source, now),
    )


def get_display_currency(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (DISPLAY_CURRENCY_KEY,)).fetchone()
    return row["value"] if row else DEFAULT_DISPLAY_CURRENCY


def set_display_currency(conn: sqlite3.Connection, currency: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (DISPLAY_CURRENCY_KEY, currency),
    )
