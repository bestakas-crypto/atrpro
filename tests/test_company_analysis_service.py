# tests/test_company_analysis_service.py
# company_analysis_service.py -- 스냅샷 조합/파이프라인 상태전이/더미모드.
# SEC/LLM 호출은 전부 monkeypatch(실제 네트워크 없음).
import pytest

from atrsite.adapters import llm_client
from atrsite.adapters import sec_edgar_client
from atrsite.repositories import companies as companies_repo
from atrsite.repositories import company_analysis as company_analysis_repo
from atrsite.services import company_analysis_service as svc


@pytest.fixture(autouse=True)
def force_llm_dummy_mode(monkeypatch):
    from atrsite.config import settings
    original = (
        settings.anthropic_api_key, settings.openai_api_key,
        settings.gemini_api_key, settings.deepseek_api_key,
    )
    object.__setattr__(settings, "anthropic_api_key", "")
    object.__setattr__(settings, "openai_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "")
    object.__setattr__(settings, "deepseek_api_key", "")
    yield
    object.__setattr__(settings, "anthropic_api_key", original[0])
    object.__setattr__(settings, "openai_api_key", original[1])
    object.__setattr__(settings, "gemini_api_key", original[2])
    object.__setattr__(settings, "deepseek_api_key", original[3])


def _fake_periods(period_type, limit):
    if period_type != "QUARTER":
        return []
    return [
        sec_edgar_client.NormalizedPeriod(
            fiscal_year=2025, fiscal_period="Q4", period_type="QUARTER", period_end="2025-11-27",
            filed_at="2025-12-18", accession_no="0000723125-25-000046",
            metrics={"revenue": 13643000000.0, "gross_profit": 7646000000.0, "net_income": 5240000000.0,
                     "operating_income": 6136000000.0, "operating_cash_flow": 8411000000.0, "capex": 5389000000.0,
                     "cash": 9731000000.0, "total_debt": 8844000000.0, "receivables": 8009000000.0,
                     "inventory": 8205000000.0, "interest_expense": None, "eps_diluted": 4.7,
                     "shares_outstanding": 1126000000.0},
        ),
        sec_edgar_client.NormalizedPeriod(
            fiscal_year=2026, fiscal_period="Q1", period_type="QUARTER", period_end="2026-02-26",
            filed_at="2026-03-19", accession_no="0000723125-26-000006",
            metrics={"revenue": 23860000000.0, "gross_profit": 17755000000.0, "net_income": 13785000000.0,
                     "operating_income": 16135000000.0, "operating_cash_flow": None, "capex": None,
                     "cash": 13908000000.0, "total_debt": None, "receivables": 15389000000.0,
                     "inventory": 8267000000.0, "interest_expense": None, "eps_diluted": None,
                     "shares_outstanding": 1128000000.0},
        ),
    ]


@pytest.fixture(autouse=True)
def stub_sec(monkeypatch):
    monkeypatch.setattr(sec_edgar_client, "fetch_company_facts", lambda cik, **kw: {"stub": True})
    monkeypatch.setattr(sec_edgar_client, "normalize_periods", lambda facts, *, period_type, limit=8: _fake_periods(period_type, limit))


def test_resolve_company_detects_semiconductor_template(db_conn):
    company = svc.resolve_company_from_us_match(db_conn, cik=723125, ticker="MU", title="MICRON TECHNOLOGY INC")
    assert company["country"] == "US"
    assert company["security_type"] == "STOCK"
    assert company["industry_template"] == "semiconductor"


def test_resolve_company_detects_etf_by_title(db_conn):
    company = svc.resolve_company_from_us_match(db_conn, cik=1067839, ticker="QQQ", title="INVESCO QQQ TRUST, SERIES 1")
    assert company["security_type"] == "ETF"
    assert company["industry_template"] is None


def test_build_snapshot_includes_metrics_and_warnings(db_conn):
    company = svc.resolve_company_from_us_match(db_conn, cik=723125, ticker="MU", title="MICRON TECHNOLOGY INC")
    snapshot = svc.build_snapshot(db_conn, company)
    assert snapshot["company"]["ticker"] == "MU"
    assert len(snapshot["quarterly_periods"]) == 2
    assert snapshot["quarterly_periods"][-1]["metrics"]["revenue"] == 23860000000.0
    assert isinstance(snapshot["warnings"], list)
    assert snapshot["macro_context"] is None or "note" in snapshot["macro_context"]

    # 실제로 DB에 영속됐는지도 확인(캐시/재사용 근거 데이터)
    periods = companies_repo.get_financial_periods(db_conn, company["id"], "QUARTER")
    assert len(periods) == 2


def test_build_snapshot_rejects_kr_company(db_conn):
    company = companies_repo.create_company(
        db_conn, country="KR", security_type="STOCK", name="삼성전자",
        currency="KRW", primary_ticker="005930",
    )
    with pytest.raises(svc.CompanyAnalysisError):
        svc.build_snapshot(db_conn, company)


def test_build_snapshot_rejects_etf(db_conn):
    company = svc.resolve_company_from_us_match(db_conn, cik=1067839, ticker="QQQ", title="INVESCO QQQ TRUST, SERIES 1")
    with pytest.raises(svc.CompanyAnalysisError):
        svc.build_snapshot(db_conn, company)


def test_execute_pipeline_dummy_mode_completes(db_conn):
    company = svc.resolve_company_from_us_match(db_conn, cik=723125, ticker="MU", title="MICRON TECHNOLOGY INC")
    request = company_analysis_repo.create_request(db_conn, company_id=company["id"])

    svc.execute_pipeline(db_conn, request_id=request["id"], company_id=company["id"])

    updated = company_analysis_repo.get_request(db_conn, request["id"])
    assert updated["status"] == "COMPLETED"
    result = company_analysis_repo.get_result_by_request(db_conn, request["id"])
    assert result is not None
    assert result["provider"] == "dummy"


def test_execute_pipeline_two_stage_routing_when_keys_present(db_conn, monkeypatch):
    from atrsite.config import settings
    object.__setattr__(settings, "anthropic_api_key", "dummy-anthropic-key")
    object.__setattr__(settings, "gemini_api_key", "dummy-gemini-key")

    calls = []

    def fake_ask(system, user, *, use_web_search=False, chain=None, max_tokens=None):
        calls.append(chain)
        if chain == "stage1_search":
            return llm_client.LLMResponse(text="1단계 결과", provider="gemini", model="gemini-test")
        return llm_client.LLMResponse(text="2단계 최종 분석", provider="claude", model="claude-test")

    monkeypatch.setattr(llm_client, "ask", fake_ask)

    company = svc.resolve_company_from_us_match(db_conn, cik=723125, ticker="MU", title="MICRON TECHNOLOGY INC")
    request = company_analysis_repo.create_request(db_conn, company_id=company["id"])
    svc.execute_pipeline(db_conn, request_id=request["id"], company_id=company["id"])

    assert calls == ["stage1_search", "stage2_judgment"]
    result = company_analysis_repo.get_result_by_request(db_conn, request["id"])
    assert result["result_text"] == "2단계 최종 분석"


def test_execute_pipeline_marks_failed_on_error(db_conn, monkeypatch):
    def raise_error(cik, **kw):
        raise sec_edgar_client.SecEdgarError("네트워크 오류")

    monkeypatch.setattr(sec_edgar_client, "fetch_company_facts", raise_error)

    company = svc.resolve_company_from_us_match(db_conn, cik=723125, ticker="MU", title="MICRON TECHNOLOGY INC")
    request = company_analysis_repo.create_request(db_conn, company_id=company["id"])
    svc.execute_pipeline(db_conn, request_id=request["id"], company_id=company["id"])

    updated = company_analysis_repo.get_request(db_conn, request["id"])
    assert updated["status"] == "FAILED"
    assert "네트워크 오류" in updated["error_message"]


def test_search_companies_notes_missing_opendart_key(monkeypatch):
    monkeypatch.setattr(
        sec_edgar_client, "search_companies",
        lambda q, **kw: [sec_edgar_client.CompanyMatch(cik=723125, ticker="MU", title="MICRON TECHNOLOGY INC")],
    )
    result = svc.search_companies("MU")
    assert result["results"][0]["ticker"] == "MU"
    assert any("OpenDART" in n for n in result["notices"])
