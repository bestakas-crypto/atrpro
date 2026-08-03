"""LLM 매크로 브리핑 API -- 2026-08-03 추가.

한 번 터치로 오늘의 매크로 브리핑을 만든다. LLM 호출(웹서치 포함)이라
몇 초~몇십 초 걸릴 수 있어서, 별도 상시 프로세스(atrsite-analysis) 없이
FastAPI 요청을 그냥 동기로 기다리게 한다 -- 개인용 단일 사용자 앱이라
요청이 자주 겹칠 일이 없어서 이 정도로 충분하다(필요해지면 나중에
백그라운드 작업으로 바꿀 수 있음).
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..adapters.llm_client import LLMError
from ..repositories import analysis as analysis_repo
from ..services import analysis_service
from .deps import get_conn, require_api_key

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"], dependencies=[Depends(require_api_key)])


@router.post("/run")
def run_analysis(force: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        result = analysis_service.run_briefing(conn, force=force)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM 브리핑 생성 실패: {exc}") from exc
    conn.commit()
    return result


@router.get("/latest")
def get_latest(conn: sqlite3.Connection = Depends(get_conn)):
    result = analysis_repo.get_latest_result(conn)
    if result is None:
        raise HTTPException(status_code=404, detail="아직 생성된 브리핑이 없습니다")
    return result
