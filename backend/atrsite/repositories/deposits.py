"""deposits 리포지토리 -- 현금/예금 관리."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import new_id, utcnow_iso


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "account_name": row["account_name"],
        "amount": row["amount"],
        "currency": row["currency"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_deposit(
    conn: sqlite3.Connection, *, account_name: str, amount: float, currency: str = "KRW"
) -> dict[str, Any]:
    deposit_id = new_id()
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO deposits (id, account_name, amount, currency, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (deposit_id, account_name, amount, currency, now, now),
    )
    return get_deposit(conn, deposit_id)  # type: ignore[return-value]


def get_deposit(conn: sqlite3.Connection, deposit_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_deposits(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM deposits ORDER BY created_at ASC").fetchall()
    return [_row_to_dict(r) for r in rows]


def update_deposit(
    conn: sqlite3.Connection,
    deposit_id: str,
    *,
    account_name: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
) -> dict[str, Any] | None:
    current = get_deposit(conn, deposit_id)
    if current is None:
        return None
    now = utcnow_iso()
    conn.execute(
        "UPDATE deposits SET account_name = ?, amount = ?, currency = ?, updated_at = ? WHERE id = ?",
        (
            account_name if account_name is not None else current["account_name"],
            amount if amount is not None else current["amount"],
            currency if currency is not None else current["currency"],
            now,
            deposit_id,
        ),
    )
    return get_deposit(conn, deposit_id)


def delete_deposit(conn: sqlite3.Connection, deposit_id: str) -> bool:
    cur = conn.execute("DELETE FROM deposits WHERE id = ?", (deposit_id,))
    return cur.rowcount > 0
