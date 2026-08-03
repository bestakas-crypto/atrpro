# tests/test_companies_repo.py
# repositories/companies.py, repositories/company_analysis.py 테스트.
from atrsite.repositories import companies as companies_repo
from atrsite.repositories import company_analysis as company_analysis_repo


def test_create_company_and_find_by_identifier(db_conn):
    company = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Micron Technology",
        currency="USD", primary_ticker="MU", exchange="NASDAQ", sec_cik="0000723125",
    )
    assert company["primary_ticker"] == "MU"

    found = companies_repo.find_by_identifier(db_conn, "TICKER", "MU")
    assert found["id"] == company["id"]
    found_by_cik = companies_repo.find_by_identifier(db_conn, "CIK", "0000723125")
    assert found_by_cik["id"] == company["id"]


def test_create_company_reuses_existing_by_cik(db_conn):
    """같은 CIK로 두 번 만들면 새 회사가 아니라 기존 회사를 반환해야 한다."""
    first = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Microsoft",
        currency="USD", primary_ticker="MSFT", sec_cik="0000789019",
    )
    second = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Microsoft Corp",
        currency="USD", primary_ticker="MSFT", sec_cik="0000789019",
    )
    assert first["id"] == second["id"]


def test_financial_period_and_metric_roundtrip(db_conn):
    company = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Micron",
        currency="USD", primary_ticker="MU", sec_cik="0000723125",
    )
    period_id = companies_repo.upsert_financial_period(
        db_conn, company_id=company["id"], fiscal_year=2026, fiscal_period="Q2",
        period_type="QUARTER", period_end="2026-02-28", filed_at="2026-03-20", source="SEC",
    )
    companies_repo.upsert_financial_metric(
        db_conn, period_id=period_id, metric_key="revenue", value=8100000000.0,
        unit="USD", verdict="IMPROVING", basis_note="전년 동기 대비 +30%",
    )

    periods = companies_repo.get_financial_periods(db_conn, company["id"], "QUARTER")
    assert len(periods) == 1
    assert periods[0]["metrics"]["revenue"]["value"] == 8100000000.0
    assert periods[0]["metrics"]["revenue"]["verdict"] == "IMPROVING"


def test_upsert_financial_period_is_idempotent_by_period_end(db_conn):
    """같은 (company, period_end, type)로 다시 넣으면 새 행이 아니라 갱신돼야
    한다 -- fiscal_year/fiscal_period가 아니라 period_end가 유일성 기준
    (2026-08-03: SEC 원본 fy/fp 태그가 비교 컬럼에서 실제 기간과 다르게
    붙는 걸 라이브 검증 중 발견해서 period_end 기준으로 바꿈)."""
    company = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Micron",
        currency="USD", primary_ticker="MU",
    )
    id1 = companies_repo.upsert_financial_period(
        db_conn, company_id=company["id"], fiscal_year=2026, fiscal_period="Q2",
        period_type="QUARTER", period_end="2026-02-28", filed_at=None, source="SEC",
    )
    id2 = companies_repo.upsert_financial_period(
        db_conn, company_id=company["id"], fiscal_year=2026, fiscal_period="Q2",
        period_type="QUARTER", period_end="2026-02-28", filed_at="2026-03-20", source="SEC",
    )
    assert id1 == id2
    periods = companies_repo.get_financial_periods(db_conn, company["id"], "QUARTER")
    assert periods[0]["filed_at"] == "2026-03-20"


def test_upsert_financial_period_different_period_end_creates_new_row(db_conn):
    """fiscal_year/fiscal_period가 우연히 같아도(SEC 원본 라벨 중복 가능성),
    period_end가 다르면 별개의 분기로 저장돼야 한다."""
    company = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Micron",
        currency="USD", primary_ticker="MU",
    )
    id1 = companies_repo.upsert_financial_period(
        db_conn, company_id=company["id"], fiscal_year=2026, fiscal_period="Q1",
        period_type="QUARTER", period_end="2024-11-28", filed_at="2025-12-18", source="SEC",
    )
    id2 = companies_repo.upsert_financial_period(
        db_conn, company_id=company["id"], fiscal_year=2026, fiscal_period="Q1",
        period_type="QUARTER", period_end="2025-11-27", filed_at="2025-12-18", source="SEC",
    )
    assert id1 != id2
    periods = companies_repo.get_financial_periods(db_conn, company["id"], "QUARTER")
    assert len(periods) == 2


def test_get_financial_periods_orders_oldest_to_newest(db_conn):
    company = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Micron",
        currency="USD", primary_ticker="MU",
    )
    for q, end in [("Q4", "2025-11-30"), ("Q1", "2026-02-28"), ("Q2", "2026-05-31")]:
        companies_repo.upsert_financial_period(
            db_conn, company_id=company["id"], fiscal_year=2026, fiscal_period=q,
            period_type="QUARTER", period_end=end, filed_at=None, source="SEC",
        )
    periods = companies_repo.get_financial_periods(db_conn, company["id"], "QUARTER")
    ends = [p["period_end"] for p in periods]
    assert ends == sorted(ends)  # 과거 -> 최신


def test_record_filing_dedups_by_accession_number(db_conn):
    company = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Micron",
        currency="USD", primary_ticker="MU",
    )
    for _ in range(2):
        companies_repo.record_filing(
            db_conn, company_id=company["id"], source="SEC", filing_type="10-Q",
            filed_at="2026-03-20", period_end="2026-02-28",
            accession_or_receipt_no="0000723125-26-000042", url="https://sec.gov/x",
        )
    filings = companies_repo.get_filings(db_conn, company["id"])
    assert len(filings) == 1


def test_company_analysis_request_lifecycle(db_conn):
    company = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Micron",
        currency="USD", primary_ticker="MU",
    )
    request = company_analysis_repo.create_request(db_conn, company_id=company["id"])
    assert request["status"] == "PENDING"

    company_analysis_repo.update_request_status(db_conn, request["id"], "COLLECTING_DATA")
    fetched = company_analysis_repo.get_request(db_conn, request["id"])
    assert fetched["status"] == "COLLECTING_DATA"

    result = company_analysis_repo.save_result(
        db_conn, request_id=request["id"], company_id=company["id"],
        snapshot_json="{}", result_text="분석 결과", provider="claude", model="claude-sonnet-5",
    )
    assert result["result_text"] == "분석 결과"
    assert company_analysis_repo.get_result_by_request(db_conn, request["id"])["id"] == result["id"]


def test_list_recent_companies_only_includes_completed(db_conn):
    company = companies_repo.create_company(
        db_conn, country="US", security_type="STOCK", name="Micron",
        currency="USD", primary_ticker="MU",
    )
    pending_req = company_analysis_repo.create_request(db_conn, company_id=company["id"])
    assert company_analysis_repo.list_recent_companies(db_conn) == []

    company_analysis_repo.update_request_status(db_conn, pending_req["id"], "COMPLETED")
    recent = company_analysis_repo.list_recent_companies(db_conn)
    assert len(recent) == 1
    assert recent[0]["primary_ticker"] == "MU"
