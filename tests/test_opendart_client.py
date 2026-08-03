# tests/test_opendart_client.py
# opendart_client.py -- 키가 없는 현재 상태(더미 모드)만 검증한다. 실제
# API 응답 파싱은 키를 받아 라이브 검증하기 전까지 테스트하지 않는다
# (미검증 추측 코드를 "테스트 통과"로 위장하지 않기 위함).
import pytest

from atrsite.adapters import opendart_client
from atrsite.config import settings


@pytest.fixture()
def no_opendart_key():
    original = settings.opendart_api_key
    object.__setattr__(settings, "opendart_api_key", "")
    yield
    object.__setattr__(settings, "opendart_api_key", original)


def test_is_configured_false_without_key(no_opendart_key):
    assert opendart_client.is_configured() is False


def test_search_companies_returns_empty_without_key(no_opendart_key):
    assert opendart_client.search_companies("삼성전자") == []


def test_fetch_financial_statements_raises_without_key(no_opendart_key):
    with pytest.raises(opendart_client.OpenDartError):
        opendart_client.fetch_financial_statements("00126380", bsns_year="2025", reprt_code="11011")
