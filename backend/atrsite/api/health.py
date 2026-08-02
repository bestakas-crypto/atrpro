"""헬스체크 API (스펙 7 api/health.py) -- 인증 없이 접근 가능해야 하므로 별도 라우터."""
from __future__ import annotations

from fastapi import APIRouter

from .. import __version__

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}
