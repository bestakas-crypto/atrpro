"""backend/atrsite/repositories/withdrawals.py -- 현금 출금기록(v1.1) 리포지토리.

스펙(v1.1 요구사항 10절 "중요한 비연동 원칙"): 이 모듈은 deposits/instruments/
trades를 절대 자동으로 갱신하지 않는다. 순수하게 독립적인 개인용 출금 장부다.

withdrawn_at은 Asia/Seoul naive 문자열(YYYY-MM-DDTHH:MM:SS)로 저장/비교한다 --
서버 시스템 시간대 자체가 Asia/Seoul로 맞춰져 있어(market_schedule.py와 동일
전제) datetime.now()를 그대로 KST로 취급해도 된다. created_at/updated_at은
기존 관례대로 UTC ISO(utils.utcnow_iso())를 그대로 쓴다 -- 이건 감사(audit)용
타임스탬프라 나머지 테이블들과 통일하는 게 맞고, withdrawn_at만 스펙 3.1의
기간별 합산 요구사항 때문에 예외적으로 KST-naive로 다르게 저장한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from ..utils import new_id, utcnow_iso

SUPPORTED_CURRENCIES = ("KRW", "USD", "JPY")
MAX_PURPOSE_LENGTH = 100
# v1.4(2026-08-12) analyze.kunoh.top 1단계 -- cash_inflows.py의 SUPPORTED_FLOW_TYPES와 동일.
SUPPORTED_FLOW_TYPES = ("EXTERNAL", "INTERNAL_TRANSFER")


class WithdrawalValidationError(ValueError):
    """서비스/API 계층이 400으로 변환할 입력 검증 오류."""


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "withdrawn_at": row["withdrawn_at"],
        "deposit_account_id": row["deposit_account_id"],
        "account_name_snapshot": row["account_name_snapshot"],
        "purpose": row["purpose"],
        "amount": row["amount"],
        "currency": row["currency"],
        "memo": row["memo"],
        # v1.4 -- flow_type 컬럼이 없는 DB(마이그레이션 전)에서도 죽지 않게 기본값.
        "flow_type": row["flow_type"] if "flow_type" in row.keys() else "EXTERNAL",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        # 스펙 9.5 "수정됨" 표시 -- edited 컬럼(명시적 플래그)으로 판단한다.
        # 처음엔 updated_at != created_at으로 비교했는데, 생성 직후 같은 초
        # 안에서 수정되면(초 단위 정밀도) 구분이 안 되는 버그가 있었음
        # (2026-08-05 테스트로 실제 발견, schema.py의 edited 컬럼 주석 참고).
        "is_edited": bool(row["edited"]),
    }


def _validate_purpose(purpose: str) -> str:
    cleaned = purpose.strip()
    if not cleaned:
        raise WithdrawalValidationError("용도를 입력하세요.")
    if len(cleaned) > MAX_PURPOSE_LENGTH:
        raise WithdrawalValidationError(f"용도는 최대 {MAX_PURPOSE_LENGTH}자까지 입력할 수 있습니다.")
    return cleaned


def _validate_amount(amount: float) -> float:
    if amount is None or amount <= 0:
        raise WithdrawalValidationError("출금액은 0보다 커야 합니다.")
    return amount


def _validate_currency(currency: str) -> str:
    if currency not in SUPPORTED_CURRENCIES:
        raise WithdrawalValidationError(
            f"지원하지 않는 통화입니다: {currency} (지원: {', '.join(SUPPORTED_CURRENCIES)})"
        )
    return currency


def _validate_flow_type(flow_type: str) -> str:
    if flow_type not in SUPPORTED_FLOW_TYPES:
        raise WithdrawalValidationError(
            f"지원하지 않는 flow_type입니다: {flow_type} (지원: {', '.join(SUPPORTED_FLOW_TYPES)})"
        )
    return flow_type


def _validate_withdrawn_at(withdrawn_at: str) -> str:
    """'YYYY-MM-DDTHH:MM' 또는 'YYYY-MM-DDTHH:MM:SS' 형식만 허용 -- datetime-local
    input이 그대로 오는 형태. 초가 없으면 :00을 붙여 통일한다."""
    value = (withdrawn_at or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise WithdrawalValidationError(f"출금 일시 형식이 올바르지 않습니다: {withdrawn_at!r}")


def create_withdrawal(
    conn: sqlite3.Connection,
    *,
    withdrawn_at: str,
    deposit_account_id: str,
    purpose: str,
    amount: float,
    currency: str,
    flow_type: str = "EXTERNAL",
    memo: str | None = None,
) -> dict[str, Any]:
    deposit = conn.execute(
        "SELECT id, account_name FROM deposits WHERE id = ?", (deposit_account_id,)
    ).fetchone()
    if deposit is None:
        raise WithdrawalValidationError(f"존재하지 않는 예금계좌입니다: {deposit_account_id}")

    withdrawal_id = new_id()
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO cash_withdrawals
            (id, withdrawn_at, deposit_account_id, account_name_snapshot, purpose,
             amount, currency, memo, flow_type, edited, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            withdrawal_id,
            _validate_withdrawn_at(withdrawn_at),
            deposit_account_id,
            deposit["account_name"],
            _validate_purpose(purpose),
            _validate_amount(amount),
            _validate_currency(currency),
            (memo or "").strip() or None,
            _validate_flow_type(flow_type),
            now,
            now,
        ),
    )
    return get_withdrawal(conn, withdrawal_id)  # type: ignore[return-value]


def get_withdrawal(conn: sqlite3.Connection, withdrawal_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM cash_withdrawals WHERE id = ?", (withdrawal_id,)).fetchone()
    return _row_to_dict(row) if row else None


@dataclass
class WithdrawalFilter:
    start_date: Optional[str] = None  # 'YYYY-MM-DD', withdrawn_at >= 이 날짜 00:00
    end_date: Optional[str] = None    # 'YYYY-MM-DD', withdrawn_at < 다음날 00:00(포함 종료일)
    purpose: Optional[str] = None     # 부분일치, 대소문자 무시
    deposit_account_id: Optional[str] = None
    currency: Optional[str] = None
    flow_type: Optional[str] = None  # v1.4 -- EXTERNAL/INTERNAL_TRANSFER로 순입금 계산 시 필터링용


def _apply_filters(where: list[str], params: list[Any], f: WithdrawalFilter) -> None:
    if f.start_date:
        where.append("withdrawn_at >= ?")
        params.append(f"{f.start_date}T00:00:00")
    if f.end_date:
        # end_date 포함 종료 -- 다음날 00:00 미만으로 비교(스펙 6: 시작 이상/다음
        # 기간 시작 미만 방식, 23:59:59 문자열 비교 지양).
        end_exclusive = (datetime.strptime(f.end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        where.append("withdrawn_at < ?")
        params.append(f"{end_exclusive}T00:00:00")
    if f.purpose:
        # 매개변수 바인딩(LIKE ? ESCAPE)만 사용, 문자열 결합 금지(스펙 5.2).
        # sqlite3의 LIKE는 기본적으로 ASCII만 대소문자 무시하지만 COLLATE NOCASE도
        # ASCII 한정이라 한글은 원래 대소문자 개념이 없어 그대로 부분일치되고,
        # 영문은 lower() 비교로 대소문자 무시를 명시적으로 보장한다.
        where.append("LOWER(purpose) LIKE ? ESCAPE '\\'")
        escaped = f.purpose.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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


def list_withdrawals(
    conn: sqlite3.Connection,
    f: WithdrawalFilter,
    *,
    limit: int = 50,
    offset: int = 0,
    sort_desc: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """(목록, 필터 조건에 맞는 전체 건수)를 함께 돌려준다 -- 스펙 5.2 "검색
    결과의 목록과 합계를 같은 필터 조건으로 계산"을 위해 페이지 무관 총건수도 필요."""
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM cash_withdrawals {where_sql}", params
    ).fetchone()["n"]

    order = "DESC" if sort_desc else "ASC"
    rows = conn.execute(
        f"""
        SELECT * FROM cash_withdrawals {where_sql}
        ORDER BY withdrawn_at {order}, id {order}
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def list_all_matching(conn: sqlite3.Connection, f: WithdrawalFilter) -> list[dict[str, Any]]:
    """스펙 9.3 CSV 내보내기용 -- 페이지네이션 없이 필터 조건에 맞는 전체 기록."""
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT * FROM cash_withdrawals {where_sql} ORDER BY withdrawn_at DESC, id DESC", params
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def sum_by_currency(conn: sqlite3.Connection, f: WithdrawalFilter) -> dict[str, float]:
    """스펙 6/7 -- 통화별로 합산, 환율 환산 없이 통화 코드별 dict로 반환.
    필터 조건에 맞는 "전체"(페이지 무관) 기록을 합산한다."""
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT currency, SUM(amount) AS total FROM cash_withdrawals {where_sql} GROUP BY currency",
        params,
    ).fetchall()
    return {r["currency"]: r["total"] for r in rows}


def sum_by_account(conn: sqlite3.Connection, f: WithdrawalFilter) -> list[dict[str, Any]]:
    """스펙 9.4 계좌별 합계 -- account_name_snapshot 기준(계좌가 삭제/개명돼도
    당시 이름으로 계속 묶인다), 통화별로도 나눈다."""
    where: list[str] = []
    params: list[Any] = []
    _apply_filters(where, params, f)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"""
        SELECT deposit_account_id, account_name_snapshot, currency, SUM(amount) AS total, COUNT(*) AS n
        FROM cash_withdrawals {where_sql}
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
    """스펙 6 "기간 정의" 그대로: 시작 이상, 다음 기간 시작 미만.
    now는 Asia/Seoul naive datetime으로 취급한다(서버 시스템 tz 전제)."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    week_start = today_start - timedelta(days=today_start.weekday())  # 월요일 00:00
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
    """스펙 6 -- 오늘/이번주/이번달/YTD 4개 요약 카드, 통화별로 합산해서 반환.
    now를 주입 가능하게 해서(기본값 datetime.now()) 테스트에서 특정 시각을
    고정해 월말/연말 경계를 검증할 수 있게 한다."""
    bounds = _period_bounds(now or datetime.now())
    result: dict[str, dict[str, float]] = {}
    for key, (start, end) in bounds.items():
        rows = conn.execute(
            "SELECT currency, SUM(amount) AS total FROM cash_withdrawals "
            "WHERE withdrawn_at >= ? AND withdrawn_at < ? GROUP BY currency",
            (start, end),
        ).fetchall()
        result[key] = {r["currency"]: r["total"] for r in rows}
    return result


def recent_purposes(conn: sqlite3.Connection, *, limit: int = 5) -> list[str]:
    """스펙 9.1 -- 최근/자주 쓴 용도 추천. 최근 30건에서 등장 빈도순, 동률이면
    최신순으로 정렬한다."""
    rows = conn.execute(
        "SELECT purpose, MAX(withdrawn_at) AS last_used, COUNT(*) AS n "
        "FROM (SELECT purpose, withdrawn_at FROM cash_withdrawals ORDER BY withdrawn_at DESC LIMIT 30) "
        "GROUP BY purpose ORDER BY n DESC, last_used DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["purpose"] for r in rows]


def find_possible_duplicate(
    conn: sqlite3.Connection,
    *,
    deposit_account_id: str,
    amount: float,
    currency: str,
    withdrawn_at: str,
    purpose: str,
    exclude_id: str | None = None,
) -> dict[str, Any] | None:
    """스펙 9.2 중복입력 경고 -- 계좌·금액·통화·출금시각·용도가 전부 같은 기록이
    이미 있으면 저장을 막지는 않고 그 기록을 반환(프론트가 경고 문구를 띄움)."""
    query = (
        "SELECT * FROM cash_withdrawals WHERE deposit_account_id = ? AND amount = ? "
        "AND currency = ? AND withdrawn_at = ? AND purpose = ?"
    )
    params: list[Any] = [deposit_account_id, amount, currency, _validate_withdrawn_at(withdrawn_at), purpose.strip()]
    if exclude_id:
        query += " AND id != ?"
        params.append(exclude_id)
    row = conn.execute(query + " LIMIT 1", params).fetchone()
    return _row_to_dict(row) if row else None


def update_withdrawal(
    conn: sqlite3.Connection,
    withdrawal_id: str,
    *,
    withdrawn_at: str | None = None,
    deposit_account_id: str | None = None,
    purpose: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    flow_type: str | None = None,
    memo: str | None = None,  # None=유지, ""(빈 문자열)=메모 지우기 -- deposits.py 등과 같은 관례.
) -> dict[str, Any] | None:
    current = get_withdrawal(conn, withdrawal_id)
    if current is None:
        return None

    account_name_snapshot = current["account_name_snapshot"]
    new_account_id = current["deposit_account_id"]
    if deposit_account_id is not None:
        deposit = conn.execute(
            "SELECT id, account_name FROM deposits WHERE id = ?", (deposit_account_id,)
        ).fetchone()
        if deposit is None:
            raise WithdrawalValidationError(f"존재하지 않는 예금계좌입니다: {deposit_account_id}")
        new_account_id = deposit_account_id
        account_name_snapshot = deposit["account_name"]  # 계좌를 바꾸면 스냅샷도 새 계좌 이름으로.

    now = utcnow_iso()
    conn.execute(
        """
        UPDATE cash_withdrawals SET
            withdrawn_at = ?, deposit_account_id = ?, account_name_snapshot = ?,
            purpose = ?, amount = ?, currency = ?, flow_type = ?, memo = ?, edited = 1, updated_at = ?
        WHERE id = ?
        """,
        (
            _validate_withdrawn_at(withdrawn_at) if withdrawn_at is not None else current["withdrawn_at"],
            new_account_id,
            account_name_snapshot,
            _validate_purpose(purpose) if purpose is not None else current["purpose"],
            _validate_amount(amount) if amount is not None else current["amount"],
            _validate_currency(currency) if currency is not None else current["currency"],
            _validate_flow_type(flow_type) if flow_type is not None else current["flow_type"],
            current["memo"] if memo is None else (memo.strip() or None),
            now,
            withdrawal_id,
        ),
    )
    return get_withdrawal(conn, withdrawal_id)


def delete_withdrawal(conn: sqlite3.Connection, withdrawal_id: str) -> bool:
    cur = conn.execute("DELETE FROM cash_withdrawals WHERE id = ?", (withdrawal_id,))
    return cur.rowcount > 0
