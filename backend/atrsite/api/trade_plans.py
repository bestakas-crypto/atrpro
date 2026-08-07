"""매매계획(트리거 감시) API -- Phase 1(TRAIL만). deposits.py와 동일한
얇은 라우터 패턴(검증은 pydantic + repository의 ValueError, 응답은 리포지토리
dict 그대로)."""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..repositories import trade_plans as plans_repo
from .deps import get_conn, require_api_key
from .schemas import TradePlanCreate, TradePlanUpdate

router = APIRouter(prefix="/api/v1/trade-plans", tags=["trade-plans"], dependencies=[Depends(require_api_key)])


@router.post("", status_code=201)
def create_trade_plan(body: TradePlanCreate, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return plans_repo.create_plan(
            conn,
            plan_type=body.plan_type,
            label=body.label,
            trigger_price=body.trigger_price,
            trigger_direction=body.trigger_direction,
            confirm_mode=body.confirm_mode,
            price_reference_instrument_id=body.price_reference_instrument_id,
            instruments=[i.model_dump() for i in body.instruments],
            tiers=[t.model_dump() for t in body.tiers],
            purpose=body.purpose,
            invalidation_condition=body.invalidation_condition,
            review_date=body.review_date,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_trade_plans(
    instrument_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    statuses = [status] if status else None
    return plans_repo.list_plans(conn, instrument_id=instrument_id, statuses=statuses)


@router.get("/{plan_id}")
def get_trade_plan(plan_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    plan = plans_repo.get_plan(conn, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="해당 매매계획을 찾을 수 없습니다.")
    return plan


@router.patch("/{plan_id}")
def update_trade_plan(plan_id: str, body: TradePlanUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    updated = plans_repo.update_plan_fields(
        conn,
        plan_id,
        change_reason=body.change_reason,
        label=body.label,
        trigger_price=body.trigger_price,
        confirm_mode=body.confirm_mode,
        purpose=body.purpose,
        invalidation_condition=body.invalidation_condition,
        review_date=body.review_date,
        reason=body.reason,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="해당 매매계획을 찾을 수 없습니다.")
    return updated


@router.delete("/{plan_id}")
def cancel_trade_plan(
    plan_id: str, reason: str = Query(min_length=1), conn: sqlite3.Connection = Depends(get_conn),
):
    """실제 DELETE가 아니라 CANCELLED 전환이다 -- 이미 발동한 tier 기록은
    그대로 보존된다. reason은 요청 바디가 아니라 쿼리 파라미터로 받는다 --
    DELETE 요청 바디는 일부 HTTP 클라이언트/프록시가 지원하지 않는다(이
    프로젝트의 테스트 클라이언트도 그중 하나였다)."""
    cancelled = plans_repo.cancel_plan(conn, plan_id, reason=reason)
    if cancelled is None:
        raise HTTPException(status_code=404, detail="해당 매매계획을 찾을 수 없습니다.")
    return cancelled


@router.get("/{plan_id}/history")
def get_trade_plan_history(plan_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    plan = plans_repo.get_plan(conn, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="해당 매매계획을 찾을 수 없습니다.")
    return plans_repo.get_history(conn, plan_id)
