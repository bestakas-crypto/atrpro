# backend/atrsite/repositories/companies.py
# companies/company_identifiers/company_filings/company_financial_periods/
# company_financial_metrics 리포지토리 -- 종목탐구 기능의 데이터 계층.
from __future__ import annotations

import sqlite3
from typing import Any

from ..utils import new_id, utcnow_iso


def _company_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "country": row["country"],
        "security_type": row["security_type"],
        "name": row["name"],
        "name_en": row["name_en"],
        "currency": row["currency"],
        "exchange": row["exchange"],
        "primary_ticker": row["primary_ticker"],
        "sec_cik": row["sec_cik"],
        "dart_corp_code": row["dart_corp_code"],
        "industry_template": row["industry_template"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_company(conn: sqlite3.Connection, company_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return _company_row_to_dict(row) if row else None


def find_by_identifier(conn: sqlite3.Connection, id_type: str, id_value: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT c.* FROM companies c
        JOIN company_identifiers ci ON ci.company_id = c.id
        WHERE ci.id_type = ? AND ci.id_value = ?
        """,
        (id_type, id_value),
    ).fetchone()
    return _company_row_to_dict(row) if row else None


def add_identifier(conn: sqlite3.Connection, company_id: str, id_type: str, id_value: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO company_identifiers (company_id, id_type, id_value) VALUES (?, ?, ?)",
        (company_id, id_type, id_value),
    )


def create_company(
    conn: sqlite3.Connection,
    *,
    country: str,
    security_type: str,
    name: str,
    currency: str,
    primary_ticker: str,
    name_en: str | None = None,
    exchange: str | None = None,
    sec_cik: str | None = None,
    dart_corp_code: str | None = None,
    industry_template: str | None = None,
) -> dict[str, Any]:
    """이미 알려진 식별자(CIK/KRX 코드)로 기존 회사가 있으면 그걸 재사용하고,
    없으면 새로 만든다 -- 같은 기업을 여러 번 검색해도 companies 테이블에
    중복이 쌓이지 않게 한다."""
    existing = None
    if sec_cik:
        existing = find_by_identifier(conn, "CIK", sec_cik)
    elif dart_corp_code:
        row = conn.execute(
            "SELECT * FROM companies WHERE dart_corp_code = ?", (dart_corp_code,)
        ).fetchone()
        existing = _company_row_to_dict(row) if row else None
    if existing:
        return existing

    company_id = new_id()
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO companies
            (id, country, security_type, name, name_en, currency, exchange,
             primary_ticker, sec_cik, dart_corp_code, industry_template, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (company_id, country, security_type, name, name_en, currency, exchange,
         primary_ticker, sec_cik, dart_corp_code, industry_template, now, now),
    )
    add_identifier(conn, company_id, "TICKER", primary_ticker.upper())
    if sec_cik:
        add_identifier(conn, company_id, "CIK", sec_cik)
    if dart_corp_code:
        add_identifier(conn, company_id, "KRX_CODE", dart_corp_code)
    return get_company(conn, company_id)  # type: ignore[return-value]


def record_filing(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    source: str,
    filing_type: str,
    filed_at: str | None,
    period_end: str | None,
    accession_or_receipt_no: str | None,
    url: str | None,
) -> None:
    """SEC/DART 원문 출처 기록 -- 스펙 9.2 "출처 없는 수치 금지"의 근거 데이터.
    같은 accession/receipt 번호가 이미 있으면 중복 삽입하지 않는다."""
    conn.execute(
        """
        INSERT OR IGNORE INTO company_filings
            (id, company_id, source, filing_type, filed_at, period_end,
             accession_or_receipt_no, url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id(), company_id, source, filing_type, filed_at, period_end,
         accession_or_receipt_no, url, utcnow_iso()),
    )


def upsert_financial_period(
    conn: sqlite3.Connection,
    *,
    company_id: str,
    fiscal_year: int,
    fiscal_period: str,
    period_type: str,
    period_end: str,
    filed_at: str | None,
    source: str,
) -> int:
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO company_financial_periods
            (company_id, fiscal_year, fiscal_period, period_type, period_end, filed_at, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, period_end, period_type) DO UPDATE SET
            fiscal_year   = excluded.fiscal_year,
            fiscal_period = excluded.fiscal_period,
            filed_at      = excluded.filed_at,
            source        = excluded.source,
            fetched_at    = excluded.fetched_at
        """,
        (company_id, fiscal_year, fiscal_period, period_type, period_end, filed_at, source, now),
    )
    row = conn.execute(
        "SELECT id FROM company_financial_periods WHERE company_id = ? AND period_end = ? AND period_type = ?",
        (company_id, period_end, period_type),
    ).fetchone()
    return row["id"]


def upsert_financial_metric(
    conn: sqlite3.Connection,
    *,
    period_id: int,
    metric_key: str,
    value: float | None,
    unit: str | None = None,
    verdict: str | None = None,
    basis_note: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO company_financial_metrics (period_id, metric_key, value, unit, verdict, basis_note)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(period_id, metric_key) DO UPDATE SET
            value = excluded.value, unit = excluded.unit,
            verdict = excluded.verdict, basis_note = excluded.basis_note
        """,
        (period_id, metric_key, value, unit, verdict, basis_note),
    )


def get_financial_periods(
    conn: sqlite3.Connection, company_id: str, period_type: str, limit: int = 8,
) -> list[dict[str, Any]]:
    """최근 N개 기간을 과거->최신(오름차순)으로 반환하고, 각 기간에 딸린
    metric_key->값 딕셔너리를 같이 채워 넣는다(스펙 6.2 "최근 8개 분기 정렬")."""
    rows = conn.execute(
        """
        SELECT * FROM company_financial_periods
        WHERE company_id = ? AND period_type = ?
        ORDER BY period_end DESC LIMIT ?
        """,
        (company_id, period_type, limit),
    ).fetchall()
    periods = []
    for row in reversed(rows):  # 과거->최신
        metrics_rows = conn.execute(
            "SELECT metric_key, value, unit, verdict, basis_note FROM company_financial_metrics WHERE period_id = ?",
            (row["id"],),
        ).fetchall()
        metrics = {
            m["metric_key"]: {
                "value": m["value"], "unit": m["unit"],
                "verdict": m["verdict"], "basis_note": m["basis_note"],
            }
            for m in metrics_rows
        }
        periods.append({
            "period_id": row["id"],
            "fiscal_year": row["fiscal_year"],
            "fiscal_period": row["fiscal_period"],
            "period_end": row["period_end"],
            "filed_at": row["filed_at"],
            "source": row["source"],
            "metrics": metrics,
        })
    return periods


def get_filings(conn: sqlite3.Connection, company_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM company_filings WHERE company_id = ? ORDER BY filed_at DESC LIMIT ?",
        (company_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
