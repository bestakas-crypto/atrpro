# backend/atrsite/api/company.py
# 종목탐구 API -- 스펙 12절. 분석(POST /company-analysis/run)은 SEC 웹서치
# 등 실제 외부 호출이 여러 번 겹쳐 몇십 초 걸릴 수 있어서, macro 브리핑
# (동기 처리)과 달리 요청ID를 먼저 반환하고 BackgroundTasks로 백그라운드
# 실행 -- 프런트가 GET .../{id}로 상태를 폴링한다.
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..db import connect
from ..repositories import companies as companies_repo
from ..repositories import company_analysis as company_analysis_repo
from ..services import company_analysis_service
from .deps import get_conn, require_api_key
from .schemas import CompanyAnalysisRun, CompanyResolveKR, CompanyResolveUS

router = APIRouter(prefix="/api/v1", tags=["company"], dependencies=[Depends(require_api_key)])


@router.get("/company/search")
def search_company(q: str, conn: sqlite3.Connection = Depends(get_conn)):
    if not q or not q.strip():
        return {"results": [], "notices": []}
    return company_analysis_service.search_companies(q.strip())


@router.post("/company/resolve")
def resolve_company(body: CompanyResolveUS, conn: sqlite3.Connection = Depends(get_conn)):
    """스펙 4.2 "이 종목 분석" 확인 -- 아직 분석을 시작하지 않고 회사
    레코드만 확정한다(동명이인/ETF오인 방지를 위해 분석 시작 전에 항상
    이 단계를 거치게 함)."""
    company = company_analysis_service.resolve_company_from_us_match(
        conn, cik=body.cik, ticker=body.ticker, title=body.name,
    )
    conn.commit()
    return company


@router.post("/company/resolve-kr")
def resolve_company_kr(body: CompanyResolveKR, conn: sqlite3.Connection = Depends(get_conn)):
    company = company_analysis_service.resolve_company_from_kr_match(
        conn, corp_code=body.corp_code, corp_name=body.corp_name, stock_code=body.stock_code,
    )
    conn.commit()
    return company


def _run_pipeline_in_background(request_id: str, company_id: str) -> None:
    """BackgroundTasks에서 실행 -- 요청-스코프 커넥션(get_conn)을 공유하지
    않고 별도 커넥션을 새로 연다(worker.py와 동일한 원칙: 프로세스/태스크
    경계를 넘어 sqlite3.Connection을 공유하지 않음)."""
    conn = connect()
    try:
        company_analysis_service.execute_pipeline(conn, request_id=request_id, company_id=company_id)
    finally:
        conn.close()


@router.post("/company-analysis/run", status_code=202)
def run_company_analysis(
    body: CompanyAnalysisRun, background_tasks: BackgroundTasks, conn: sqlite3.Connection = Depends(get_conn),
):
    company = companies_repo.get_company(conn, body.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다. 먼저 /company/resolve로 확인하세요.")
    request = company_analysis_repo.create_request(conn, company_id=body.company_id)
    conn.commit()
    background_tasks.add_task(_run_pipeline_in_background, request["id"], body.company_id)
    return request


@router.get("/company-analysis/recent")
def list_recent(conn: sqlite3.Connection = Depends(get_conn)):
    return company_analysis_repo.list_recent_companies(conn)


@router.get("/company-analysis/{request_id}")
def get_company_analysis(request_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    request = company_analysis_repo.get_request(conn, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="분석 요청을 찾을 수 없습니다.")
    result = None
    if request["status"] == "COMPLETED":
        result = company_analysis_repo.get_result_by_request(conn, request_id)
    return {**request, "result": result}
