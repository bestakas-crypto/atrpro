"""backend/atrsite/repositories/schedules.py -- 투자 스케줄(v1.2) 리포지토리.

investment_plans/investment_plan_items/investment_schedules/investment_schedule_items/
schedule_occurrences/schedule_occurrence_items/schedule_executions 전체를 다룬다.
v1.1(cash_withdrawals)과 마찬가지로 conn.commit()은 여기서 하지 않는다 -- api/deps.py의
get_conn()이 요청 단위로 commit/rollback을 담당한다(스펙 참고: 기존 관례 재사용).

핵심 원칙(스펙 재확인):
  - 스케줄/회차 생성은 "계획"일 뿐이다. trades/cash_withdrawals/deposits를 이 모듈이
    직접 만들거나 잔액을 바꾸는 일은 절대 없다 -- schedule_executions로만 실제 발생
    사실을 별도 기록/연결한다.
  - 계좌 개념은 deposits뿐이라 deposit_account_id(FK, ON DELETE SET NULL) +
    account_name_snapshot을 cash_withdrawals와 동일하게 재사용한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from ..services import schedule_engine
from ..utils import new_id, utcnow_iso

SCHEDULE_TYPES = (
    "BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "REBALANCE", "RULE_REVIEW", "MATURITY",
    "DIVIDEND_CHECK", "INTEREST_CHECK", "EXCHANGE", "DISCLOSURE_CHECK", "TAX_CHECK", "GENERAL",
)
PLAN_STATUSES = ("ACTIVE", "PAUSED", "COMPLETED", "CANCELLED", "ARCHIVED")
SCHEDULE_STATUSES = ("ACTIVE", "PAUSED", "COMPLETED", "CANCELLED")
OCCURRENCE_STATUSES = (
    "SCHEDULED", "NOTIFIED", "PARTIALLY_EXECUTED", "COMPLETED", "SKIPPED", "CANCELLED", "ARCHIVED",
)
# 배지 카운트에서 애초에 제외되는 확정 상태(스펙: 완료/건너뜀/취소/보관 + 미래일정 제외).
_BADGE_EXCLUDED_STATUSES = frozenset({"COMPLETED", "SKIPPED", "CANCELLED", "ARCHIVED"})


class ScheduleValidationError(ValueError):
    """서비스/API 계층이 400으로 변환할 입력 검증 오류."""


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

def _lookup_deposit_snapshot(conn: sqlite3.Connection, deposit_account_id: Optional[str]) -> Optional[str]:
    if not deposit_account_id:
        return None
    row = conn.execute("SELECT account_name FROM deposits WHERE id = ?", (deposit_account_id,)).fetchone()
    if row is None:
        raise ScheduleValidationError(f"존재하지 않는 deposit_account_id: {deposit_account_id}")
    return row["account_name"]


def _lookup_instrument_snapshot(conn: sqlite3.Connection, instrument_id: str) -> str:
    row = conn.execute("SELECT name FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    if row is None:
        raise ScheduleValidationError(f"존재하지 않는 instrument_id: {instrument_id}")
    return row["name"]


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ScheduleValidationError(f"{field}는 YYYY-MM-DD 형식이어야 합니다: {value!r}") from exc


# ---------------------------------------------------------------------------
# investment_plans
# ---------------------------------------------------------------------------

def _plan_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"],
        "deposit_account_id": row["deposit_account_id"],
        "account_name_snapshot": row["account_name_snapshot"],
        "total_amount": row["total_amount"], "currency": row["currency"],
        "status": row["status"], "memo": row["memo"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _plan_item_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "plan_id": row["plan_id"],
        "instrument_id": row["instrument_id"],
        "instrument_name_snapshot": row["instrument_name_snapshot"],
        "ratio_percent": row["ratio_percent"],
    }


def create_plan(
    conn: sqlite3.Connection, *, name: str, deposit_account_id: Optional[str] = None,
    total_amount: Optional[float] = None, currency: str = "KRW", memo: Optional[str] = None,
    items: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if not name.strip():
        raise ScheduleValidationError("name은 비어 있을 수 없습니다")
    account_snapshot = _lookup_deposit_snapshot(conn, deposit_account_id)
    plan_id = new_id()
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO investment_plans "
        "(id, name, deposit_account_id, account_name_snapshot, total_amount, currency, status, memo, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
        (plan_id, name.strip(), deposit_account_id, account_snapshot, total_amount, currency, memo, now, now),
    )
    for item in items or []:
        _insert_plan_item(conn, plan_id=plan_id, instrument_id=item["instrument_id"], ratio_percent=item["ratio_percent"])
    return get_plan(conn, plan_id)  # type: ignore[return-value]


def _insert_plan_item(conn: sqlite3.Connection, *, plan_id: str, instrument_id: str, ratio_percent: float) -> None:
    if ratio_percent <= 0:
        raise ScheduleValidationError("ratio_percent는 0보다 커야 합니다")
    snapshot = _lookup_instrument_snapshot(conn, instrument_id)
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO investment_plan_items "
        "(id, plan_id, instrument_id, instrument_name_snapshot, ratio_percent, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), plan_id, instrument_id, snapshot, ratio_percent, now, now),
    )


def get_plan(conn: sqlite3.Connection, plan_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM investment_plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        return None
    plan = _plan_row_to_dict(row)
    items = conn.execute(
        "SELECT * FROM investment_plan_items WHERE plan_id = ? ORDER BY created_at ASC", (plan_id,)
    ).fetchall()
    plan["items"] = [_plan_item_row_to_dict(r) for r in items]
    return plan


def list_plans(conn: sqlite3.Connection, *, status: Optional[str] = None) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM investment_plans WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM investment_plans ORDER BY created_at DESC").fetchall()
    return [_plan_row_to_dict(r) for r in rows]


def update_plan(
    conn: sqlite3.Connection, plan_id: str, *, name: Optional[str] = None,
    deposit_account_id: Optional[str] = "__unset__", total_amount: Optional[float] = "__unset__",  # type: ignore[assignment]
    currency: Optional[str] = None, status: Optional[str] = None, memo: Optional[str] = "__unset__",  # type: ignore[assignment]
) -> Optional[dict[str, Any]]:
    existing = get_plan(conn, plan_id)
    if existing is None:
        return None
    if status is not None and status not in PLAN_STATUSES:
        raise ScheduleValidationError(f"지원하지 않는 status: {status}")

    fields: dict[str, Any] = {}
    if name is not None:
        if not name.strip():
            raise ScheduleValidationError("name은 비어 있을 수 없습니다")
        fields["name"] = name.strip()
    if deposit_account_id != "__unset__":
        fields["deposit_account_id"] = deposit_account_id
        fields["account_name_snapshot"] = _lookup_deposit_snapshot(conn, deposit_account_id)
    if total_amount != "__unset__":
        fields["total_amount"] = total_amount
    if currency is not None:
        fields["currency"] = currency
    if status is not None:
        fields["status"] = status
    if memo != "__unset__":
        fields["memo"] = memo

    if not fields:
        return existing
    fields["updated_at"] = utcnow_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE investment_plans SET {set_clause} WHERE id = ?", (*fields.values(), plan_id))
    return get_plan(conn, plan_id)


def delete_plan(conn: sqlite3.Connection, plan_id: str) -> bool:
    """ON DELETE CASCADE로 investment_schedules까지 함께 지워진다 -- 계획 삭제는
    거기 딸린 예약 일정도 같이 사라지는 게 맞다(둘 다 아직 '계획' 단계이므로).
    이미 실행 완료(schedule_executions)된 회차라도 occurrence가 지워지면 실행기록도
    같이 지워진다(cascade) -- 이는 계획 삭제라는 명시적 사용자 행위의 결과이며
    v1.1 cash_withdrawals(별도 독립 장부)와는 성격이 다르다."""
    cur = conn.execute("DELETE FROM investment_plans WHERE id = ?", (plan_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# investment_schedules + occurrences 생성
# ---------------------------------------------------------------------------

def _schedule_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "plan_id": row["plan_id"], "schedule_type": row["schedule_type"],
        "title": row["title"], "market": row["market"],
        "recurrence_type": row["recurrence_type"], "recurrence_interval": row["recurrence_interval"],
        "start_date": row["start_date"], "end_date": row["end_date"],
        "occurrence_count": row["occurrence_count"], "holiday_policy": row["holiday_policy"],
        "notify_days_before": row["notify_days_before"],
        "total_amount": row["total_amount"], "currency": row["currency"],
        "deposit_account_id": row["deposit_account_id"], "account_name_snapshot": row["account_name_snapshot"],
        "status": row["status"], "memo": row["memo"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _schedule_item_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "schedule_id": row["schedule_id"], "instrument_id": row["instrument_id"],
        "instrument_name_snapshot": row["instrument_name_snapshot"], "ratio_percent": row["ratio_percent"],
    }


def _occurrence_row_to_dict(row: sqlite3.Row, *, today: Optional[date] = None) -> dict[str, Any]:
    today = today or datetime.now().date()
    status = row["status"]
    adjusted = date.fromisoformat(row["adjusted_scheduled_date"])
    is_overdue = status in ("SCHEDULED", "NOTIFIED") and adjusted < today
    result = {
        "id": row["id"], "schedule_id": row["schedule_id"], "occurrence_index": row["occurrence_index"],
        "original_scheduled_date": row["original_scheduled_date"],
        "adjusted_scheduled_date": row["adjusted_scheduled_date"],
        "needs_holiday_confirmation": bool(row["needs_holiday_confirmation"]),
        "planned_amount": row["planned_amount"], "currency": row["currency"],
        "status": status, "acknowledged": bool(row["acknowledged"]),
        "is_overdue": is_overdue,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    # list_occurrences()/get_occurrence()/today_occurrences()는 investment_schedules와
    # JOIN해서 title/schedule_type/market도 함께 담아준다 -- 프런트가 회차만 보고도
    # 어떤 일정인지 알 수 있게(추가 조회 왕복 없이). get_schedule() 안의 occurrences
    # 목록은 이미 같은 스케줄 컨텍스트라 이 컬럼들이 없다(row.keys()로 있을 때만 포함).
    keys = row.keys()
    if "schedule_title" in keys:
        result["schedule_title"] = row["schedule_title"]
    if "schedule_type" in keys:
        result["schedule_type"] = row["schedule_type"]
    if "market" in keys:
        result["market"] = row["market"]
    return result


def create_schedule(
    conn: sqlite3.Connection, *, plan_id: Optional[str], schedule_type: str, title: str,
    market: str = "NONE", recurrence_type: str, recurrence_interval: int = 1,
    start_date: str, end_date: Optional[str] = None, occurrence_count: Optional[int] = None,
    custom_dates: Optional[list[str]] = None, holiday_policy: str = "NEXT_BUSINESS_DAY",
    notify_days_before: int = 0,
    total_amount: Optional[float] = None, currency: str = "KRW",
    deposit_account_id: Optional[str] = None, memo: Optional[str] = None,
    items: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if not title.strip():
        raise ScheduleValidationError("title은 비어 있을 수 없습니다")
    if schedule_type not in SCHEDULE_TYPES:
        raise ScheduleValidationError(f"지원하지 않는 schedule_type: {schedule_type}")
    if plan_id is not None and get_plan(conn, plan_id) is None:
        raise ScheduleValidationError(f"존재하지 않는 plan_id: {plan_id}")

    account_snapshot = _lookup_deposit_snapshot(conn, deposit_account_id)

    start_d = _parse_date(start_date, "start_date")
    end_d = _parse_date(end_date, "end_date") if end_date else None
    custom_ds = [_parse_date(d, "custom_dates") for d in custom_dates] if custom_dates else None

    try:
        dates = schedule_engine.generate_occurrence_dates(
            start_date=start_d, recurrence_type=recurrence_type, interval=recurrence_interval,
            end_date=end_d, occurrence_count=occurrence_count, custom_dates=custom_ds,
        )
    except schedule_engine.ScheduleEngineError as exc:
        raise ScheduleValidationError(str(exc)) from exc

    items = items or []
    for item in items:
        if item["ratio_percent"] <= 0:
            raise ScheduleValidationError("ratio_percent는 0보다 커야 합니다")

    schedule_id = new_id()
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO investment_schedules "
        "(id, plan_id, schedule_type, title, market, recurrence_type, recurrence_interval, "
        " start_date, end_date, occurrence_count, holiday_policy, notify_days_before, total_amount, currency, "
        " deposit_account_id, account_name_snapshot, status, memo, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
        (
            schedule_id, plan_id, schedule_type, title.strip(), market, recurrence_type, recurrence_interval,
            start_date, end_date, occurrence_count, holiday_policy, notify_days_before, total_amount, currency,
            deposit_account_id, account_snapshot, memo, now, now,
        ),
    )

    item_snapshots: list[dict[str, Any]] = []
    for item in items:
        snapshot = _lookup_instrument_snapshot(conn, item["instrument_id"])
        conn.execute(
            "INSERT INTO investment_schedule_items "
            "(id, schedule_id, instrument_id, instrument_name_snapshot, ratio_percent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), schedule_id, item["instrument_id"], snapshot, item["ratio_percent"], now),
        )
        item_snapshots.append({"instrument_id": item["instrument_id"], "instrument_name_snapshot": snapshot, "ratio_percent": item["ratio_percent"]})

    occurrence_amounts: list[Optional[float]]
    if total_amount is not None:
        occurrence_amounts = schedule_engine.allocate_evenly(total_amount, len(dates), currency=currency)
    else:
        occurrence_amounts = [None] * len(dates)

    ratios = [it["ratio_percent"] for it in item_snapshots]
    for idx, d in enumerate(dates):
        adj = schedule_engine.adjust_for_holiday(d, market=market, holiday_policy=holiday_policy)
        occ_id = new_id()
        occ_amount = occurrence_amounts[idx]
        conn.execute(
            "INSERT INTO schedule_occurrences "
            "(id, schedule_id, occurrence_index, original_scheduled_date, adjusted_scheduled_date, "
            " needs_holiday_confirmation, planned_amount, currency, status, acknowledged, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED', 0, ?, ?)",
            (
                occ_id, schedule_id, idx + 1, adj.original.isoformat(), adj.adjusted.isoformat(),
                1 if adj.needs_confirmation else 0, occ_amount, currency, now, now,
            ),
        )
        if item_snapshots and occ_amount is not None:
            item_amounts = schedule_engine.allocate_by_ratio(occ_amount, ratios, currency=currency)
            for item, amt in zip(item_snapshots, item_amounts):
                conn.execute(
                    "INSERT INTO schedule_occurrence_items "
                    "(id, occurrence_id, instrument_id, instrument_name_snapshot, planned_amount, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id(), occ_id, item["instrument_id"], item["instrument_name_snapshot"], amt, now),
                )

    return get_schedule(conn, schedule_id)  # type: ignore[return-value]


def get_schedule(conn: sqlite3.Connection, schedule_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM investment_schedules WHERE id = ?", (schedule_id,)).fetchone()
    if row is None:
        return None
    sched = _schedule_row_to_dict(row)
    items = conn.execute(
        "SELECT * FROM investment_schedule_items WHERE schedule_id = ? ORDER BY created_at ASC", (schedule_id,)
    ).fetchall()
    sched["items"] = [_schedule_item_row_to_dict(r) for r in items]
    occs = conn.execute(
        "SELECT * FROM schedule_occurrences WHERE schedule_id = ? ORDER BY occurrence_index ASC", (schedule_id,)
    ).fetchall()
    sched["occurrences"] = [_occurrence_row_to_dict(r) for r in occs]
    return sched


def list_schedules(
    conn: sqlite3.Connection, *, plan_id: Optional[str] = None, status: Optional[str] = None,
    schedule_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if plan_id:
        clauses.append("plan_id = ?")
        params.append(plan_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if schedule_type:
        clauses.append("schedule_type = ?")
        params.append(schedule_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM investment_schedules {where} ORDER BY created_at DESC", params
    ).fetchall()
    return [_schedule_row_to_dict(r) for r in rows]


def update_schedule(
    conn: sqlite3.Connection, schedule_id: str, *, title: Optional[str] = None,
    holiday_policy: Optional[str] = None, status: Optional[str] = None, memo: Optional[str] = "__unset__",  # type: ignore[assignment]
) -> Optional[dict[str, Any]]:
    existing = get_schedule(conn, schedule_id)
    if existing is None:
        return None
    if status is not None and status not in SCHEDULE_STATUSES:
        raise ScheduleValidationError(f"지원하지 않는 status: {status}")
    if holiday_policy is not None and holiday_policy not in schedule_engine.HOLIDAY_POLICIES:
        raise ScheduleValidationError(f"지원하지 않는 holiday_policy: {holiday_policy}")

    fields: dict[str, Any] = {}
    if title is not None:
        if not title.strip():
            raise ScheduleValidationError("title은 비어 있을 수 없습니다")
        fields["title"] = title.strip()
    if holiday_policy is not None:
        fields["holiday_policy"] = holiday_policy
    if status is not None:
        fields["status"] = status
    if memo != "__unset__":
        fields["memo"] = memo

    if not fields:
        return existing
    fields["updated_at"] = utcnow_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE investment_schedules SET {set_clause} WHERE id = ?", (*fields.values(), schedule_id))

    # 스케줄 자체를 CANCELLED로 바꾸면, 아직 미확정인 미래 회차도 함께 취소 처리한다
    # (스펙 취지: 상위 일정을 취소했는데 개별 회차만 예정 상태로 남아 알림이 계속
    # 나가면 안 됨). 이미 완료/건너뜀/취소된 회차는 그대로 둔다.
    if status == "CANCELLED":
        conn.execute(
            "UPDATE schedule_occurrences SET status = 'CANCELLED', updated_at = ? "
            "WHERE schedule_id = ? AND status IN ('SCHEDULED', 'NOTIFIED')",
            (utcnow_iso(), schedule_id),
        )
    return get_schedule(conn, schedule_id)


def delete_schedule(conn: sqlite3.Connection, schedule_id: str) -> bool:
    cur = conn.execute("DELETE FROM investment_schedules WHERE id = ?", (schedule_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# schedule_occurrences (조회/상태변경/확인)
# ---------------------------------------------------------------------------

def get_occurrence(conn: sqlite3.Connection, occurrence_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT o.*, s.title AS schedule_title, s.schedule_type, s.market "
        "FROM schedule_occurrences o JOIN investment_schedules s ON s.id = o.schedule_id "
        "WHERE o.id = ?",
        (occurrence_id,),
    ).fetchone()
    if row is None:
        return None
    occ = _occurrence_row_to_dict(row)
    items = conn.execute(
        "SELECT * FROM schedule_occurrence_items WHERE occurrence_id = ? ORDER BY created_at ASC", (occurrence_id,)
    ).fetchall()
    occ["items"] = [
        {
            "id": r["id"], "instrument_id": r["instrument_id"],
            "instrument_name_snapshot": r["instrument_name_snapshot"], "planned_amount": r["planned_amount"],
        }
        for r in items
    ]
    executions = conn.execute(
        "SELECT * FROM schedule_executions WHERE occurrence_id = ? ORDER BY created_at ASC", (occurrence_id,)
    ).fetchall()
    occ["executions"] = [_execution_row_to_dict(r) for r in executions]
    return occ


def list_occurrences(
    conn: sqlite3.Connection, *, schedule_id: Optional[str] = None,
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if schedule_id:
        clauses.append("schedule_id = ?")
        params.append(schedule_id)
    if start_date:
        clauses.append("adjusted_scheduled_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("adjusted_scheduled_date <= ?")
        params.append(end_date)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(f"o.{c}" for c in clauses) if clauses else ""
    rows = conn.execute(
        "SELECT o.*, s.title AS schedule_title, s.schedule_type, s.market "
        f"FROM schedule_occurrences o JOIN investment_schedules s ON s.id = o.schedule_id {where} "
        "ORDER BY o.adjusted_scheduled_date ASC",
        params,
    ).fetchall()
    return [_occurrence_row_to_dict(r) for r in rows]


def update_occurrence(
    conn: sqlite3.Connection, occurrence_id: str, *, status: Optional[str] = None,
    acknowledged: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    existing = get_occurrence(conn, occurrence_id)
    if existing is None:
        return None
    if status is not None and status not in OCCURRENCE_STATUSES:
        raise ScheduleValidationError(f"지원하지 않는 status: {status}")

    fields: dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    if acknowledged is not None:
        fields["acknowledged"] = 1 if acknowledged else 0
    if not fields:
        return existing
    fields["updated_at"] = utcnow_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE schedule_occurrences SET {set_clause} WHERE id = ?", (*fields.values(), occurrence_id))
    return get_occurrence(conn, occurrence_id)


def find_occurrences_due_for_notification(conn: sqlite3.Connection, *, today: date) -> list[dict[str, Any]]:
    """오늘 텔레그램으로 알려야 할 회차 목록 -- adjusted_scheduled_date에서
    schedule.notify_days_before일만큼 앞당긴 날짜가 오늘이고, 아직 SCHEDULED
    상태인 것들. 실제 중복 방지는 outbox insert 시점의 UNIQUE 제약이 최종
    책임지므로(멱등성), 이 조회는 "오늘 대상 후보"만 골라내면 된다."""
    today_iso = today.isoformat()
    rows = conn.execute(
        """
        SELECT o.*, s.title AS schedule_title, s.schedule_type, s.notify_days_before, s.market
        FROM schedule_occurrences o
        JOIN investment_schedules s ON s.id = o.schedule_id
        WHERE o.status = 'SCHEDULED'
          AND date(o.adjusted_scheduled_date, '-' || s.notify_days_before || ' days') = ?
        """,
        (today_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def badge_count(conn: sqlite3.Connection, *, today: Optional[date] = None, cap: int = 9) -> dict[str, Any]:
    """상단 배지 숫자 -- SCHEDULED(오늘까지 도래한 것만)/NOTIFIED(미확인)/
    PARTIALLY_EXECUTED/OVERDUE만 센다. COMPLETED/SKIPPED/CANCELLED/ARCHIVED와
    아직 도래하지 않은 미래 SCHEDULED는 제외한다."""
    today = today or datetime.now().date()
    today_iso = today.isoformat()
    rows = conn.execute(
        "SELECT status, acknowledged, adjusted_scheduled_date FROM schedule_occurrences "
        "WHERE status NOT IN ('COMPLETED', 'SKIPPED', 'CANCELLED', 'ARCHIVED')"
    ).fetchall()
    count = 0
    for r in rows:
        status = r["status"]
        if status == "PARTIALLY_EXECUTED":
            count += 1
        elif status == "NOTIFIED" and not r["acknowledged"]:
            count += 1
        elif status == "SCHEDULED" and r["adjusted_scheduled_date"] <= today_iso:
            count += 1
    return {"count": count, "display": f"{cap}+" if count > cap else str(count)}


# ---------------------------------------------------------------------------
# schedule_executions
# ---------------------------------------------------------------------------

def _execution_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "occurrence_id": row["occurrence_id"], "execution_type": row["execution_type"],
        "linked_withdrawal_id": row["linked_withdrawal_id"], "linked_trade_id": row["linked_trade_id"],
        "executed_amount": row["executed_amount"], "executed_at": row["executed_at"],
        "memo": row["memo"], "created_at": row["created_at"],
    }


def create_execution(
    conn: sqlite3.Connection, *, occurrence_id: str, execution_type: str,
    linked_withdrawal_id: Optional[str] = None, linked_trade_id: Optional[str] = None,
    executed_amount: Optional[float] = None, executed_at: Optional[str] = None, memo: Optional[str] = None,
) -> dict[str, Any]:
    """실제 실행 사실만 기록한다 -- cash_withdrawals/trades를 만들지 않는다. 이미 그
    쪽에서 발생시킨 기록을 link로 연결하거나, link 없이 "그냥 완료 처리"만 남긴다."""
    occ = get_occurrence(conn, occurrence_id)
    if occ is None:
        raise ScheduleValidationError(f"존재하지 않는 occurrence_id: {occurrence_id}")
    if linked_withdrawal_id:
        row = conn.execute("SELECT id FROM cash_withdrawals WHERE id = ?", (linked_withdrawal_id,)).fetchone()
        if row is None:
            raise ScheduleValidationError(f"존재하지 않는 linked_withdrawal_id: {linked_withdrawal_id}")
    if linked_trade_id:
        row = conn.execute("SELECT id FROM trades WHERE id = ?", (linked_trade_id,)).fetchone()
        if row is None:
            raise ScheduleValidationError(f"존재하지 않는 linked_trade_id: {linked_trade_id}")

    exec_id = new_id()
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO schedule_executions "
        "(id, occurrence_id, execution_type, linked_withdrawal_id, linked_trade_id, executed_amount, executed_at, memo, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (exec_id, occurrence_id, execution_type, linked_withdrawal_id, linked_trade_id, executed_amount, executed_at or now, memo, now),
    )
    return get_occurrence(conn, occurrence_id)  # type: ignore[return-value]
