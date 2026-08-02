"""한국투자증권(KIS) Open API 클라이언트 -- 스펙 11절.

인증/토큰 처리(TokenCache)와 요청 패턴(_headers, rt_cd 검사)은
C:\\mmean\\core\\kis_data_api.py의 검증된 방식을 strpro 스타일로 다시 작성한
것이다 (그대로 복사하지 않음 -- 두 프로젝트는 계속 독립적으로 유지).
시세 조회 TR 자체(get_current_price/get_daily_bars의 실제 호출 본문)는 아직
TODO다: 이 저장소에는 실거래 계좌가 없어 응답 스키마를 실제로 찍어볼 수
없으므로, 사용자가 검증된 연결 코드를 직접 붙여넣을 자리로 남겨둔다.
"""
from __future__ import annotations

import hashlib
import random
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import httpx

from ..config import settings
from ..services.atr_engine import DailyBar

KIS_BASE_LIVE = "https://openapi.koreainvestment.com:9443"
KIS_BASE_VIRTUAL = "https://openapivts.koreainvestment.com:29443"
OAUTH_TOKEN_PATH = "/oauth2/tokenP"

# C:\strpro\manual의 TR 명세 문서로 이미 확인된 값 (재조사 불필요, 스펙 3단계 지시).
# 실제 요청 파라미터/응답 필드 매핑은 아직 채우지 않았다 -- 아래 TODO 참고.
TR_CURRENT_PRICE = "FHKST01010100"  # 주식현재가 시세 [v1_국내주식-008]
EP_CURRENT_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
TR_DAILY_CHART_PRICE = "FHKST03010100"  # 국내주식기간별시세(일_주_월_년) [v1_국내주식-016]
EP_DAILY_CHART_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"


class KisApiError(RuntimeError):
    """rt_cd != '0' 등 KIS API가 명시적으로 실패를 반환했을 때 발생."""


@dataclass(frozen=True)
class Quote:
    instrument_code: str
    price: float
    day_high: float
    quoted_at: str  # ISO datetime 문자열


class RateLimiter:
    """스펙 11.2 -- 모든 호출은 순차 Rate Limiter를 거친다.

    실전 REST 한도(계좌당 초당 18건, 100~150ms 권장 간격)보다 5분 폴링은
    훨씬 낮은 빈도지만, 한 폴링 주기 안에서 여러 종목을 연속 조회할 때
    호출 사이 최소 간격만 강제하는 단순한 구현이다.
    """

    def __init__(self, min_interval_seconds: float = 0.15):
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            remaining = self._min_interval - (time.monotonic() - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
            self._last_call = time.monotonic()


class TokenCache:
    """스펙 11.1 토큰 처리 순서 -- 저장된 토큰 확인 -> 만료시각 확인 -> 유효하면
    재사용 -> 만료 임박(5분 전) 시 재발급. 프로세스 생존 기간 동안 메모리에
    캐시해서 재시작마다 새로 발급하지 않는다 (C:\\mmean의 get_data_token 패턴).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._access_token: Optional[str] = None
        self._issued_at: float = 0.0
        self._expires_in: int = 0

    def get(self, http: httpx.Client, base_url: str) -> str:
        with self._lock:
            if self._access_token and (time.time() - self._issued_at) < (self._expires_in - 300):
                return self._access_token

        if not settings.kis_app_key or not settings.kis_app_secret:
            raise KisApiError("KIS_APP_KEY / KIS_APP_SECRET 미설정 -- .env 확인")

        res = http.post(
            base_url + OAUTH_TOKEN_PATH,
            json={
                "grant_type": "client_credentials",
                "appkey": settings.kis_app_key,
                "appsecret": settings.kis_app_secret,
            },
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        token = (data.get("access_token") or "").strip()
        expires_in = int(data.get("expires_in", 86400))
        if not token:
            raise KisApiError(f"access_token 발급 실패: {data}")

        with self._lock:
            self._access_token = token
            self._issued_at = time.time()
            self._expires_in = expires_in
        return token


class KisClient:
    def __init__(self):
        self._http = httpx.Client()
        self._token_cache = TokenCache()
        self.rate_limiter = RateLimiter()

    def _base_url(self) -> str:
        return KIS_BASE_VIRTUAL if settings.kis_is_paper_trading else KIS_BASE_LIVE

    def _headers(self, tr_id: str) -> dict[str, str]:
        token = self._token_cache.get(self._http, self._base_url())
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def get_current_price(self, instrument_code: str) -> Quote:
        """
        TODO: manual 폴더의 "주식현재가 시세[v1_국내주식-008]" 명세
        (TR FHKST01010100, GET /uapi/domestic-stock/v1/quotations/inquire-price)와
        C:\\mmean의 인증/요청 패턴(_headers, rt_cd 검사)을 참고해서 실제
        KIS 시세조회 TR 호출 코드를 채울 것.
        임시로는 테스트용 더미 데이터를 반환하도록 구현해서 나머지
        파이프라인(ATR 계산, 신호 판정)이 이 함수 결과만으로 정상 동작하는지
        먼저 검증할 것.
        """
        self.rate_limiter.wait()
        return _dummy_quote(instrument_code)

    def get_daily_bars(self, instrument_code: str, start_date: str, end_date: str) -> list[DailyBar]:
        """
        TODO: manual 폴더의 "국내주식기간별시세(일_주_월_년)[v1_국내주식-016]"
        명세 (TR FHKST03010100, GET
        /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice,
        FID_ORG_ADJ_PRC=0 수정주가, FID_PERIOD_DIV_CODE=D 일봉)와 C:\\mmean의
        요청 패턴을 참고해서 실제 확정 일봉 조회 코드를 채울 것.
        임시로는 테스트용 더미 데이터(atr_engine.DailyBar 리스트, 최소 15개
        이상)를 반환하도록 구현해서 ATR 계산 파이프라인이 이 함수 결과만으로
        정상 동작하는지 먼저 검증할 것.
        """
        self.rate_limiter.wait()
        return _dummy_daily_bars(instrument_code, start_date, end_date)

    def close(self) -> None:
        self._http.close()


# ---------------------------------------------------------------------------
# 더미 데이터 -- 실제 KIS 연동 전까지 파이프라인(시세->ATR->신호) 검증용.
# 종목코드로 시드를 고정해 매 호출 결과가 안정적으로 재현되게 한다.
# ---------------------------------------------------------------------------

def _seeded_random(instrument_code: str) -> random.Random:
    seed = int(hashlib.sha256(instrument_code.encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def _dummy_base_price(instrument_code: str) -> float:
    rng = _seeded_random(instrument_code)
    return round(rng.uniform(10000, 100000), -2)  # 1만~10만원, 100원 단위


def _dummy_quote(instrument_code: str) -> Quote:
    base = _dummy_base_price(instrument_code)
    rng = _seeded_random(instrument_code + "-quote")
    price = round(base * rng.uniform(0.97, 1.03), -1)
    day_high = max(price, round(base * rng.uniform(1.0, 1.04), -1))
    return Quote(
        instrument_code=instrument_code,
        price=price,
        day_high=day_high,
        quoted_at=datetime.now().isoformat(timespec="seconds"),
    )


def _dummy_daily_bars(instrument_code: str, start_date: str, end_date: str) -> list[DailyBar]:
    base = _dummy_base_price(instrument_code)
    rng = _seeded_random(instrument_code + "-bars")
    end = date.fromisoformat(end_date) if end_date else date.today()
    n = 20
    bars: list[DailyBar] = []
    price = base
    for i in range(n):
        d = end - timedelta(days=(n - 1 - i))
        drift = rng.uniform(-0.01, 0.01)
        price = max(price * (1 + drift), 100)
        high = price * rng.uniform(1.0, 1.02)
        low = price * rng.uniform(0.98, 1.0)
        close = price
        bars.append(DailyBar(trade_date=d.isoformat(), high=round(high, -1), low=round(low, -1), close=round(close, -1)))
    return bars


_default_client: Optional[KisClient] = None
_default_client_lock = threading.Lock()


def get_client() -> KisClient:
    """프로세스 전역 싱글턴 -- 토큰 캐시를 프로세스 생존 기간 동안 공유한다."""
    global _default_client
    with _default_client_lock:
        if _default_client is None:
            _default_client = KisClient()
        return _default_client
