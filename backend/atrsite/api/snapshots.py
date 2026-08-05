"""backend/atrsite/api/snapshots.py -- v1.3 일별 총자산/손익 스냅샷 API.

URL 프리픽스는 기존 관례(/api/v1/<resource>) 그대로. 정적 경로(/latest, /chart,
/run)는 withdrawals.py/schedules.py와 동일한 이유로 /{snapshot_date}보다 먼저
등록한다(안 그러면 "latest" 같은 문자열이 {snapshot_date}로 먹혀버림).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from ..repositories import snapshots as snapshots_repo
from ..services import snapshot_service
from .deps import get_conn, require_api_key

router = APIRouter(prefix="/api/v1/portfolio-snapshots", tags=["portfolio-snapshots"], dependencies=[Depends(require_api_key)])

# 스펙 10절 -- 1개월/3개월/6개월/1년/처음부터/사용자지정 기간.
_PERIOD_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}

_CHART_FIELDS = (
    "snapshot_date", "total_asset_value", "investment_market_value", "cash_deposit_value",
    "unrealized_profit", "realized_profit", "total_profit", "profit_rate", "data_quality_status",
    # 스펙 10절이 요구하는 최소 목록을 넘어서 -- 스펙 12.4 툴팁이 "적용 환율"까지
    # 요구해서 여기 추가해뒀다(그래야 터치할 때마다 상세 API를 추가 조회 안 해도 됨).
    "usd_krw_rate", "jpy_krw_rate", "quality_notes",
)


@router.get("/latest")
def get_latest(conn: sqlite3.Connection = Depends(get_conn)):
    snap = snapshots_repo.get_latest_snapshot(conn)
    if snap is None:
        raise HTTPException(status_code=404, detail="아직 생성된 스냅샷이 없습니다")
    return snap


@router.get("/chart")
def get_chart(
    period: str = "3m", start_date: str | None = None, end_date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if start_date or end_date:
        rows = snapshots_repo.list_snapshots(conn, start_date=start_date, end_date=end_date)
    elif period == "all":
        rows = snapshots_repo.list_snapshots(conn)
    else:
        days = _PERIOD_DAYS.get(period)
        if days is None:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 period: {period}")
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = snapshots_repo.list_snapshots(conn, start_date=cutoff)
    return {"items": [{k: r[k] for k in _CHART_FIELDS} for r in rows]}


@router.post("/run", status_code=201)
def run_now(conn: sqlite3.Connection = Depends(get_conn)):
    """관리자가 "오늘 스냅샷 다시 계산"을 수동으로 실행(스펙 9절 "수동 실행").
    이미 COMPLETE류로 완료된 스냅샷이 있어도 명시적 요청이니 강제로 다시 계산한다."""
    data = snapshot_service.build_snapshot(conn, snapshot_date=date.today(), triggered_by="manual")
    return snapshots_repo.save_snapshot(conn, data, force=True)


@router.get("")
def list_snapshots(
    start_date: str | None = None, end_date: str | None = None, conn: sqlite3.Connection = Depends(get_conn),
):
    return {"items": snapshots_repo.list_snapshots(conn, start_date=start_date, end_date=end_date)}


@router.get("/{snapshot_date}")
def get_one(snapshot_date: str, conn: sqlite3.Connection = Depends(get_conn)):
    snap = snapshots_repo.get_snapshot(conn, snapshot_date)
    if snap is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return snap


@router.get("/{snapshot_date}/items")
def get_items(snapshot_date: str, conn: sqlite3.Connection = Depends(get_conn)):
    snap = snapshots_repo.get_snapshot(conn, snapshot_date)
    if snap is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return {"items": snapshots_repo.list_snapshot_items(conn, snap["id"])}


@router.post("/{snapshot_date}/recalculate")
def recalculate(snapshot_date: str, conn: sqlite3.Connection = Depends(get_conn)):
    """스펙 17절 -- "과거 스냅샷은 당시 데이터를 정확히 복원할 수 없으면 재계산을
    막는다." 이 프로젝트는 과거 시세/환율을 별도로 저장해두지 않으므로(현재값만
    유지), 오늘 날짜 스냅샷만 재계산을 허용한다."""
    try:
        d = date.fromisoformat(snapshot_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다(YYYY-MM-DD)") from exc
    if d != date.today():
        raise HTTPException(
            status_code=400,
            detail="오늘 날짜 스냅샷만 재계산할 수 있습니다 -- 과거 시세·환율을 정확히 복원할 수 없습니다",
        )
    data = snapshot_service.build_snapshot(conn, snapshot_date=d, triggered_by="manual")
    return snapshots_repo.save_snapshot(conn, data, force=True)
