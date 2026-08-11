"""backend/atrsite/repositories/cash_ledger.py -- 입출금 통합 장부(v1.5,
2026-08-12 추가) 리포지토리.

cash_withdrawals(v1.1)/cash_inflows(v1.4)를 하나로 합친 것. 원래 출금기록은
카드사용/소비형태 분석까지 염두에 두고 만든 페이지였는데, 그 역할은 이제
card-kunoh/Kunoh's Sheet가 별도 사이트로 담당하게 돼서(사용자, 2026-08-12)
이 장부는 "입출금을 간단히 기록해서 analyze.kunoh.top에 순현금흐름 데이터를
제공"하는 목적으로 축소됨 -- 그래서 purpose/source 같은 분류 필드, 최근
용도추천, 계좌별 합계 펼치기, 중복입력 경고는 전부 뺐다(memo 자유서술만
남김). 기존 데이터의 purpose/source는 db.py의 마이그레이션에서 memo로
접어 넣었으므로 유실 없음.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from ..utils import new_id, utcnow_iso

SUPPORTED_CURRENCIES = ("KRW", "USD", "JPY")
ENTRY_TYPES = ("EXTERNAL_IN", "EXTERNAL_OUT", "INTERNAL_IN", "INTERNAL_OUT")
IN_TYPES = ("EXTERNAL_IN", "INTERNAL_IN")
OUT_TYPES = ("EXTERNAL_OUT", "INTERNAL_OUT")


class CashLedgerValidationError(ValueError):
    """서비스/API 계층이 400으로 변환할 입력 검증 오류."""


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "occurred_at": row["occurred_at"],
        "deposit_account_id": row["deposit_account_id"],
        "account_name_snapshot": row["account_name_snapshot"],
        "entry_type": row["entry_type"],
        "direction": "IN" if row["entry_type"] in IN_TYPES else "OUT",
        "amount": row["amount"],
        "currency": row["currency"],
        "memo": row["memo"],
        "is_edited": bool(row["edited"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validate_amount(amount: float) -> float:
    if amount is None or amount <= 0:
        raise CashLedgerValidationError("금액은 0보다 커야 합니다.")
    return amount


def _validate_currency(currency: str) -> str:
    if currency not in SUPPORTED_CURRENCIES:
        raise CashLedgerValidationError(
            f"지원하지 않는 통화입니다: {currency} (지원: {', '.join(SUPPORTED_CURRENCIES)})"
        )
    return currency


def _validate_entry_type(entry_type: str) -> str:
    if entry_type not in ENTRY_TYPES:
        raise CashLedgerValidationError(
            f"지원하지 않는 구분입니다: {entry_type} (지원: {', '.join(ENTRY_TYPES)})"
        )
    return entry_type


def _validate_occurred_at(occurred_at: str) -> str:
    """'YYYY-MM-DDTHH:MM' 또는 'YYYY-MM-DDTHH:MM:SS' 형식만 허용 -- withdrawals.py/
    cash_inflows.py와 동일하게 datetime-local input이 그대로 오는 형태."""
    value = (occurred_at or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise CashLedgerValidationError(f"일시 형식이 올바르지 않습니다: {occurred_at!r}")


def create_entry(
    conn: sqlite3.Connection,
    *,
    occurred_at: str,
    deposit_account_id: str,
    entry_type: str,
    amount: float,
    currency: str,
    memo: str | None = None,
) -> dict[str, Any]:
    deposit = conn.execute(
        "SELECT id, account_name FROM deposits WHERE id = ?", (deposit_account_id,)
    ).fetchone()
    if deposit is None:
        raise CashLedgerValidationError(f"존재하지 않는 예금계좌입니다: {deposit_account_id}")

    entry_id = new_id()
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO cash_ledger
            (id, occurred_at, deposit_account_id, account_name_snapshot, entry_type,
             amount, currency, memo, edited, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            entry_id,
            _validate_occurred_at(occurred_at),
            deposit_account_id,
            deposit["account_name"],
            _validate_entry_type(entry_type),
            _validate_amount(amount),
            _validate_currency(currency),
            (memo or "").strip() or None,
            now,
            now,
        ),
    )
    return get_entry(conn, entry_id)  # type: ignore[return-value]


def get_entry(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM cash_ledger WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_dict(row) if row else None


@dataclass
class CashLedgerFilter:
    start_date: Optional[str] = None  # 'YYYY-MM-DD', occurred_at >= 이 날짜 00:00
    end_date: Optional[str] = None    # 'YYYY-MM-DD', occurred_at < 다음날 00:00(포함 종료일)
    deposit_account_id: Optional[str] = None
    currency: Optional[str] = None
    entry_type: Optional[str] = None


def _apply_filters(where: list[str], params: list[Any], f: CashLedgerFilter) -> None:
    if f.start_date:
        where.append("occurred_at >= ?")
        params.append(f"{f.start_date}T00:00:00")
    if f.end_date:
        end_exclusive = (datetime.strptime(f.end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        where.append("occurred_at < ?")
        params.append(f"{end_exclusive}T00:00:00")
    if f.deposit_account_id:
        where.append("deposit_account_id = ?")
        params.append(f.deposit_account_id)
    if f.currency:
        where.append("currency = ?")
        params.append(f.currency)
    if f.entry_type:
        where.append("entry_type = ?")
        params.append(f.entry_type)


def list_entries(
    conn: sqlite3.Connection,
    f: CashLedgerFilter,
    *,
    limit: int = 50,
    offset: int = 0,
    sort_desc: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM cash_ledger {where_sql}", params
    ).fetchone()["n"]

    order = "DESC" if sort_desc else "ASC"
    rows = conn.execute(
        f"""
        SELECT * FROM cash_ledger {where_sql}
        ORDER BY occurred_at {order}, id {order}
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def list_all_matching(conn: sqlite3.Connection, f: CashLedgerFilter) -> list[dict[str, Any]]:
    """CSV 내보내기용 -- 페이지네이션 없이 필터 조건에 맞는 전체 기록."""
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT * FROM cash_ledger {where_sql} ORDER BY occurred_at DESC, id DESC", params
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def sum_by_currency(conn: sqlite3.Connection, f: CashLedgerFilter) -> dict[str, dict[str, float]]:
    """통화별 {in, out, net} -- 필터 조건에 맞는 "전체"(페이지 무관) 기록을 합산."""
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT currency, entry_type, SUM(amount) AS total FROM cash_ledger {where_sql} "
        f"GROUP BY currency, entry_type",
        params,
    ).fetchall()
    result: dict[str, dict[str, float]] = {}
    for r in rows:
        bucket = result.setdefault(r["currency"], {"in": 0.0, "out": 0.0, "net": 0.0})
        if r["entry_type"] in IN_TYPES:
            bucket["in"] += r["total"]
        else:
            bucket["out"] += r["total"]
    for bucket in result.values():
        bucket["net"] = bucket["in"] - bucket["out"]
    return result


def _period_bounds(now: datetime) -> dict[str, tuple[str, str]]:
    """withdrawals.py/cash_inflows.py와 동일한 정의(시작 이상/다음 기간 시작 미만)."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    week_start = today_start - timedelta(days=today_start.weekday())
    next_week_start = week_start + timedelta(days=7)

    month_start = today_start.replace(day=1)
    next_month_start = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    year_start = today_start.replace(month=1, day=1)
    next_year_start = year_start.replace(year=year_start.year + 1)

    fmt = "%Y-%m-%dT%H:%M:%S"
    return {
        "today": (today_start.strftime(fmt), tomorrow_start.strftime(fmt)),
        "this_week": (week_start.strftime(fmt), next_week_start.strftime(fmt)),
        "this_month": (month_start.strftime(fmt), next_month_start.strftime(fmt)),
        "ytd": (year_start.strftime(fmt), next_year_start.strftime(fmt)),
    }


def period_summary(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, dict[str, dict[str, float]]]:
    """오늘/이번주/이번달/YTD, 통화별 {in, out, net}."""
    bounds = _period_bounds(now or datetime.now())
    result: dict[str, dict[str, dict[str, float]]] = {}
    for key, (start, end) in bounds.items():
        rows = conn.execute(
            "SELECT currency, entry_type, SUM(amount) AS total FROM cash_ledger "
            "WHERE occurred_at >= ? AND occurred_at < ? GROUP BY currency, entry_type",
            (start, end),
        ).fetchall()
        bucket_by_currency: dict[str, dict[str, float]] = {}
        for r in rows:
            bucket = bucket_by_currency.setdefault(r["currency"], {"in": 0.0, "out": 0.0, "net": 0.0})
            if r["entry_type"] in IN_TYPES:
                bucket["in"] += r["total"]
            else:
                bucket["out"] += r["total"]
        for bucket in bucket_by_currency.values():
            bucket["net"] = bucket["in"] - bucket["out"]
        result[key] = bucket_by_currency
    return result


def update_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    occurred_at: str | None = None,
    deposit_account_id: str | None = None,
    entry_type: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    memo: str | None = None,  # None=유지, ""(빈 문자열)=메모 지우기.
) -> dict[str, Any] | None:
    current = get_entry(conn, entry_id)
    if current is None:
        return None

    account_name_snapshot = current["account_name_snapshot"]
    new_account_id = current["deposit_account_id"]
    if deposit_account_id is not None:
        deposit = conn.execute(
            "SELECT id, account_name FROM deposits WHERE id = ?", (deposit_account_id,)
        ).fetchone()
        if deposit is None:
            raise CashLedgerValidationError(f"존재하지 않는 예금계좌입니다: {deposit_account_id}")
        new_account_id = deposit_account_id
        account_name_snapshot = deposit["account_name"]

    now = utcnow_iso()
    conn.execute(
        """
        UPDATE cash_ledger SET
            occurred_at = ?, deposit_account_id = ?, account_name_snapshot = ?,
            entry_type = ?, amount = ?, currency = ?, memo = ?, edited = 1, updated_at = ?
        WHERE id = ?
        """,
        (
            _validate_occurred_at(occurred_at) if occurred_at is not None else current["occurred_at"],
            new_account_id,
            account_name_snapshot,
            _validate_entry_type(entry_type) if entry_type is not None else current["entry_type"],
            _validate_amount(amount) if amount is not None else current["amount"],
            _validate_currency(currency) if currency is not None else current["currency"],
            current["memo"] if memo is None else (memo.strip() or None),
            now,
            entry_id,
        ),
    )
    return get_entry(conn, entry_id)


def delete_entry(conn: sqlite3.Connection, entry_id: str) -> bool:
    cur = conn.execute("DELETE FROM cash_ledger WHERE id = ?", (entry_id,))
    return cur.rowcount > 0
