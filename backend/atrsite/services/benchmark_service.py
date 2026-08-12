"""backend/atrsite/services/benchmark_service.py -- 벤치마크 지수(코스피/
S&P500) 조회, analyze.kunoh.top 4단계(2026-08-12 추가).

KIS가 이미 제공하는 지수 조회 TR을 그대로 쓴다(kis_client.py 참고) --
Yahoo Finance 같은 외부 비공식 API 대신, 이미 연동돼 있고 신뢰성이 검증된
KIS를 재사용하는 게 낫다고 판단함. 이 모듈은 DB에 아무 것도 저장하지
않는다(그때그때 KIS를 호출해서 반환만 함 -- analyze.kunoh.top이 원래
"자체 DB 없음" 원칙으로 설계됐고, 이 지수 데이터도 strpro 입장에서 딱히
영속화할 이유가 없는 순수 조회성 데이터라 동일하게 취급).
"""
from __future__ import annotations

from typing import Any

from ..adapters.kis_client import KOSPI_INDEX_CODE, SP500_INDEX_CODE, get_client


def _bars_to_items(bars: list, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """DailyBar 리스트를 API 응답 형태로 변환하면서 요청 범위로 한 번 더
    필터링한다 -- 국내지수 TR은 시작일 없이 기준일만 받아서 그보다 이른
    데이터까지 돌려주므로, 여기서 start_date 미만은 잘라낸다."""
    return [
        {"date": b.trade_date, "close": b.close}
        for b in bars
        if start_date <= b.trade_date <= end_date
    ]


def get_kospi_daily(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """코스피(업종코드 0001) 일별 종가. KIS 국내지수 TR은 기준일(종료일) 하나만
    받아서 거기서부터 과거로 데이터를 주므로, end_date를 기준일로 넘긴다.
    한 번 호출로 돌아오는 기간보다 더 과거를 요청하면 그만큼은 정직하게
    빠진다(추가 페이징 호출 없음 -- 이번 범위에선 짧은 기간 비교가 주 용도라
    과설계하지 않음)."""
    client = get_client()
    bars = client.get_domestic_index_daily(KOSPI_INDEX_CODE, base_date=end_date)
    return _bars_to_items(bars, start_date, end_date)


def get_sp500_daily(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """S&P500(SPX) 일별 종가. 해외지수 TR은 시작/종료를 둘 다 받는다."""
    client = get_client()
    bars = client.get_overseas_index_daily(SP500_INDEX_CODE, start_date=start_date, end_date=end_date)
    return _bars_to_items(bars, start_date, end_date)
