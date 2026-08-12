"""backend/atrsite/api/benchmark.py -- 벤치마크 지수(코스피/S&P500) API,
analyze.kunoh.top 4단계(2026-08-12 추가). 읽기 전용, DB 저장 없음.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..adapters.kis_client import KisApiError
from ..services import benchmark_service
from .deps import require_api_key

router = APIRouter(prefix="/api/v1/benchmark", tags=["benchmark"], dependencies=[Depends(require_api_key)])


@router.get("/kospi")
def get_kospi(
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    try:
        return {"items": benchmark_service.get_kospi_daily(start_date, end_date)}
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=f"코스피 지수 조회 실패: {exc}") from exc


@router.get("/sp500")
def get_sp500(
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    try:
        return {"items": benchmark_service.get_sp500_daily(start_date, end_date)}
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=f"S&P500 지수 조회 실패: {exc}") from exc
