"""market_index_client.py 테스트 -- 네트워크 없이 필드 매핑/실패 처리만 검증.

2026-08-03 실전으로 VXN을 직접 조회해서 정상 동작(26.0, 전일대비 -8.4%)을
확인했음 -- 여기서는 그 형태를 흉내낸 가짜 응답으로 회귀만 방지한다.
"""
import httpx
import pytest

from atrsite.adapters.market_index_client import fetch_index


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_index_parses_value_and_change_pct(monkeypatch):
    def fake_get(self, url, params=None, headers=None, timeout=None):
        return _FakeResponse({
            "chart": {"result": [{
                "meta": {
                    "regularMarketPrice": 26.0,
                    "previousClose": 28.39,
                    "regularMarketTime": 1785528901,
                },
            }]},
        })

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    q = fetch_index("^VXN", "VXN")
    assert q.status == "OK"
    assert q.value == 26.0
    assert q.prev_close == 28.39
    assert q.change_pct == pytest.approx((26.0 - 28.39) / 28.39 * 100)
    assert q.quote_time is not None


def test_fetch_index_returns_unavailable_on_missing_price(monkeypatch):
    def fake_get(self, url, params=None, headers=None, timeout=None):
        return _FakeResponse({"chart": {"result": [{"meta": {}}]}})

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    q = fetch_index("^VXN", "VXN")
    assert q.status == "UNAVAILABLE"
    assert q.value is None


def test_fetch_index_returns_unavailable_on_network_error(monkeypatch):
    def fake_get(self, url, params=None, headers=None, timeout=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    q = fetch_index("^VXN", "VXN")
    assert q.status == "UNAVAILABLE"
