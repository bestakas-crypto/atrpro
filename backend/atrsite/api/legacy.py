"""기존 데이터 가져오기 API (스펙 7 -- scripts/import_legacy.py와 로직을 공유)."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from ..services import legacy_import
from .deps import get_conn, require_api_key

router = APIRouter(prefix="/api/v1/legacy", tags=["legacy"], dependencies=[Depends(require_api_key)])


@router.post("/import")
def import_legacy(payload: dict[str, Any], dry_run: bool = True, conn: sqlite3.Connection = Depends(get_conn)):
    if dry_run:
        report = legacy_import.validate(payload)
    else:
        report = legacy_import.apply(conn, payload)
    return report.to_dict()
