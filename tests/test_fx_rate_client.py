"""fx_rate_client.py 테스트 -- 네트워크 없이 필드 매핑/실패 처리만 검증.

2026-08-06 실제로 frankfurter.dev를 직접 호출해서 정상 동작(USD/KRW 1423.72,
JPY/KRW 9.0343) 확인했음 -- 여기서는 그 형태를 흉내낸 가짜 응답으로 회귀만
방지한다.
"""
import httpx
import pytest

from atrsite.adapters.fx_rate_client import fetch_rate_to_krw


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_krw_shortcut_never_calls_network(monkeypatch):
    def fake_get(self, url, params=None, timeout=None):
        raise AssertionError("KRW는 네트워크 호출 없이 1.0을 바로 반환해야 함")

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert fetch_rate_to_krw("KRW") == 1.0


def test_fetch_rate_parses_usd_krw(monkeypatch):
    def fake_get(self, url, params=None, timeout=None):
        assert params == {"base": "USD", "symbols": "KRW"}
        return _FakeResponse({"amount": 1.0, "base": "USD", "date": "2026-08-05", "rates": {"KRW": 1423.72}})

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert fetch_rate_to_krw("USD") == pytest.approx(1423.72)


def test_fetch_rate_parses_jpy_krw(monkeypatch):
    def fake_get(self, url, params=None, timeout=None):
        return _FakeResponse({"amount": 1.0, "base": "JPY", "date": "2026-08-05", "rates": {"KRW": 9.0343}})

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert fetch_rate_to_krw("JPY") == pytest.approx(9.0343)


def test_fetch_rate_returns_none_on_missing_krw_field(monkeypatch):
    def fake_get(self, url, params=None, timeout=None):
        return _FakeResponse({"amount": 1.0, "base": "USD", "date": "2026-08-05", "rates": {}})

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert fetch_rate_to_krw("USD") is None


def test_fetch_rate_returns_none_on_network_error(monkeypatch):
    def fake_get(self, url, params=None, timeout=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert fetch_rate_to_krw("USD") is None


def test_fetch_rate_returns_none_on_http_error(monkeypatch):
    class _ErrResponse(_FakeResponse):
        def raise_for_status(self):
            raise httpx.HTTPStatusError("500", request=None, response=None)

    def fake_get(self, url, params=None, timeout=None):
        return _ErrResponse({})

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert fetch_rate_to_krw("USD") is None
