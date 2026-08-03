# tests/test_sec_edgar_client.py
# sec_edgar_client.py 단위 테스트 -- 합성 픽스처로 파싱/라벨링 로직만 검증
# (실제 SEC 서버 호출 없음, kis_client.py/market_index_client.py와 동일한
# 원칙). search_companies/fetch_company_facts의 HTTP 계층은 monkeypatch.
import httpx
import pytest

from atrsite.adapters import sec_edgar_client as sec


def _duration_entry(start, end, val, *, fy, fp, filed, accn="0000000000-26-000001", form="10-Q"):
    return {"start": start, "end": end, "val": val, "fy": fy, "fp": fp, "filed": filed, "accn": accn, "form": form}


def _instant_entry(end, val, *, filed, accn="0000000000-26-000001"):
    return {"end": end, "val": val, "filed": filed, "accn": accn}


def test_normalize_periods_quarterly_basic():
    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                    _duration_entry("2025-11-28", "2026-02-26", 23860000000, fy=2026, fp="Q2", filed="2026-03-19"),
                    _duration_entry("2025-08-29", "2026-02-26", 37503000000, fy=2026, fp="Q2", filed="2026-03-19"),  # YTD, 걸러져야 함
                ]}},
                "GrossProfit": {"units": {"USD": [
                    _duration_entry("2025-11-28", "2026-02-26", 17755000000, fy=2026, fp="Q2", filed="2026-03-19"),
                ]}},
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
                    _instant_entry("2026-02-26", 13908000000, filed="2026-03-19"),
                ]}},
            }
        }
    }
    periods = sec.normalize_periods(facts, period_type="QUARTER", limit=8)
    assert len(periods) == 1
    p = periods[0]
    assert p.period_end == "2026-02-26"
    assert p.metrics["revenue"] == 23860000000  # 90일짜리만, YTD(181일)는 제외
    assert p.metrics["gross_profit"] == 17755000000
    assert p.metrics["cash"] == 13908000000
    assert p.metrics["net_income"] is None  # 데이터 없는 지표는 None(임의로 안 채움)


def test_normalize_periods_dedups_by_latest_filed():
    """같은 기간이 두 번 신고되면(정정 등) 가장 최근 filed 값을 써야 한다."""
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _duration_entry("2025-11-28", "2026-02-26", 100, fy=2026, fp="Q2", filed="2026-03-19"),
            _duration_entry("2025-11-28", "2026-02-26", 999, fy=2026, fp="Q2", filed="2026-04-01"),  # 정정본, 더 최근
        ]}},
    }}}
    periods = sec.normalize_periods(facts, period_type="QUARTER", limit=8)
    assert periods[0].metrics["revenue"] == 999


def test_normalize_periods_labels_dont_collide_on_bad_sec_fy_tags():
    """실제 라이브 검증(2026-08-03)에서 발견한 문제 재현: 전년동기 비교
    컬럼이 SEC 원본 fy/fp 태그에서 당해년도와 같은 라벨(fy=2026,fp=Q1)로
    잘못 붙어 있어도, 우리는 period_end로 직접 계산하므로 서로 다른
    라벨이 나와야 한다(겹치면 DB UNIQUE 제약과 충돌해 데이터가 사라짐)."""
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            # 둘 다 SEC 원본 태그는 fy=2026/fp=Q1 이지만 실제로는 1년 차이
            _duration_entry("2024-08-29", "2024-11-28", 8709000000, fy=2026, fp="Q1", filed="2025-12-18"),
            _duration_entry("2025-08-29", "2025-11-27", 13643000000, fy=2026, fp="Q1", filed="2025-12-18"),
        ]}},
    }}}
    periods = sec.normalize_periods(facts, period_type="QUARTER", limit=8)
    labels = [(p.fiscal_year, p.fiscal_period) for p in periods]
    assert len(labels) == len(set(labels)), f"라벨 충돌 발생: {labels}"
    assert periods[0].period_end == "2024-11-28"
    assert periods[1].period_end == "2025-11-27"
    assert periods[0].fiscal_year == 2024
    assert periods[1].fiscal_year == 2025


def test_normalize_periods_annual_uses_wider_day_range():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _duration_entry("2024-08-30", "2025-08-28", 37378000000, fy=2025, fp="FY", filed="2025-10-03", form="10-K"),
            _duration_entry("2025-11-28", "2026-02-26", 23860000000, fy=2026, fp="Q2", filed="2026-03-19"),  # 분기, 걸러져야 함
        ]}},
    }}}
    annual = sec.normalize_periods(facts, period_type="ANNUAL", limit=4)
    assert len(annual) == 1
    assert annual[0].fiscal_period == "FY"
    assert annual[0].metrics["revenue"] == 37378000000


def test_normalize_periods_empty_when_no_revenue():
    assert sec.normalize_periods({"facts": {"us-gaap": {}}}, period_type="QUARTER") == []


def test_search_companies_ranks_exact_ticker_first(monkeypatch):
    payload = {
        "0": {"cik_str": 1067839, "ticker": "QQQ", "title": "INVESCO QQQ TRUST, SERIES 1"},
        "1": {"cik_str": 723125, "ticker": "MU", "title": "MICRON TECHNOLOGY INC"},
        "2": {"cik_str": 99999, "ticker": "MUX", "title": "SOME OTHER MU COMPANY"},
    }

    def fake_get(self, url, headers=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self): return payload
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    sec._tickers_cache["data"] = None
    sec._tickers_cache["fetched_at"] = 0.0

    results = sec.search_companies("MU")
    assert results[0].ticker == "MU"  # 완전일치가 먼저
    assert results[1].ticker == "MUX"  # 접두사 일치가 다음


def test_fetch_company_facts_wraps_http_errors(monkeypatch):
    def fake_get(self, url, headers=None, timeout=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    with pytest.raises(sec.SecEdgarError):
        sec.fetch_company_facts(723125)
