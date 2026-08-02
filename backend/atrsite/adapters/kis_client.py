"""한국투자증권(KIS) Open API 클라이언트 -- 스펙 11절.

인증/토큰 처리(TokenCache)와 요청 패턴(_headers, rt_cd 검사)은
C:\\mmean\\core\\kis_data_api.py의 검증된 방식을 strpro 스타일로 다시 작성한
것이다 (그대로 복사하지 않음 -- 두 프로젝트는 계속 독립적으로 유지).

시세 조회 TR(get_current_price/get_daily_bars)은 C:\\strpro\\manual의 실제
명세("주식현재가 시세[v1_국내주식-008]", "국내주식기간별시세(일_주_월_년)
[v1_국내주식-016]")를 그대로 따라 구현했고, 2026-08-02 사용자의 실전 계좌로
실제 KIS 서버에 대고 토큰 발급 + 시세 조회까지 확인함.

telegram_client.py와 동일한 더미 모드 패턴: KIS_APP_KEY/KIS_APP_SECRET이
.env에 없으면(로컬 개발/테스트 기본값) 실제 호출 대신 결정론적 더미 데이터를
반환한다 -- pytest가 실제 KIS 서버나 유효한 자격증명 없이도 항상 통과해야
하기 때문이다.

해외주식(예: 나스닥 QQQ) 시세 TR("해외주식 현재가상세[v1_해외주식-029]",
"해외주식 기간별시세[v1_해외주식-010]")도 동일한 방식으로 지원한다 --
manual 폴더에는 해외주식 명세 파일이 없어서, KIS 공식 GitHub
(koreainvestment/open-trading-api)의 예제 코드로 TR ID/파라미터/응답
필드명을 확인하고, 실전 계좌로 QQQ를 직접 조회해 값이 정상인지 검증했다
(2026-08-02). instrument.kis_market이 비어있거나 "KRX"면 기존 국내 TR을,
그 외(NAS/NYS/AMS 등 거래소코드)면 해외 TR을 쓴다.
"""
from __future__ import annotations

import hashlib
import random
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from ..config import settings
from ..services.atr_engine import DailyBar

KIS_BASE_LIVE = "https://openapi.koreainvestment.com:9443"
KIS_BASE_VIRTUAL = "https://openapivts.koreainvestment.com:29443"
OAUTH_TOKEN_PATH = "/oauth2/tokenP"

# C:\strpro\manual의 TR 명세 문서로 확인된 값.
TR_CURRENT_PRICE = "FHKST01010100"  # 주식현재가 시세 [v1_국내주식-008]
EP_CURRENT_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
TR_DAILY_CHART_PRICE = "FHKST03010100"  # 국내주식기간별시세(일_주_월_년) [v1_국내주식-016]
EP_DAILY_CHART_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

# FID_COND_MRKT_DIV_CODE -- "J:KRX, NX:NXT, UN:통합" (명세 그대로). 국내 상장
# 종목은 KRX 기준으로 조회한다.
FID_MARKET_DIV_KRX = "J"

# 해외주식 현재가상세 [v1_해외주식-029] / 기간별시세 [v1_해외주식-010].
# KIS 공식 예제(examples_llm/overseas_stock)로 확인, 2026-08-02 QQQ로 실전 검증.
TR_OVERSEAS_PRICE_DETAIL = "HHDFS76200200"
EP_OVERSEAS_PRICE_DETAIL = "/uapi/overseas-price/v1/quotations/price-detail"
TR_OVERSEAS_DAILY_PRICE = "HHDFS76240000"
EP_OVERSEAS_DAILY_PRICE = "/uapi/overseas-price/v1/quotations/dailyprice"

# instrument.kis_market이 비어있거나 이 값이면 국내(KRX) TR을 쓴다. 그 외
# 문자열(NAS/NYS/AMS 등 KIS 거래소코드)은 그대로 EXCD 파라미터로 넘긴다.
DOMESTIC_MARKET = "KRX"


def _is_domestic(market: Optional[str]) -> bool:
    return not market or market.upper() == DOMESTIC_MARKET


class KisApiError(RuntimeError):
    """rt_cd != '0' 등 KIS API가 명시적으로 실패를 반환했을 때 발생."""


def is_dummy_mode() -> bool:
    """텔레그램 클라이언트와 동일한 원칙 -- 자격증명이 없으면 실제 호출 대신
    더미 데이터를 쓴다(테스트/로컬 개발이 실제 KIS 서버에 의존하지 않게)."""
    return not settings.kis_app_key or not settings.kis_app_secret


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

        try:
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
        except httpx.HTTPError as exc:
            # httpx가 던지는 원시 예외(HTTPStatusError/타임아웃/연결 실패 등)를
            # 여기서 KisApiError로 바꿔놔야 worker.py/portfolio_service.py의
            # "except KisApiError"가 실제로 잡는다 -- 안 그러면 토큰 발급이
            # 잠깐 막혔을 때(예: 짧은 시간에 재발급 시도가 몰려 403) 원시
            # 예외가 그대로 새어나가 worker 루프 전체가 죽는다(2026-08-02
            # 실사용 중 실제로 403으로 재현됨).
            raise KisApiError(f"토큰 발급 실패: {exc}") from exc
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

    def _get_json(self, url: str, *, headers: dict[str, str], params: dict[str, str]) -> dict:
        """GET + raise_for_status + json()을 한 곳에서 처리하고, httpx가 던지는
        원시 예외(HTTP 4xx/5xx, 타임아웃, 연결 실패 등)를 전부 KisApiError로
        바꿔준다 -- TokenCache.get()과 같은 이유(2026-08-02 실사용 중 403으로
        실제 재현됨: worker.py/portfolio_service.py는 KisApiError만 잡으므로
        원시 예외가 새어나가면 worker 루프가 죽거나 API가 500을 낸다)."""
        try:
            res = self._http.get(url, headers=headers, params=params, timeout=10)
            res.raise_for_status()
        except httpx.HTTPError as exc:
            raise KisApiError(f"KIS 요청 실패 url={url}: {exc}") from exc
        return res.json()

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

    def get_current_price(self, instrument_code: str, market: Optional[str] = None) -> Quote:
        """market이 국내(KRX)면 주식현재가 시세[v1_국내주식-008], 그 외(NAS 등
        해외 거래소코드)면 해외주식 현재가상세[v1_해외주식-029]를 쓴다."""
        self.rate_limiter.wait()
        if is_dummy_mode():
            return _dummy_quote(instrument_code)

        if _is_domestic(market):
            return self._get_domestic_price(instrument_code)
        return self._get_overseas_price(instrument_code, market)

    def _get_domestic_price(self, instrument_code: str) -> Quote:
        """주식현재가 시세 [v1_국내주식-008] (TR FHKST01010100).

        manual 폴더 명세 그대로: 요청은 FID_COND_MRKT_DIV_CODE(J)+FID_INPUT_ISCD,
        응답 output.stck_prpr(현재가)/output.stck_hgpr(당일 최고가)를 쓴다.
        """
        body = self._get_json(
            self._base_url() + EP_CURRENT_PRICE,
            headers=self._headers(TR_CURRENT_PRICE),
            params={"FID_COND_MRKT_DIV_CODE": FID_MARKET_DIV_KRX, "FID_INPUT_ISCD": instrument_code},
        )
        if body.get("rt_cd") != "0":
            raise KisApiError(
                f"현재가 조회 실패 code={instrument_code} rt_cd={body.get('rt_cd')} msg={body.get('msg1', '')}"
            )
        output = body.get("output") or {}
        try:
            price = float(output["stck_prpr"])
            day_high = float(output["stck_hgpr"])
        except (KeyError, ValueError) as exc:
            raise KisApiError(f"현재가 응답 형식 이상 code={instrument_code}: {output}") from exc

        # 이 TR은 국내(KRX) 종목 전용이다. QQQ 같은 해외 티커나 존재하지 않는
        # 코드를 넣어도 KIS는 rt_cd="0"(정상)에 stck_prpr="0"을 돌려줄 뿐 에러를
        # 주지 않는다 -- 그대로 두면 가격 0을 진짜 시세로 저장해버리므로 여기서
        # 명시적으로 걸러낸다.
        if price <= 0:
            raise KisApiError(
                f"현재가 조회 결과가 비정상입니다(code={instrument_code}): "
                "국내(KRX) 상장 종목 코드가 맞는지 확인하세요. 해외 종목이면 "
                "종목 설정에서 거래소(시장)를 올바르게 선택했는지 확인하세요."
            )

        return Quote(
            instrument_code=instrument_code,
            price=price,
            day_high=day_high,
            quoted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _get_overseas_price(self, instrument_code: str, market: str) -> Quote:
        """해외주식 현재가상세 [v1_해외주식-029] (TR HHDFS76200200).

        KIS 공식 예제(examples_llm/overseas_stock/price_detail)로 확인:
        요청 AUTH(빈값)+EXCD(거래소코드)+SYMB(종목코드), 응답
        output.last(현재가)/output.high(당일 고가)를 쓴다. 2026-08-02 QQQ/NAS로
        실전 계좌에서 직접 검증(687.99 USD 등 정상값 확인).
        """
        body = self._get_json(
            self._base_url() + EP_OVERSEAS_PRICE_DETAIL,
            headers=self._headers(TR_OVERSEAS_PRICE_DETAIL),
            params={"AUTH": "", "EXCD": market, "SYMB": instrument_code},
        )
        if body.get("rt_cd") != "0":
            raise KisApiError(
                f"해외 현재가 조회 실패 code={instrument_code} market={market} "
                f"rt_cd={body.get('rt_cd')} msg={body.get('msg1', '')}"
            )
        output = body.get("output") or {}
        try:
            price = float(output["last"])
            day_high = float(output["high"])
        except (KeyError, ValueError) as exc:
            raise KisApiError(f"해외 현재가 응답 형식 이상 code={instrument_code}: {output}") from exc

        if price <= 0:
            raise KisApiError(
                f"해외 현재가 조회 결과가 비정상입니다(code={instrument_code}, market={market}): "
                "거래소코드/종목코드가 맞는지 확인하세요."
            )

        return Quote(
            instrument_code=instrument_code,
            price=price,
            day_high=day_high,
            quoted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def get_daily_bars(
        self, instrument_code: str, start_date: str, end_date: str, market: Optional[str] = None,
    ) -> list[DailyBar]:
        """market이 국내(KRX)면 국내주식기간별시세[v1_국내주식-016], 그 외면
        해외주식 기간별시세[v1_해외주식-010]를 쓴다."""
        self.rate_limiter.wait()
        if is_dummy_mode():
            return _dummy_daily_bars(instrument_code, start_date, end_date)

        if _is_domestic(market):
            return self._get_domestic_daily_bars(instrument_code, start_date, end_date)
        return self._get_overseas_daily_bars(instrument_code, end_date, market)

    def _get_domestic_daily_bars(self, instrument_code: str, start_date: str, end_date: str) -> list[DailyBar]:
        """국내주식기간별시세(일_주_월_년) [v1_국내주식-016] (TR FHKST03010100).

        manual 폴더 명세 그대로: FID_PERIOD_DIV_CODE=D(일봉), FID_ORG_ADJ_PRC=0
        (수정주가), FID_INPUT_DATE_1/2는 YYYYMMDD(하이픈 없음). 응답 output2가
        확정 일봉 배열(한 번에 최대 100건, 최신순으로 온다고 명세에 나와 있어
        날짜 오름차순으로 다시 정렬한다 -- atr_engine이 과거->최신 순서를
        전제로 하기 때문).
        """
        body = self._get_json(
            self._base_url() + EP_DAILY_CHART_PRICE,
            headers=self._headers(TR_DAILY_CHART_PRICE),
            params={
                "FID_COND_MRKT_DIV_CODE": FID_MARKET_DIV_KRX,
                "FID_INPUT_ISCD": instrument_code,
                "FID_INPUT_DATE_1": start_date.replace("-", ""),
                "FID_INPUT_DATE_2": end_date.replace("-", ""),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        if body.get("rt_cd") != "0":
            raise KisApiError(
                f"일봉 조회 실패 code={instrument_code} rt_cd={body.get('rt_cd')} msg={body.get('msg1', '')}"
            )

        bars: list[DailyBar] = []
        for row in body.get("output2") or []:
            raw_date = row.get("stck_bsop_date")
            if not raw_date:
                continue  # 명세상 영업일 없는 빈 행이 섞여 나올 수 있음
            try:
                close = float(row["stck_clpr"])
                high = float(row["stck_hgpr"])
                low = float(row["stck_lwpr"])
            except (KeyError, ValueError) as exc:
                raise KisApiError(f"일봉 응답 형식 이상 code={instrument_code}: {row}") from exc
            if close <= 0:
                # get_current_price와 동일한 이유 -- 국내 TR이 알 수 없는
                # 코드에도 0값 행을 "정상"으로 줄 수 있다.
                raise KisApiError(
                    f"일봉 조회 결과가 비정상입니다(code={instrument_code}): "
                    "국내(KRX) 상장 종목 코드가 맞는지 확인하세요."
                )
            bars.append(DailyBar(
                trade_date=f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
                high=high,
                low=low,
                close=close,
            ))

        bars.sort(key=lambda b: b.trade_date)
        return bars

    def _get_overseas_daily_bars(self, instrument_code: str, end_date: str, market: str) -> list[DailyBar]:
        """해외주식 기간별시세 [v1_해외주식-010] (TR HHDFS76240000).

        국내 TR과 달리 날짜 범위가 아니라 기준일(BYMD) 하나만 받고 거기서부터
        과거로 최대 100건을 돌려준다 -- start_date는 쓰지 않는다(ATR 계산에
        필요한 15개보다 훨씬 많이 오므로 문제 없음). 2026-08-02 QQQ/NAS로
        실전 계좌에서 직접 검증(100건, 최신순으로 옴 -> 오름차순 재정렬 필요,
        국내 TR과 동일).

        MODP(수정주가반영여부)는 국내 TR의 FID_ORG_ADJ_PRC와 값 의미가 반대다
        -- 국내는 "0=수정주가/1=원주가", 해외는 "0=미반영/1=반영"(KIS 공식
        예제 코드 기준). 액면분할/배당락으로 과거 봉이 흔들려 ATR이 왜곡되는
        걸 막으려면 반드시 "1"(수정주가 반영)이어야 한다.
        """
        body = self._get_json(
            self._base_url() + EP_OVERSEAS_DAILY_PRICE,
            headers=self._headers(TR_OVERSEAS_DAILY_PRICE),
            params={
                "AUTH": "", "EXCD": market, "SYMB": instrument_code,
                "GUBN": "0", "BYMD": end_date.replace("-", ""), "MODP": "1",
            },
        )
        if body.get("rt_cd") != "0":
            raise KisApiError(
                f"해외 일봉 조회 실패 code={instrument_code} market={market} "
                f"rt_cd={body.get('rt_cd')} msg={body.get('msg1', '')}"
            )

        bars: list[DailyBar] = []
        for row in body.get("output2") or []:
            raw_date = row.get("xymd")
            if not raw_date:
                continue
            try:
                close = float(row["clos"])
                high = float(row["high"])
                low = float(row["low"])
            except (KeyError, ValueError) as exc:
                raise KisApiError(f"해외 일봉 응답 형식 이상 code={instrument_code}: {row}") from exc
            if close <= 0:
                raise KisApiError(
                    f"해외 일봉 조회 결과가 비정상입니다(code={instrument_code}, market={market}): "
                    "거래소코드/종목코드가 맞는지 확인하세요."
                )
            bars.append(DailyBar(
                trade_date=f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
                high=high,
                low=low,
                close=close,
            ))

        bars.sort(key=lambda b: b.trade_date)
        return bars

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
        # UTC-aware로 통일 (repositories/market_data.py의 utils.utcnow_iso()와
        # 동일한 형식이어야 신선도 경과시간 계산이 타임존 불일치로 틀어지지 않는다).
        quoted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
