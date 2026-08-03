# backend/atrsite/adapters/sec_edgar_client.py
# 미국 기업 재무 데이터 -- SEC EDGAR 공개 API(키 불필요, User-Agent 필수).
# market_index_client.py와 동일한 원칙: 실패해도 절대 예외로 파이프라인을
# 죽이지 않고, 호출자가 "자료 없음"으로 명확히 처리할 수 있게 구조화된
# 결과를 돌려주거나 명시적 예외 하나로 통일해서 던진다.
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ..config import settings

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL_TMPL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# 분기(약 91일)/연간(약 365일) 구간만 골라내기 위한 허용 오차 -- SEC의
# duration 사실(revenue 등)에는 분기 단독 값과 반기/3분기 누적값이 섞여
# 들어오므로(2026-08-03 실제 MU 데이터로 확인), 일수 범위로 걸러낸다.
QUARTER_DAYS_RANGE = (80, 100)
ANNUAL_DAYS_RANGE = (350, 380)

_TICKERS_CACHE_TTL_SECONDS = 24 * 60 * 60
_tickers_cache: dict = {"data": None, "fetched_at": 0.0}

# 지표별 후보 태그(회사/연도마다 실제 쓰는 태그명이 다를 수 있어 여러 개
# 시도) -- duration(손익/현금흐름, 기간 값)과 instant(재무상태표, 시점 값)
# 두 종류로 나눈다.
DURATION_METRIC_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "interest_expense": ["InterestExpense", "InterestExpenseDebt"],
    "eps_diluted": ["EarningsPerShareDiluted"],
}
INSTANT_METRIC_TAGS: dict[str, list[str]] = {
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "total_debt": ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "shares_outstanding": ["CommonStockSharesOutstanding"],
}


class SecEdgarError(RuntimeError):
    """SEC EDGAR 호출/파싱 실패."""


@dataclass(frozen=True)
class CompanyMatch:
    cik: int
    ticker: str
    title: str


@dataclass(frozen=True)
class NormalizedPeriod:
    fiscal_year: int
    fiscal_period: str  # 'Q1'..'Q4' or 'FY'
    period_type: str    # 'QUARTER' or 'ANNUAL'
    period_end: str
    filed_at: Optional[str]
    accession_no: Optional[str]
    metrics: dict = field(default_factory=dict)  # metric_key -> value(float) or None


def _headers() -> dict:
    return {"User-Agent": settings.sec_user_agent}


def _get_json(url: str, *, http: httpx.Client) -> dict:
    try:
        res = http.get(url, headers=_headers(), timeout=20)
        res.raise_for_status()
        return res.json()
    except httpx.HTTPError as exc:
        raise SecEdgarError(f"SEC EDGAR 호출 실패: {url} -- {exc}") from exc


def search_companies(query: str, *, http: Optional[httpx.Client] = None, limit: int = 10) -> list[CompanyMatch]:
    """company_tickers.json(전체 상장사 티커/CIK 목록, 약 1만 개)을 캐시해두고
    티커 완전일치를 최상위로, 이름/티커 부분일치를 그 다음으로 반환한다."""
    now = time.time()
    if _tickers_cache["data"] is None or now - _tickers_cache["fetched_at"] > _TICKERS_CACHE_TTL_SECONDS:
        owns_client = http is None
        client = http or httpx.Client()
        try:
            data = _get_json(TICKERS_URL, http=client)
        finally:
            if owns_client:
                client.close()
        _tickers_cache["data"] = list(data.values())
        _tickers_cache["fetched_at"] = now

    q = query.strip().upper()
    if not q:
        return []
    all_items = _tickers_cache["data"]

    exact_ticker = [it for it in all_items if it["ticker"].upper() == q]
    prefix_ticker = [it for it in all_items if it["ticker"].upper().startswith(q) and it not in exact_ticker]
    name_match = [
        it for it in all_items
        if q in it["title"].upper() and it not in exact_ticker and it not in prefix_ticker
    ]
    ordered = exact_ticker + prefix_ticker + name_match
    return [CompanyMatch(cik=it["cik_str"], ticker=it["ticker"], title=it["title"]) for it in ordered[:limit]]


def fetch_company_facts(cik: int, *, http: Optional[httpx.Client] = None) -> dict:
    owns_client = http is None
    client = http or httpx.Client()
    try:
        return _get_json(COMPANY_FACTS_URL_TMPL.format(cik=cik), http=client)
    finally:
        if owns_client:
            client.close()


def _duration_days(start: str, end: str) -> int:
    from datetime import date
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return (e - s).days


def _calendar_quarter_label(period_end: str, period_type: str) -> tuple[int, str]:
    """period_end로부터 (연도, 분기) 라벨을 계산한다 -- 회사 자체의 회계연도
    표기(예: 마이크론 FY2026 Q1)가 아니라 달력 분기다. SEC 원본 fy/fp
    태그는 비교(전년동기) 컬럼에 실제 기간과 다른 값이 붙는 경우가 실제
    라이브 데이터에서 확인돼서(2026-08-03) 신뢰할 수 없음 -- period_end
    날짜 자체(항상 정확함)로부터 직접, 결정론적으로 계산해서 절대 같은
    라벨이 서로 다른 기간에 겹치지 않게 한다."""
    from datetime import date
    d = date.fromisoformat(period_end)
    if period_type == "ANNUAL":
        return d.year, "FY"
    q = (d.month - 1) // 3 + 1
    return d.year, f"Q{q}"


def _extract_duration_facts(gaap: dict, tag_candidates: list[str], days_range: tuple[int, int]) -> dict:
    """(start, end) -> {val, filed, accn, fy, fp, form} -- 지정된 일수 범위(분기
    또는 연간)에 맞는 값만 남기고, 같은 기간이 여러 번 신고됐으면(정정 등)
    가장 최근 filed 값을 쓴다(스펙 5.2 "정정 공시 우선")."""
    best: dict = {}
    for tag in tag_candidates:
        entries = (gaap.get(tag) or {}).get("units", {}).get("USD") or []
        for e in entries:
            start, end = e.get("start"), e.get("end")
            if not start or not end:
                continue
            try:
                days = _duration_days(start, end)
            except ValueError:
                continue
            if not (days_range[0] <= days <= days_range[1]):
                continue
            key = (start, end)
            prev = best.get(key)
            if prev is None or (e.get("filed") or "") > (prev.get("filed") or ""):
                best[key] = e
        if best:
            break  # 첫 번째로 데이터가 있는 태그만 쓴다(같은 지표를 두 태그로 이중 집계하지 않음)
    return best


def _extract_instant_facts(gaap: dict, tag_candidates: list[str]) -> dict:
    """end -> {val, filed, accn} -- 대차대조표 항목(시점 값)."""
    best: dict = {}
    for tag in tag_candidates:
        entries = (gaap.get(tag) or {}).get("units", {}).get("USD") or (gaap.get(tag) or {}).get("units", {}).get("shares") or []
        for e in entries:
            end = e.get("end")
            if not end:
                continue
            prev = best.get(end)
            if prev is None or (e.get("filed") or "") > (prev.get("filed") or ""):
                best[end] = e
        if best:
            break
    return best


def normalize_periods(facts_json: dict, *, period_type: str = "QUARTER", limit: int = 8) -> list[NormalizedPeriod]:
    """companyfacts 원본 JSON을 최근 N개 분기(또는 연간)로 표준화한다.
    revenue를 기준(anchor) 지표로 삼아 기간 목록을 정하고, 나머지 지표는
    같은 기간(instant는 end 날짜, duration은 (start,end))으로 매칭해 채운다.
    매칭되는 값이 없는 지표는 None으로 남기고(스펙: 자료 부족을 임의로
    채우지 않음), 상위 호출자가 INSUFFICIENT_DATA로 표시한다."""
    gaap = (facts_json.get("facts") or {}).get("us-gaap") or {}
    days_range = QUARTER_DAYS_RANGE if period_type == "QUARTER" else ANNUAL_DAYS_RANGE

    duration_series: dict[str, dict] = {}
    for metric_key, tags in DURATION_METRIC_TAGS.items():
        duration_series[metric_key] = _extract_duration_facts(gaap, tags, days_range)

    instant_series: dict[str, dict] = {}
    for metric_key, tags in INSTANT_METRIC_TAGS.items():
        instant_series[metric_key] = _extract_instant_facts(gaap, tags)

    anchor = duration_series.get("revenue", {})
    if not anchor:
        return []

    anchor_keys = sorted(anchor.keys(), key=lambda k: k[1], reverse=True)[:limit]
    periods: list[NormalizedPeriod] = []
    for start, end in reversed(anchor_keys):  # 과거 -> 최신
        anchor_entry = anchor[(start, end)]
        metrics: dict[str, Optional[float]] = {}
        for metric_key, series in duration_series.items():
            entry = series.get((start, end))
            metrics[metric_key] = entry.get("val") if entry else None
        for metric_key, series in instant_series.items():
            entry = series.get(end)
            metrics[metric_key] = entry.get("val") if entry else None

        fiscal_year, fiscal_period = _calendar_quarter_label(end, period_type)
        periods.append(NormalizedPeriod(
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            period_type=period_type,
            period_end=end,
            filed_at=anchor_entry.get("filed"),
            accession_no=anchor_entry.get("accn"),
            metrics=metrics,
        ))
    return periods
