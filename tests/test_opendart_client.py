# tests/test_opendart_client.py
# opendart_client.py -- 키 없는 더미 모드 + (2026-08-03 실제 키로 라이브
# 검증 완료 후) 합성 픽스처로 파싱 로직 검증. corpCode.xml은 진짜 ZIP을
# 만들어서 monkeypatch로 준다.
import zipfile
from io import BytesIO

import httpx
import pytest

from atrsite.adapters import opendart_client
from atrsite.config import settings


@pytest.fixture()
def no_opendart_key():
    original = settings.opendart_api_key
    object.__setattr__(settings, "opendart_api_key", "")
    yield
    object.__setattr__(settings, "opendart_api_key", original)


@pytest.fixture()
def fake_opendart_key():
    original = settings.opendart_api_key
    object.__setattr__(settings, "opendart_api_key", "dummy-opendart-key")
    yield
    object.__setattr__(settings, "opendart_api_key", original)
    opendart_client._corp_code_cache["data"] = None
    opendart_client._corp_code_cache["fetched_at"] = 0.0


def test_is_configured_false_without_key(no_opendart_key):
    assert opendart_client.is_configured() is False


def test_search_companies_returns_empty_without_key(no_opendart_key):
    assert opendart_client.search_companies("삼성전자") == []


def test_fetch_financial_statements_raises_without_key(no_opendart_key):
    with pytest.raises(opendart_client.OpenDartError):
        opendart_client.fetch_financial_statements("00126380", bsns_year="2025", reprt_code="11011")


def _make_corpcode_zip():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<result>'
        '<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>'
        '<corp_eng_name>Samsung Electronics</corp_eng_name><stock_code>005930</stock_code>'
        '<modify_date>20260101</modify_date></list>'
        '<list><corp_code>00164779</corp_code><corp_name>삼성전자서비스</corp_name>'
        '<corp_eng_name>Samsung Electronics Service</corp_eng_name><stock_code> </stock_code>'
        '<modify_date>20260101</modify_date></list>'
        '<list><corp_code>00164742</corp_code><corp_name>삼성SDI</corp_name>'
        '<corp_eng_name>Samsung SDI</corp_eng_name><stock_code>006400</stock_code>'
        '<modify_date>20260101</modify_date></list>'
        '</result>'
    ).encode("utf-8")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def test_search_companies_exact_stock_code_first(fake_opendart_key, monkeypatch):
    zip_bytes = _make_corpcode_zip()

    def fake_get(self, url, params=None, timeout=None):
        class FakeResponse:
            content = zip_bytes
            def raise_for_status(self):
                pass
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    results = opendart_client.search_companies("005930")
    assert results[0].corp_name == "삼성전자"
    assert results[0].corp_code == "00126380"


def test_search_companies_filters_out_no_stock_code(fake_opendart_key, monkeypatch):
    zip_bytes = _make_corpcode_zip()

    def fake_get(self, url, params=None, timeout=None):
        class FakeResponse:
            content = zip_bytes
            def raise_for_status(self):
                pass
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    results = opendart_client.search_companies("삼성")
    names = {r.corp_name for r in results}
    assert "삼성전자서비스" not in names  # stock_code 없음(비상장) -- 제외돼야 함
    assert "삼성전자" in names
    assert "삼성SDI" in names


def test_search_companies_name_substring_match(fake_opendart_key, monkeypatch):
    zip_bytes = _make_corpcode_zip()

    def fake_get(self, url, params=None, timeout=None):
        class FakeResponse:
            content = zip_bytes
            def raise_for_status(self):
                pass
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    results = opendart_client.search_companies("SDI")
    assert any(r.corp_name == "삼성SDI" for r in results)


def _fs_row(sj_div, account_id, thstrm, *, thstrm_add=None, rcept_no="20260101000001"):
    row = {"sj_div": sj_div, "account_id": account_id, "thstrm_amount": str(thstrm), "rcept_no": rcept_no}
    if thstrm_add is not None:
        row["thstrm_add_amount"] = str(thstrm_add)
    return row


def test_fetch_financial_statements_raises_on_bad_status(fake_opendart_key, monkeypatch):
    def fake_get(self, url, params=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"status": "013", "message": "조회된 데이터가 없습니다."}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    with pytest.raises(opendart_client.OpenDartError):
        opendart_client.fetch_financial_statements("00126380", bsns_year="2025", reprt_code="11011")


def test_normalize_periods_annual_extracts_is_bs_cf(fake_opendart_key, monkeypatch):
    rows = [
        _fs_row("IS", "ifrs-full_Revenue", 333605938000000),
        _fs_row("IS", "ifrs-full_GrossProfit", 131370425000000),
        _fs_row("BS", "ifrs-full_CashAndCashEquivalents", 57856378000000),
        _fs_row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", 85315148000000),
    ]

    def fake_get(self, url, params=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"status": "000", "message": "정상", "list": rows}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    periods = opendart_client.normalize_periods("00126380", period_type="ANNUAL", years=[2025])
    assert len(periods) == 1
    p = periods[0]
    assert p.fiscal_period == "FY"
    assert p.metrics["revenue"] == 333605938000000
    assert p.metrics["cash"] == 57856378000000
    assert p.metrics["operating_cash_flow"] == 85315148000000


def test_normalize_periods_quarterly_subtracts_cumulative_cash_flow(fake_opendart_key, monkeypatch):
    """실제 라이브 검증(2026-08-03, 삼성전자)으로 확인한 패턴 재현: IS는
    분기단독 그대로, CF는 누적이라 구간별로 빼야 한다."""
    def rows_for(reprt_code):
        if reprt_code == opendart_client.REPORT_CODE_Q1:
            return [
                _fs_row("IS", "ifrs-full_Revenue", 100),
                _fs_row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", 10),  # Q1 누적=Q1 단독
            ]
        if reprt_code == opendart_client.REPORT_CODE_H1:
            return [
                _fs_row("IS", "ifrs-full_Revenue", 120, thstrm_add=220),  # Q2 단독=120
                _fs_row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", 25),  # H1 누적=25(Q1:10+Q2:15)
            ]
        if reprt_code == opendart_client.REPORT_CODE_Q3:
            return [
                _fs_row("IS", "ifrs-full_Revenue", 130, thstrm_add=350),  # Q3 단독=130
                _fs_row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", 45),  # 9개월 누적=45(+20)
            ]
        if reprt_code == opendart_client.REPORT_CODE_ANNUAL:
            return [
                _fs_row("IS", "ifrs-full_Revenue", 480),  # 연간 총합
                _fs_row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", 70),  # 연간 총합(+25 for Q4)
            ]
        return []

    def fake_get(self, url, params=None, timeout=None):
        reprt_code = params["reprt_code"]

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"status": "000", "message": "정상", "list": rows_for(reprt_code)}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    periods = opendart_client.normalize_periods("00126380", period_type="QUARTER", years=[2025])
    assert [p.fiscal_period for p in periods] == ["Q1", "Q2", "Q3", "Q4"]

    revenues = {p.fiscal_period: p.metrics["revenue"] for p in periods}
    assert revenues == {"Q1": 100, "Q2": 120, "Q3": 130, "Q4": 130}  # Q4 = 480(연간) - 350(3분기 누적)

    ocf = {p.fiscal_period: p.metrics["operating_cash_flow"] for p in periods}
    assert ocf["Q1"] == 10
    assert ocf["Q2"] == 15   # 25(H1 누적) - 10(Q1)
    assert ocf["Q3"] == 20   # 45(9개월 누적) - 25(H1 누적)
    assert ocf["Q4"] == 25   # 70(연간) - 45(9개월 누적)


def test_resolve_kr_company_detects_etf_by_name_keyword():
    from atrsite.services import company_analysis_service as svc
    import sqlite3

    from atrsite import db as db_module
    conn = db_module.connect(":memory:")
    db_module.init_db(conn)
    company = svc.resolve_company_from_kr_match(conn, corp_code="00123456", corp_name="KODEX 200", stock_code="069500")
    assert company["security_type"] == "ETF"
    conn.close()


def test_normalize_periods_skips_quarters_with_no_real_data(fake_opendart_key, monkeypatch):
    """2026-08-03 실제 삼성전자로 라이브 검증 중 발견한 버그 재현: 아직
    공시되지 않은 미래 분기(리포트 자체가 없음)가 전부 None인 채로 그대로
    끼어들면 안 된다 -- _subtract_cf가 항상 CF_TAGS 전체 키를 채운
    dict(값은 None)를 돌려줘서 "dict가 비어있는가"로는 걸러지지 않던 것."""
    def fake_get(self, url, params=None, timeout=None):
        reprt_code = params["reprt_code"]

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                if reprt_code == opendart_client.REPORT_CODE_Q1:
                    return {"status": "000", "message": "정상", "list": [_fs_row("IS", "ifrs-full_Revenue", 100)]}
                # 나머지 분기는 아직 공시 전 -- 데이터 없음
                return {"status": "013", "message": "조회된 데이터가 없습니다."}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    periods = opendart_client.normalize_periods("00126380", period_type="QUARTER", years=[2026])
    assert [p.fiscal_period for p in periods] == ["Q1"]
    assert periods[0].metrics["revenue"] == 100
