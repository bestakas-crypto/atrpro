# backend/atrsite/repositories/company_analysis.py
# company_analysis_requests/company_analysis_results 리포지토리 -- 스펙 12절
# "요청ID 먼저 반환 -> 프런트가 상태 폴링" 패턴의 데이터 계층.
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import new_id, utcnow_iso

VALID_STATUSES = (
    "PENDING", "COLLECTING_DATA", "COMPUTING_METRICS", "SEARCHING_NEWS",
    "ANALYZING", "COMPLETED", "FAILED",
)


def create_request(conn: sqlite3.Connection, *, company_id: str) -> dict[str, Any]:
    request_id = new_id()
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO company_analysis_requests (id, company_id, status, created_at, updated_at)
        VALUES (?, ?, 'PENDING', ?, ?)
        """,
        (request_id, company_id, now, now),
    )
    return get_request(conn, request_id)  # type: ignore[return-value]


def update_request_status(
    conn: sqlite3.Connection, request_id: str, status: str, *, error_message: str | None = None,
) -> None:
    assert status in VALID_STATUSES, f"알 수 없는 상태값: {status}"
    conn.execute(
        "UPDATE company_analysis_requests SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
        (status, error_message, utcnow_iso(), request_id),
    )


def get_request(conn: sqlite3.Connection, request_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM company_analysis_requests WHERE id = ?", (request_id,)).fetchone()
    return dict(row) if row else None


def save_result(
    conn: sqlite3.Connection, *,
    request_id: str, company_id: str, snapshot_json: str, result_text: str, provider: str, model: str,
) -> dict[str, Any]:
    result_id = new_id()
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO company_analysis_results
            (id, request_id, company_id, created_at, snapshot_json, result_text, provider, model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (result_id, request_id, company_id, now, snapshot_json, result_text, provider, model),
    )
    return get_result_by_request(conn, request_id)  # type: ignore[return-value]


def get_result_by_request(conn: sqlite3.Connection, request_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM company_analysis_results WHERE request_id = ?", (request_id,)
    ).fetchone()
    return dict(row) if row else None


def get_latest_result_for_company(conn: sqlite3.Connection, company_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM company_analysis_results WHERE company_id = ? ORDER BY created_at DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return dict(row) if row else None


def list_recent_companies(conn: sqlite3.Connection, limit: int = 5) -> list[dict[str, Any]]:
    """최근 분석한 종목 목록(스펙 4.1 "최근 분석") -- 완료된 요청 기준으로
    회사별 가장 최근 시각만 남기고 최신순 정렬."""
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.primary_ticker, c.country, MAX(r.created_at) AS last_analyzed_at
        FROM company_analysis_requests r
        JOIN companies c ON c.id = r.company_id
        WHERE r.status = 'COMPLETED'
        GROUP BY c.id
        ORDER BY last_analyzed_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
