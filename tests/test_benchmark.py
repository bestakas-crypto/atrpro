"""tests/test_benchmark.py -- 벤치마크 지수(코스피/S&P500) API,
analyze.kunoh.top 4단계(2026-08-12 추가).

kis_client.py는 KIS_APP_KEY/SECRET이 없으면 더미 모드로 결정론적 값을
반환하므로(test_kis_client.py와 동일한 원칙), 여기서도 실제 KIS 서버 없이
API 계층(파라미터 검증/응답 포맷/에러 변환)만 검증한다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path):
    from atrsite.config import settings
    from atrsite.main import create_app

    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "")
    # KIS 자격증명도 비워서 kis_client.py가 더미 모드로 동작하게 한다
    # (실제 KIS 서버 호출 없이 결정론적 값으로 테스트).
    original_kis_key, original_kis_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")

    app = create_app()
    with TestClient(app) as c:
        yield c

    object.__setattr__(settings, "kis_app_key", original_kis_key)
    object.__setattr__(settings, "kis_app_secret", original_kis_secret)


def test_kospi_endpoint_returns_items(client):
    resp = client.get("/api/v1/benchmark/kospi", params={"start_date": "2026-08-01", "end_date": "2026-08-12"})
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert len(body["items"]) > 0
    for item in body["items"]:
        assert "date" in item and "close" in item
        assert item["date"] >= "2026-08-01"
        assert item["date"] <= "2026-08-12"


def test_sp500_endpoint_returns_items(client):
    resp = client.get("/api/v1/benchmark/sp500", params={"start_date": "2026-08-01", "end_date": "2026-08-12"})
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert len(body["items"]) > 0


def test_kospi_endpoint_requires_date_params(client):
    resp = client.get("/api/v1/benchmark/kospi", params={"start_date": "2026-08-01"})
    assert resp.status_code == 422


def test_kospi_endpoint_rejects_malformed_date(client):
    resp = client.get("/api/v1/benchmark/kospi", params={"start_date": "2026/08/01", "end_date": "2026-08-12"})
    assert resp.status_code == 422


def test_benchmark_results_are_deterministic_across_calls(client):
    """더미 모드는 종목코드로 시드를 고정하므로, 같은 기간을 두 번 요청하면
    동일한 값이 나와야 한다(추후 실제 KIS 응답으로 바뀌어도 이 API 계약
    자체는 안정적이어야 함을 확인)."""
    resp1 = client.get("/api/v1/benchmark/kospi", params={"start_date": "2026-08-01", "end_date": "2026-08-12"}).json()
    resp2 = client.get("/api/v1/benchmark/kospi", params={"start_date": "2026-08-01", "end_date": "2026-08-12"}).json()
    assert resp1 == resp2


def test_unauthenticated_request_rejected_when_api_key_configured(tmp_path):
    from atrsite.config import settings
    from atrsite.main import create_app

    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "secret-key-123")
    try:
        app = create_app()
        with TestClient(app) as c:
            resp = c.get("/api/v1/benchmark/kospi", params={"start_date": "2026-08-01", "end_date": "2026-08-12"})
            assert resp.status_code == 401
    finally:
        object.__setattr__(settings, "api_key", "")
