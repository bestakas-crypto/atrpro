"""FastAPI 진입점 (스펙 7 backend/atrsite/main.py).

웹 프로세스 전용 -- 시세 수집/신호 판정 폴링은 worker.py(3단계)가 별도
프로세스로 담당한다(스펙 5.2). 이 프로세스는 API 라우터 + 정적 프런트엔드
서빙만 한다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.analysis import router as analysis_router
from .api.company import router as company_router
from .api.dashboard import router as dashboard_router
from .api.deposits import router as deposits_router
from .api.health import router as health_router
from .api.instruments import router as instruments_router
from .api.legacy import router as legacy_router
from .api.trades import router as trades_router
from .config import settings
from .db import connect, init_db


@asynccontextmanager
async def _lifespan(app: FastAPI):
    conn = connect()
    try:
        init_db(conn)
    finally:
        conn.close()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=__version__, lifespan=_lifespan)

    app.include_router(health_router)
    app.include_router(dashboard_router)
    app.include_router(instruments_router)
    app.include_router(trades_router)
    app.include_router(deposits_router)
    app.include_router(legacy_router)
    app.include_router(analysis_router)
    app.include_router(company_router)

    frontend_dir = settings.frontend_dir
    if frontend_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_dir), name="assets")

        @app.get("/{full_path:path}")
        def serve_frontend(full_path: str):
            """스펙 4.8 -- /api/는 이 핸들러에 도달하지 않는다(라우터가 먼저 매칭됨).
            그 외 경로는 전부 index.html로 보내 프런트엔드의 해시 기반 라우팅
            (#list, #detail/<id>)이 처리하게 한다."""
            candidate = frontend_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend_dir / "index.html")

    return app


app = create_app()
