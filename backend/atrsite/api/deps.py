"""FastAPI 의존성 -- DB 커넥션 + 단일 사용자 인증 (스펙 14.1)."""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Header, HTTPException, status

from ..config import settings
from ..db import connect


def get_conn() -> Iterator[sqlite3.Connection]:
    """요청 1건 = 트랜잭션 1개. 정상 종료 시 commit, 예외 시 rollback."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """settings.api_key가 비어 있으면(로컬 개발 기본값) 인증을 강제하지 않는다.
    값이 설정된 배포 환경에서는 헤더가 정확히 일치해야 한다."""
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing API key")
