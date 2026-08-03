# tests/test_company_analysis_service.py
# company_analysis_service.py -- 스냅샷 조합/파이프라인 상태전이/더미모드.
# SEC/LLM 호출은 전부 monkeypatch(실제 네트워크 없음).
import pytest

from atrsite.adapters import llm_client
from atrsite.adapters import opendart_client
from atrsite.adapters import sec_edgar_client
from atrsite.repositories import companies as companies_repo
from atrsite.repositories import company_analysis as company_analysis_repo
from atrsite.services import company_analysis_service as svc


@pytest.fixture()
def no_opendart_key():
    """이 개발 환경의 .env에 실제 OpenDART 키가 들어있을 수 있어서(2026-08-03
    이후), "키 없음" 상태를 검증하는 테스트는 명시적으로 비운다."""
    from atrsite.config import settings
    original = settings.opendart_api_key
    object.__setattr__(settings, "opendart_api_key", "")
    yield
    object.__setattr__(settings, "opendart_api_key", original)


@pytest.fixture()
def fake_opendart_key():
    from atrsite.config import settings
    original = settings.opendart_api_key
    object.__setattr__(settings, "opendart_api_key", "dummy-opendart-key")
    yield
    object.__setattr__(settings, "opendart_api_key", original)


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


def test_build_snapshot_rejects_kr_company_without_opendart_key(db_conn, no_opendart_key):
    company = companies_repo.create_company(
        db_conn, country="KR", security_type="STOCK", name="삼성전자",
        currency="KRW", primary_ticker="005930",
    )
    with pytest.raises(svc.CompanyAnalysisError):
        svc.build_snapshot(db_conn, company)


def test_build_snapshot_kr_company_with_opendart_key(db_conn, fake_opendart_key, monkeypatch):
    """2026-08-03 OpenDART 키 발급 후 실제 라이브 검증(삼성전자 기준)한
    응답 패턴을 그대로 재현 -- Q4는 연간에서 3분기 누적을 뺀 값이어야
    하고, 현금흐름은 분기별로 정확히 나뉘어야 한다."""
    def fake_normalize(corp_code, *, period_type, years, fs_div="CFS", http=None):
        if period_type == "ANNUAL":
            return []
        return [
            opendart_client.NormalizedPeriod(
                fiscal_year=2025, fiscal_period="Q4", period_type="QUARTER", period_end="2025-12-31",
                filed_at=None, accession_no="20260310002820",
                metrics={"revenue": 90000000000000.0, "gross_profit": 35000000000000.0,
                         "operating_income": 14000000000000.0, "net_income": 11000000000000.0,
                         "cash": 57856378000000.0, "receivables": 51127642000000.0,
                         "inventory": 52636828000000.0, "operating_cash_flow": 28800000000000.0,
                         "capex": 12000000000000.0, "eps_diluted": 1500.0},
            ),
            opendart_client.NormalizedPeriod(
                fiscal_year=2026, fiscal_period="Q1", period_type="QUARTER", period_end="2026-03-31",
                filed_at=None, accession_no="20260515002181",
                metrics={"revenue": 133873444000000.0, "gross_profit": 81913173000000.0,
                         "operating_income": 57232797000000.0, "net_income": 47225272000000.0,
                         "cash": 60000000000000.0, "receivables": 55000000000000.0,
                         "inventory": 53000000000000.0, "operating_cash_flow": None,
                         "capex": None, "eps_diluted": 7123.0},
            ),
        ]

    monkeypatch.setattr(opendart_client, "normalize_periods", fake_normalize)

    company = svc.resolve_company_from_kr_match(db_conn, corp_code="00126380", corp_name="삼성전자", stock_code="005930")
    snapshot = svc.build_snapshot(db_conn, company)
    assert snapshot["company"]["ticker"] == "005930"
    assert snapshot["source"] == "OpenDART (전자공시)"
    assert len(snapshot["quarterly_periods"]) == 2
    assert snapshot["quarterly_periods"][-1]["metrics"]["revenue"] == 133873444000000.0


def test_resolve_company_from_kr_match_reuses_existing_by_corp_code(db_conn):
    first = svc.resolve_company_from_kr_match(db_conn, corp_code="00126380", corp_name="삼성전자", stock_code="005930")
    second = svc.resolve_company_from_kr_match(db_conn, corp_code="00126380", corp_name="삼성전자", stock_code="005930")
    assert first["id"] == second["id"]


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


def test_search_companies_notes_missing_opendart_key(monkeypatch, no_opendart_key):
    monkeypatch.setattr(
        sec_edgar_client, "search_companies",
        lambda q, **kw: [sec_edgar_client.CompanyMatch(cik=723125, ticker="MU", title="MICRON TECHNOLOGY INC")],
    )
    result = svc.search_companies("MU")
    assert result["results"][0]["ticker"] == "MU"
    assert any("OpenDART" in n for n in result["notices"])


def test_search_companies_includes_kr_matches_when_configured(monkeypatch, fake_opendart_key):
    monkeypatch.setattr(sec_edgar_client, "search_companies", lambda q, **kw: [])
    monkeypatch.setattr(
        opendart_client, "search_companies",
        lambda q, **kw: [opendart_client.DartCompanyMatch(corp_code="00126380", corp_name="삼성전자", stock_code="005930")],
    )
    result = svc.search_companies("삼성전자")
    assert result["results"][0]["country"] == "KR"
    assert result["results"][0]["stock_code"] == "005930"
    assert result["notices"] == []
