"""backend/atrsite/repositories/snapshots.py -- v1.3 portfolio_daily_snapshots/
portfolio_snapshot_items 저장소. 계산(services/snapshot_service.py)과는 분리 --
여기는 순수 CRUD/upsert만 담당한다.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from ..services.snapshot_service import COMPLETE_LIKE_STATUSES
from ..utils import new_id, utcnow_iso

_ITEM_COLUMNS = (
    "id", "snapshot_id", "item_type", "account_id", "account_name_snapshot",
    "instrument_id", "instrument_name_snapshot", "instrument_code_snapshot", "market_code",
    "currency", "quantity", "unit_price", "price_date", "original_value", "exchange_rate",
    "krw_value", "cost_basis", "unrealized_profit", "data_status", "error_message", "created_at",
)


def _snapshot_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _item_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def get_snapshot(conn: sqlite3.Connection, snapshot_date: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM portfolio_daily_snapshots WHERE snapshot_date = ?", (snapshot_date,)
    ).fetchone()
    return _snapshot_row_to_dict(row) if row else None


def get_latest_snapshot(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM portfolio_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    return _snapshot_row_to_dict(row) if row else None


def list_snapshots(
    conn: sqlite3.Connection, *, start_date: Optional[str] = None, end_date: Optional[str] = None,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if start_date:
        clauses.append("snapshot_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("snapshot_date <= ?")
        params.append(end_date)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM portfolio_daily_snapshots {where} ORDER BY snapshot_date ASC", params
    ).fetchall()
    return [_snapshot_row_to_dict(r) for r in rows]


def list_snapshot_items(conn: sqlite3.Connection, snapshot_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshot_items WHERE snapshot_id = ? ORDER BY item_type ASC, created_at ASC",
        (snapshot_id,),
    ).fetchall()
    return [_item_row_to_dict(r) for r in rows]


def save_snapshot(conn: sqlite3.Connection, data: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """data는 snapshot_service.build_snapshot()의 반환값(그대로, "items" 포함).

    같은 snapshot_date에 이미 COMPLETE류(COMPLETE_LIKE_STATUSES) 스냅샷이 있으면
    force=False일 때 조용히 건너뛰고 기존 값을 반환한다(스펙: "이미 정상 완료된
    스냅샷은 특별한 이유 없이 덮어쓰지 않는다"). force=True는 관리자 수동
    재계산(/run, /{date}/recalculate) 전용 -- 명시적 요청이니 덮어쓴다.
    """
    data = dict(data)
    items = data.pop("items")
    snapshot_date = data["snapshot_date"]

    existing = get_snapshot(conn, snapshot_date)
    if existing and not force and existing["data_quality_status"] in COMPLETE_LIKE_STATUSES:
        return existing

    now = utcnow_iso()
    if existing:
        snapshot_id = existing["id"]
        conn.execute("DELETE FROM portfolio_snapshot_items WHERE snapshot_id = ?", (snapshot_id,))
        fields = {**data, "updated_at": now}
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE portfolio_daily_snapshots SET {set_clause} WHERE id = ?",
            (*fields.values(), snapshot_id),
        )
    else:
        snapshot_id = new_id()
        columns = [*data.keys(), "id", "created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in columns)
        values = [*data.values(), snapshot_id, now, now]
        conn.execute(
            f"INSERT INTO portfolio_daily_snapshots ({', '.join(columns)}) VALUES ({placeholders})", values,
        )

    for item in items:
        row = {
            "id": new_id(), "snapshot_id": snapshot_id, "created_at": now,
            "item_type": item["item_type"], "account_id": item.get("account_id"),
            "account_name_snapshot": item.get("account_name_snapshot"),
            "instrument_id": item.get("instrument_id"),
            "instrument_name_snapshot": item.get("instrument_name_snapshot"),
            "instrument_code_snapshot": item.get("instrument_code_snapshot"),
            "market_code": item.get("market_code"), "currency": item["currency"],
            "quantity": item.get("quantity"), "unit_price": item.get("unit_price"),
            "price_date": item.get("price_date"), "original_value": item["original_value"],
            "exchange_rate": item.get("exchange_rate"), "krw_value": item.get("krw_value"),
            "cost_basis": item.get("cost_basis"), "unrealized_profit": item.get("unrealized_profit"),
            "data_status": item["data_status"], "error_message": item.get("error_message"),
        }
        placeholders = ", ".join("?" for _ in _ITEM_COLUMNS)
        conn.execute(
            f"INSERT INTO portfolio_snapshot_items ({', '.join(_ITEM_COLUMNS)}) VALUES ({placeholders})",
            [row[c] for c in _ITEM_COLUMNS],
        )

    return get_snapshot(conn, snapshot_date)  # type: ignore[return-value]
