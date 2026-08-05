"""backend/atrsite/api/schedules.py -- 투자 스케줄(v1.2) API.

URL 프리픽스는 기존 관례(/api/v1/<resource>) 그대로 따른다. 부분수정은 PATCH.
정적 경로(/today, /badge-count)는 withdrawals.py와 동일한 이유로 /{id}보다 먼저
등록한다(안 그러면 "today"/"badge-count" 문자열이 {occurrence_id}로 먹혀버림).
"""
from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from ..repositories import schedules as schedules_repo
from ..repositories.schedules import ScheduleValidationError
from .deps import get_conn, require_api_key
from .schemas import (
    InvestmentPlanCreate, InvestmentPlanUpdate, InvestmentScheduleCreate, InvestmentScheduleUpdate,
    ScheduleExecutionCreate, ScheduleOccurrenceUpdate,
)

plans_router = APIRouter(prefix="/api/v1/investment-plans", tags=["investment-plans"], dependencies=[Depends(require_api_key)])
schedules_router = APIRouter(prefix="/api/v1/investment-schedules", tags=["investment-schedules"], dependencies=[Depends(require_api_key)])
occurrences_router = APIRouter(prefix="/api/v1/schedule-occurrences", tags=["schedule-occurrences"], dependencies=[Depends(require_api_key)])


# ---------------------------------------------------------------------------
# investment-plans
# ---------------------------------------------------------------------------

@plans_router.get("")
def list_plans(status: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": schedules_repo.list_plans(conn, status=status)}


@plans_router.post("", status_code=201)
def create_plan(body: InvestmentPlanCreate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return schedules_repo.create_plan(
            conn, name=body.name, deposit_account_id=body.deposit_account_id,
            total_amount=body.total_amount, currency=body.currency, memo=body.memo,
            items=[item.model_dump() for item in body.items],
        )
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@plans_router.get("/{plan_id}")
def get_plan(plan_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    result = schedules_repo.get_plan(conn, plan_id)
    if result is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return result


@plans_router.patch("/{plan_id}")
def update_plan(plan_id: str, body: InvestmentPlanUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    kwargs = body.model_dump(exclude_unset=True)
    try:
        updated = schedules_repo.update_plan(conn, plan_id, **kwargs)
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return updated


@plans_router.delete("/{plan_id}", status_code=204)
def delete_plan(plan_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not schedules_repo.delete_plan(conn, plan_id):
        raise HTTPException(status_code=404, detail="plan not found")
    return None


# ---------------------------------------------------------------------------
# investment-schedules
# ---------------------------------------------------------------------------

@schedules_router.get("")
def list_schedules(
    plan_id: str | None = None, status: str | None = None, schedule_type: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    return {"items": schedules_repo.list_schedules(conn, plan_id=plan_id, status=status, schedule_type=schedule_type)}


@schedules_router.post("", status_code=201)
def create_schedule(body: InvestmentScheduleCreate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return schedules_repo.create_schedule(
            conn, plan_id=body.plan_id, schedule_type=body.schedule_type, title=body.title,
            market=body.market, recurrence_type=body.recurrence_type, recurrence_interval=body.recurrence_interval,
            start_date=body.start_date, end_date=body.end_date, occurrence_count=body.occurrence_count,
            custom_dates=body.custom_dates, holiday_policy=body.holiday_policy,
            notify_days_before=body.notify_days_before, total_amount=body.total_amount,
            currency=body.currency, deposit_account_id=body.deposit_account_id, memo=body.memo,
            items=[item.model_dump() for item in body.items],
        )
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@schedules_router.get("/{schedule_id}")
def get_schedule(schedule_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    result = schedules_repo.get_schedule(conn, schedule_id)
    if result is None:
        raise HTTPException(status_code=404, detail="schedule not found")
    return result


@schedules_router.patch("/{schedule_id}")
def update_schedule(schedule_id: str, body: InvestmentScheduleUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    kwargs = body.model_dump(exclude_unset=True)
    try:
        updated = schedules_repo.update_schedule(conn, schedule_id, **kwargs)
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="schedule not found")
    return updated


@schedules_router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not schedules_repo.delete_schedule(conn, schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found")
    return None


# ---------------------------------------------------------------------------
# schedule-occurrences (배지/오늘 위젯 -- 정적 경로가 /{occurrence_id}보다 먼저)
# ---------------------------------------------------------------------------

@occurrences_router.get("/today")
def today_occurrences(conn: sqlite3.Connection = Depends(get_conn)):
    today_iso = date.today().isoformat()
    items = schedules_repo.list_occurrences(conn, start_date=today_iso, end_date=today_iso)
    # 이미 지난(overdue) 미확정 일정도 "오늘 확인할 것"에 함께 보여준다.
    overdue = [
        o for o in schedules_repo.list_occurrences(conn, end_date=today_iso)
        if o["is_overdue"]
    ]
    return {"today": items, "overdue": overdue}


@occurrences_router.get("/badge-count")
def get_badge_count(conn: sqlite3.Connection = Depends(get_conn)):
    return schedules_repo.badge_count(conn)


@occurrences_router.get("")
def list_occurrences(
    schedule_id: str | None = None, start_date: str | None = None, end_date: str | None = None,
    status: str | None = None, conn: sqlite3.Connection = Depends(get_conn),
):
    return {"items": schedules_repo.list_occurrences(conn, schedule_id=schedule_id, start_date=start_date, end_date=end_date, status=status)}


@occurrences_router.get("/{occurrence_id}")
def get_occurrence(occurrence_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    result = schedules_repo.get_occurrence(conn, occurrence_id)
    if result is None:
        raise HTTPException(status_code=404, detail="occurrence not found")
    return result


@occurrences_router.patch("/{occurrence_id}")
def update_occurrence(occurrence_id: str, body: ScheduleOccurrenceUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    kwargs = body.model_dump(exclude_unset=True)
    try:
        updated = schedules_repo.update_occurrence(conn, occurrence_id, **kwargs)
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="occurrence not found")
    return updated


@occurrences_router.post("/{occurrence_id}/executions", status_code=201)
def create_execution(occurrence_id: str, body: ScheduleExecutionCreate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return schedules_repo.create_execution(
            conn, occurrence_id=occurrence_id, execution_type=body.execution_type,
            linked_withdrawal_id=body.linked_withdrawal_id, linked_trade_id=body.linked_trade_id,
            executed_amount=body.executed_amount, executed_at=body.executed_at, memo=body.memo,
        )
    except ScheduleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
