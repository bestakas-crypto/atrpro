"""매크로 브리핑용 외부 지수 조회 -- 2026-08-03 추가.

KIS는 국내/해외 "주식" 시세만 제공하고 VXN(나스닥100 변동성지수) 같은
지수 자체는 취급하지 않는다. 이 모듈은 Yahoo Finance의 비공식 조회
엔드포인트(별도 API 키 불필요)로 지수값을 가져온다.

주의: 이건 정식 계약된 데이터 공급자가 아니라 개인 프로젝트에서 흔히 쓰는
비공식 엔드포인트다 -- 언제든 형식이 바뀌거나 막힐 수 있다. 실패하면
예외를 던지지 않고 status="UNAVAILABLE"을 돌려준다(핵심 원칙: 데이터가
없으면 없다고 표시하고, LLM이 숫자를 지어내게 하면 안 됨). 나중에 더
안정적인 유료/공식 소스로 교체 가능하도록 이 함수의 반환 형태만 유지하면
된다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("atrsite.market_index")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# 자주 쓰는 심볼 -- Yahoo Finance 표기 그대로.
SYMBOL_VXN = "^VXN"   # 나스닥100 변동성지수
SYMBOL_VIX = "^VIX"   # S&P500 변동성지수


@dataclass(frozen=True)
class IndexQuote:
    symbol: str
    label: str
    status: str  # "OK" | "UNAVAILABLE"
    value: Optional[float] = None
    prev_close: Optional[float] = None
    change_pct: Optional[float] = None
    observed_at: Optional[str] = None  # ISO, 이 조회를 실행한 시각
    quote_time: Optional[str] = None   # ISO, Yahoo가 준 시세 시각(거래소 기준)
    source: str = "Yahoo Finance(비공식)"


def _unavailable(symbol: str, label: str) -> IndexQuote:
    return IndexQuote(symbol=symbol, label=label, status="UNAVAILABLE", observed_at=_now_iso())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_index(symbol: str, label: str, *, http: Optional[httpx.Client] = None) -> IndexQuote:
    """실패해도 예외를 던지지 않는다 -- 브리핑 생성 전체를 막으면 안 되고,
    호출자가 status로 판단해서 UI/프롬프트에 "데이터 없음"으로 명시하면
    된다."""
    owns_client = http is None
    client = http or httpx.Client()
    try:
        res = client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},  # 기본 UA로는 종종 차단됨
            timeout=10,
        )
        res.raise_for_status()
        payload = res.json()
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if result is None:
            raise ValueError(f"chart.result 비어있음: {payload}")
        meta = result.get("meta") or {}
        value = meta.get("regularMarketPrice")
        prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
        quote_ts = meta.get("regularMarketTime")

        if value is None:
            raise ValueError(f"regularMarketPrice 없음: {meta}")

        change_pct = None
        if prev_close:
            change_pct = (float(value) - float(prev_close)) / float(prev_close) * 100

        quote_time_iso = None
        if quote_ts:
            quote_time_iso = datetime.fromtimestamp(int(quote_ts), tz=timezone.utc).isoformat(timespec="seconds")

        return IndexQuote(
            symbol=symbol, label=label, status="OK",
            value=float(value),
            prev_close=float(prev_close) if prev_close is not None else None,
            change_pct=change_pct,
            observed_at=_now_iso(),
            quote_time=quote_time_iso,
        )
    except Exception as exc:  # noqa: BLE001 -- 브리핑은 계속 진행돼야 함
        logger.warning("지수 조회 실패 symbol=%s: %s", symbol, exc)
        return _unavailable(symbol, label)
    finally:
        if owns_client:
            client.close()
