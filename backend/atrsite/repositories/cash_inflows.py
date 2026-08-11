"""backend/atrsite/repositories/cash_inflows.py -- 현금 입금기록(v1.4) 리포지토리.

cash_withdrawals(v1.1)의 정반대 짝. withdrawals.py와 거의 동일한 구조를
그대로 따른다(스펙 10절 "중요한 비연동 원칙"과 동일: deposits/포지션/거래이력을
자동으로 갱신하지 않는 완전히 독립적인 기록).

deposited_at은 withdrawn_at과 동일한 이유로 Asia/Seoul naive 문자열
(YYYY-MM-DDTHH:MM:SS)로 저장/비교한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from ..utils import new_id, utcnow_iso

SUPPORTED_CURRENCIES = ("KRW", "USD", "JPY")
SUPPORTED_FLOW_TYPES = ("EXTERNAL", "INTERNAL_TRANSFER")
MAX_SOURCE_LENGTH = 100


class CashInflowValidationError(ValueError):
    """서비스/API 계층이 400으로 변환할 입력 검증 오류."""


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "deposited_at": row["deposited_at"],
        "deposit_account_id": row["deposit_account_id"],
        "account_name_snapshot": row["account_name_snapshot"],
        "source": row["source"],
        "amount": row["amount"],
        "currency": row["currency"],
        "memo": row["memo"],
        "flow_type": row["flow_type"],
        "is_edited": bool(row["edited"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validate_source(source: str) -> str:
    cleaned = (source or "").strip()
    if not cleaned:
        raise CashInflowValidationError("출처를 입력하세요.")
    if len(cleaned) > MAX_SOURCE_LENGTH:
        raise CashInflowValidationError(f"출처는 최대 {MAX_SOURCE_LENGTH}자까지 입력할 수 있습니다.")
    return cleaned


def _validate_amount(amount: float) -> float:
    if amount is None or amount <= 0:
        raise CashInflowValidationError("입금액은 0보다 커야 합니다.")
    return amount


def _validate_currency(currency: str) -> str:
    if currency not in SUPPORTED_CURRENCIES:
        raise CashInflowValidationError(
            f"지원하지 않는 통화입니다: {currency} (지원: {', '.join(SUPPORTED_CURRENCIES)})"
        )
    return currency


def _validate_flow_type(flow_type: str) -> str:
    if flow_type not in SUPPORTED_FLOW_TYPES:
        raise CashInflowValidationError(
            f"지원하지 않는 flow_type입니다: {flow_type} (지원: {', '.join(SUPPORTED_FLOW_TYPES)})"
        )
    return flow_type


def _validate_deposited_at(deposited_at: str) -> str:
    """'YYYY-MM-DDTHH:MM' 또는 'YYYY-MM-DDTHH:MM:SS' 형식만 허용 -- withdrawals.py와
    동일하게 datetime-local input이 그대로 오는 형태를 받는다."""
    value = (deposited_at or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise CashInflowValidationError(f"입금 일시 형식이 올바르지 않습니다: {deposited_at!r}")


def create_cash_inflow(
    conn: sqlite3.Connection,
    *,
    deposited_at: str,
    deposit_account_id: str,
    source: str,
    amount: float,
    currency: str,
    flow_type: str = "EXTERNAL",
    memo: str | None = None,
) -> dict[str, Any]:
    deposit = conn.execute(
        "SELECT id, account_name FROM deposits WHERE id = ?", (deposit_account_id,)
    ).fetchone()
    if deposit is None:
        raise CashInflowValidationError(f"존재하지 않는 예금계좌입니다: {deposit_account_id}")

    inflow_id = new_id()
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO cash_inflows
            (id, deposited_at, deposit_account_id, account_name_snapshot, source,
             amount, currency, memo, flow_type, edited, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            inflow_id,
            _validate_deposited_at(deposited_at),
            deposit_account_id,
            deposit["account_name"],
            _validate_source(source),
            _validate_amount(amount),
            _validate_currency(currency),
            (memo or "").strip() or None,
            _validate_flow_type(flow_type),
            now,
            now,
        ),
    )
    return get_cash_inflow(conn, inflow_id)  # type: ignore[return-value]


def get_cash_inflow(conn: sqlite3.Connection, inflow_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM cash_inflows WHERE id = ?", (inflow_id,)).fetchone()
    return _row_to_dict(row) if row else None


@dataclass
class CashInflowFilter:
    start_date: Optional[str] = None  # 'YYYY-MM-DD', deposited_at >= 이 날짜 00:00
    end_date: Optional[str] = None    # 'YYYY-MM-DD', deposited_at < 다음날 00:00(포함 종료일)
    source: Optional[str] = None      # 부분일치, 대소문자 무시
    deposit_account_id: Optional[str] = None
    currency: Optional[str] = None
    flow_type: Optional[str] = None


def _apply_filters(where: list[str], params: list[Any], f: CashInflowFilter) -> None:
    if f.start_date:
        where.append("deposited_at >= ?")
        params.append(f"{f.start_date}T00:00:00")
    if f.end_date:
        end_exclusive = (datetime.strptime(f.end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        where.append("deposited_at < ?")
        params.append(f"{end_exclusive}T00:00:00")
    if f.source:
        where.append("LOWER(source) LIKE ? ESCAPE '\\'")
        escaped = f.source.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    if f.deposit_account_id:
        where.append("deposit_account_id = ?")
        params.append(f.deposit_account_id)
    if f.currency:
        where.append("currency = ?")
        params.append(f.currency)
    if f.flow_type:
        where.append("flow_type = ?")
        params.append(f.flow_type)


def list_cash_inflows(
    conn: sqlite3.Connection,
    f: CashInflowFilter,
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
        f"SELECT COUNT(*) AS n FROM cash_inflows {where_sql}", params
    ).fetchone()["n"]

    order = "DESC" if sort_desc else "ASC"
    rows = conn.execute(
        f"""
        SELECT * FROM cash_inflows {where_sql}
        ORDER BY deposited_at {order}, id {order}
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def list_all_matching(conn: sqlite3.Connection, f: CashInflowFilter) -> list[dict[str, Any]]:
    """CSV 내보내기용 -- 페이지네이션 없이 필터 조건에 맞는 전체 기록."""
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT * FROM cash_inflows {where_sql} ORDER BY deposited_at DESC, id DESC", params
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def sum_by_currency(conn: sqlite3.Connection, f: CashInflowFilter) -> dict[str, float]:
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT currency, SUM(amount) AS total FROM cash_inflows {where_sql} GROUP BY currency",
        params,
    ).fetchall()
    return {r["currency"]: r["total"] for r in rows}


def sum_by_account(conn: sqlite3.Connection, f: CashInflowFilter) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"""
        SELECT deposit_account_id, account_name_snapshot, currency, SUM(amount) AS total, COUNT(*) AS n
        FROM cash_inflows {where_sql}
        GROUP BY deposit_account_id, account_name_snapshot, currency
        ORDER BY account_name_snapshot ASC, currency ASC
        """,
        params,
    ).fetchall()
    return [
        {
            "deposit_account_id": r["deposit_account_id"],
            "account_name_snapshot": r["account_name_snapshot"],
            "currency": r["currency"],
            "total": r["total"],
            "count": r["n"],
        }
        for r in rows
    ]


def _period_bounds(now: datetime) -> dict[str, tuple[str, str]]:
    """withdrawals.py의 _period_bounds와 동일한 정의(시작 이상/다음 기간 시작 미만)."""
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


def period_summary(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, dict[str, float]]:
    """오늘/이번주/이번달/YTD 4개 요약 카드, 통화별로 합산해서 반환(withdrawals.py와 동일)."""
    bounds = _period_bounds(now or datetime.now())
    result: dict[str, dict[str, float]] = {}
    for key, (start, end) in bounds.items():
        rows = conn.execute(
            "SELECT currency, SUM(amount) AS total FROM cash_inflows "
            "WHERE deposited_at >= ? AND deposited_at < ? GROUP BY currency",
            (start, end),
        ).fetchall()
        result[key] = {r["currency"]: r["total"] for r in rows}
    return result


def recent_sources(conn: sqlite3.Connection, *, limit: int = 5) -> list[str]:
    """최근/자주 쓴 출처 추천 -- withdrawals.py의 recent_purposes와 동일한 방식."""
    rows = conn.execute(
        "SELECT source, MAX(deposited_at) AS last_used, COUNT(*) AS n "
        "FROM (SELECT source, deposited_at FROM cash_inflows ORDER BY deposited_at DESC LIMIT 30) "
        "GROUP BY source ORDER BY n DESC, last_used DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["source"] for r in rows]


def find_possible_duplicate(
    conn: sqlite3.Connection,
    *,
    deposit_account_id: str,
    amount: float,
    currency: str,
    deposited_at: str,
    source: str,
    exclude_id: str | None = None,
) -> dict[str, Any] | None:
    query = (
        "SELECT * FROM cash_inflows WHERE deposit_account_id = ? AND amount = ? "
        "AND currency = ? AND deposited_at = ? AND source = ?"
    )
    params: list[Any] = [deposit_account_id, amount, currency, _validate_deposited_at(deposited_at), source.strip()]
    if exclude_id:
        query += " AND id != ?"
        params.append(exclude_id)
    row = conn.execute(query + " LIMIT 1", params).fetchone()
    return _row_to_dict(row) if row else None


def update_cash_inflow(
    conn: sqlite3.Connection,
    inflow_id: str,
    *,
    deposited_at: str | None = None,
    deposit_account_id: str | None = None,
    source: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    flow_type: str | None = None,
    memo: str | None = None,  # None=유지, ""(빈 문자열)=메모 지우기.
) -> dict[str, Any] | None:
    current = get_cash_inflow(conn, inflow_id)
    if current is None:
        return None

    account_name_snapshot = current["account_name_snapshot"]
    new_account_id = current["deposit_account_id"]
    if deposit_account_id is not None:
        deposit = conn.execute(
            "SELECT id, account_name FROM deposits WHERE id = ?", (deposit_account_id,)
        ).fetchone()
        if deposit is None:
            raise CashInflowValidationError(f"존재하지 않는 예금계좌입니다: {deposit_account_id}")
        new_account_id = deposit_account_id
        account_name_snapshot = deposit["account_name"]

    now = utcnow_iso()
    conn.execute(
        """
        UPDATE cash_inflows SET
            deposited_at = ?, deposit_account_id = ?, account_name_snapshot = ?,
            source = ?, amount = ?, currency = ?, flow_type = ?, memo = ?, edited = 1, updated_at = ?
        WHERE id = ?
        """,
        (
            _validate_deposited_at(deposited_at) if deposited_at is not None else current["deposited_at"],
            new_account_id,
            account_name_snapshot,
            _validate_source(source) if source is not None else current["source"],
            _validate_amount(amount) if amount is not None else current["amount"],
            _validate_currency(currency) if currency is not None else current["currency"],
            _validate_flow_type(flow_type) if flow_type is not None else current["flow_type"],
            current["memo"] if memo is None else (memo.strip() or None),
            now,
            inflow_id,
        ),
    )
    return get_cash_inflow(conn, inflow_id)


def delete_cash_inflow(conn: sqlite3.Connection, inflow_id: str) -> bool:
    cur = conn.execute("DELETE FROM cash_inflows WHERE id = ?", (inflow_id,))
    return cur.rowcount > 0
