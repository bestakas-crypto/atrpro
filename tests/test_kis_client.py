"""kis_client 단위 테스트.

이 저장소를 체크아웃한 다른 환경(또는 CI)에는 실제 KIS 자격증명이 없으므로,
더미 모드 테스트는 항상 credentials를 명시적으로 비워서 강제한다 -- 지금
이 개발 환경의 .env에는 실제 계좌 키가 들어 있어서, 그냥 두면 아래 테스트가
실제 KIS 서버에 접속을 시도하게 된다(느리고, 계정별 호출 한도를 깎아먹고,
실시간 시세라 결정론적 테스트가 깨진다).

실제 요청/응답 파싱 로직(get_current_price/get_daily_bars의 필드 매핑)은
네트워크 호출 없이 가짜 HTTP 응답으로 검증한다 -- manual 폴더 명세의 Example
섹션 구조를 그대로 흉내낸다.
"""
import time

import pytest

from atrsite.adapters.kis_client import KisApiError, KisClient, RateLimiter, TokenCache
from atrsite.config import settings
from atrsite.services.atr_engine import compute_wilder_atr


@pytest.fixture()
def restore_kis_credentials():
    """settings는 frozen dataclass라 monkeypatch.setattr을 못 쓴다 -- object.__setattr__로
    직접 바꾸고 테스트 후 원상복구한다."""
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    yield
    object.__setattr__(settings, "kis_app_key", original_key)
    object.__setattr__(settings, "kis_app_secret", original_secret)


@pytest.fixture()
def dummy_mode(restore_kis_credentials):
    """자격증명을 강제로 비워 더미 모드를 켠다."""
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")


@pytest.fixture()
def fake_credentials(restore_kis_credentials):
    """더미 모드를 끄되(자격증명이 "있는 것처럼") 실제 HTTP는 몽키패치로 가로챈다."""
    object.__setattr__(settings, "kis_app_key", "dummy-key")
    object.__setattr__(settings, "kis_app_secret", "dummy-secret")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _client_with_fake_transport(get_payload):
    client = KisClient()
    client._token_cache.get = lambda http, base_url: "fake-token"  # 네트워크로 토큰 발급 시도 안 함
    client._http.get = lambda *a, **kw: _FakeResponse(get_payload)
    return client


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


def test_dummy_quote_is_deterministic_per_instrument_code(dummy_mode):
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


def test_dummy_daily_bars_have_enough_length_for_atr14(dummy_mode):
    client = KisClient()
    try:
        bars = client.get_daily_bars("005930", "2026-06-01", "2026-08-01")
        assert len(bars) >= 15  # atr_engine이 period+1(=15)개를 요구함
        points = compute_wilder_atr(bars, period=14)
        assert len(points) >= 1
        assert points[-1].atr > 0
    finally:
        client.close()


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


# ---------------------------------------------------------------------------
# 실제 요청/응답 파싱 로직 -- manual 폴더 명세의 Example 구조를 그대로 흉내낸
# 가짜 HTTP 응답으로, 네트워크 없이 필드 매핑만 검증한다 (2026-08-02 추가).
# ---------------------------------------------------------------------------

def test_get_current_price_parses_real_response_shape(fake_credentials):
    """[v1_국내주식-008] 명세의 Response Example 구조 그대로."""
    client = _client_with_fake_transport({
        "rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다!",
        "output": {"stck_prpr": "128500", "stck_hgpr": "130000", "stck_lwpr": "128500"},
    })
    try:
        quote = client.get_current_price("000660")
        assert quote.price == 128500.0
        assert quote.day_high == 130000.0
        assert quote.instrument_code == "000660"
    finally:
        client.close()


def test_get_current_price_raises_kis_api_error_on_rt_cd_failure(fake_credentials):
    client = _client_with_fake_transport({
        "rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다.",
    })
    try:
        with pytest.raises(KisApiError):
            client.get_current_price("000660")
    finally:
        client.close()


def test_get_current_price_raises_kis_api_error_on_missing_fields(fake_credentials):
    client = _client_with_fake_transport({"rt_cd": "0", "output": {"stck_prpr": "128500"}})  # stck_hgpr 없음
    try:
        with pytest.raises(KisApiError):
            client.get_current_price("000660")
    finally:
        client.close()


def test_get_daily_bars_parses_and_sorts_ascending(fake_credentials):
    """[v1_국내주식-016] 명세: output2가 최신순으로 와도 오름차순으로 재정렬해야
    atr_engine의 과거->최신 전제와 맞는다."""
    client = _client_with_fake_transport({
        "rt_cd": "0",
        "output2": [
            {"stck_bsop_date": "20260502", "stck_hgpr": "113000", "stck_lwpr": "111000", "stck_clpr": "112000"},
            {"stck_bsop_date": "20260501", "stck_hgpr": "109000", "stck_lwpr": "106500", "stck_clpr": "107500"},
        ],
    })
    try:
        bars = client.get_daily_bars("000660", "2026-05-01", "2026-05-02")
        assert [b.trade_date for b in bars] == ["2026-05-01", "2026-05-02"]
        assert bars[0].close == 107500.0
        assert bars[1].high == 113000.0
    finally:
        client.close()


def test_get_daily_bars_skips_blank_rows(fake_credentials):
    client = _client_with_fake_transport({
        "rt_cd": "0",
        "output2": [
            {"stck_bsop_date": "", "stck_hgpr": "", "stck_lwpr": "", "stck_clpr": ""},
            {"stck_bsop_date": "20260501", "stck_hgpr": "109000", "stck_lwpr": "106500", "stck_clpr": "107500"},
        ],
    })
    try:
        bars = client.get_daily_bars("000660", "2026-05-01", "2026-05-02")
        assert len(bars) == 1
    finally:
        client.close()
