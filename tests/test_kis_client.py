"""kis_client 단위 테스트 -- 3단계는 더미 데이터 단계이므로 실제 KIS 서버 호출
없이 검증 가능한 부분(레이트리미터, 토큰 캐시 로직, 더미 데이터 형태)만 다룬다.
"""
import time

import pytest

from atrsite.adapters.kis_client import KisApiError, KisClient, RateLimiter, TokenCache
from atrsite.config import settings
from atrsite.services.atr_engine import compute_wilder_atr


def test_rate_limiter_enforces_minimum_interval():
    limiter = RateLimiter(min_interval_seconds=0.05)
    start = time.monotonic()
    limiter.wait()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - start
    # 두 번의 간격(0.05*2)에 근접해야 함 -- 타이머 오버헤드 때문에 아주 약간
    # 못 미칠 수 있어 느슨한 허용오차(10%)를 둔다.
    assert elapsed >= 0.1 * 0.9


def test_dummy_quote_is_deterministic_per_instrument_code():
    client = KisClient()
    try:
        q1 = client.get_current_price("005930")
        q2 = client.get_current_price("005930")
        q3 = client.get_current_price("000660")
        assert q1.price == q2.price
        assert q1.day_high == q2.day_high
        assert q1.price != q3.price  # 다른 종목코드는 다른 더미값
        assert q1.day_high >= q1.price
    finally:
        client.close()


def test_dummy_daily_bars_have_enough_length_for_atr14():
    client = KisClient()
    try:
        bars = client.get_daily_bars("005930", "2026-06-01", "2026-08-01")
        assert len(bars) >= 15  # atr_engine이 period+1(=15)개를 요구함
        points = compute_wilder_atr(bars, period=14)
        assert len(points) >= 1
        assert points[-1].atr > 0
    finally:
        client.close()


@pytest.fixture()
def restore_kis_credentials():
    """settings는 frozen dataclass라 monkeypatch.setattr을 못 쓴다 -- object.__setattr__로
    직접 바꾸고 테스트 후 원상복구한다."""
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    yield
    object.__setattr__(settings, "kis_app_key", original_key)
    object.__setattr__(settings, "kis_app_secret", original_secret)


def test_token_cache_raises_when_credentials_missing(restore_kis_credentials):
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    cache = TokenCache()

    class DummyHttp:
        def post(self, *a, **kw):
            raise AssertionError("자격증명이 없으면 실제 요청을 시도하면 안 됨")

    with pytest.raises(KisApiError):
        cache.get(DummyHttp(), "https://example.invalid")


def test_token_cache_caches_token_across_calls(restore_kis_credentials):
    object.__setattr__(settings, "kis_app_key", "dummy-key")
    object.__setattr__(settings, "kis_app_secret", "dummy-secret")
    cache = TokenCache()

    call_count = {"n": 0}

    class DummyResponse:
        def raise_for_status(self):
            pass

        def json(self):
            call_count["n"] += 1
            return {"access_token": f"token-{call_count['n']}", "expires_in": 86400}

    class DummyHttp:
        def post(self, *a, **kw):
            return DummyResponse()

    http = DummyHttp()
    token1 = cache.get(http, "https://example.invalid")
    token2 = cache.get(http, "https://example.invalid")
    assert token1 == token2 == "token-1"  # 두 번째 호출은 캐시를 재사용해야 함
    assert call_count["n"] == 1
