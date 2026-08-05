"""backend/atrsite/api/snapshots.py + repositories/snapshots.py 통합 테스트."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from atrsite.config import settings
    from atrsite.main import create_app

    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "")

    app = create_app()
    with TestClient(app) as c:
        yield c


def _seed_holding(client):
    resp = client.post("/api/v1/instruments", json={"name": "테스트종목", "currency": "KRW"})
    inst_id = resp.json()["id"]
    client.post(f"/api/v1/instruments/{inst_id}/trades", json={
        "trade_type": "buy", "price": 1000, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    client.post(f"/api/v1/instruments/{inst_id}/quote", json={"price": 1200})
    return inst_id


def test_run_creates_snapshot(client):
    _seed_holding(client)
    resp = client.post("/api/v1/portfolio-snapshots/run")
    assert resp.status_code == 201
    body = resp.json()
    assert body["snapshot_date"] == date.today().isoformat()
    assert body["data_quality_status"] == "MANUAL"
    assert body["total_asset_value"] == pytest.approx(12000)


def test_run_is_idempotent_key(client):
    """같은 날짜에 두 번 POST /run을 호출해도 스냅샷 행이 하나만 존재해야 한다."""
    _seed_holding(client)
    client.post("/api/v1/portfolio-snapshots/run")
    client.post("/api/v1/portfolio-snapshots/run")
    resp = client.get("/api/v1/portfolio-snapshots", params={"start_date": date.today().isoformat()})
    assert len(resp.json()["items"]) == 1


def test_latest_returns_most_recent(client):
    _seed_holding(client)
    client.post("/api/v1/portfolio-snapshots/run")
    resp = client.get("/api/v1/portfolio-snapshots/latest")
    assert resp.status_code == 200
    assert resp.json()["snapshot_date"] == date.today().isoformat()


def test_latest_404_when_empty(client):
    resp = client.get("/api/v1/portfolio-snapshots/latest")
    assert resp.status_code == 404


def test_get_by_date_and_items(client):
    inst_id = _seed_holding(client)
    client.post("/api/v1/portfolio-snapshots/run")
    today = date.today().isoformat()

    resp = client.get(f"/api/v1/portfolio-snapshots/{today}")
    assert resp.status_code == 200

    resp = client.get(f"/api/v1/portfolio-snapshots/{today}/items")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["instrument_id"] == inst_id for i in items)


def test_get_by_date_404_when_missing(client):
    resp = client.get("/api/v1/portfolio-snapshots/2020-01-01")
    assert resp.status_code == 404


def test_recalculate_rejects_past_date(client):
    _seed_holding(client)
    past = (date.today() - timedelta(days=1)).isoformat()
    resp = client.post(f"/api/v1/portfolio-snapshots/{past}/recalculate")
    assert resp.status_code == 400
    assert "복원할 수 없습니다" in resp.json()["detail"]


def test_recalculate_today_allowed(client):
    _seed_holding(client)
    client.post("/api/v1/portfolio-snapshots/run")
    today = date.today().isoformat()
    resp = client.post(f"/api/v1/portfolio-snapshots/{today}/recalculate")
    assert resp.status_code == 200


def test_chart_period_filters(client):
    _seed_holding(client)
    client.post("/api/v1/portfolio-snapshots/run")

    resp = client.get("/api/v1/portfolio-snapshots/chart", params={"period": "1m"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    resp = client.get("/api/v1/portfolio-snapshots/chart", params={"period": "all"})
    assert len(resp.json()["items"]) == 1


def test_chart_rejects_unsupported_period(client):
    resp = client.get("/api/v1/portfolio-snapshots/chart", params={"period": "10y"})
    assert resp.status_code == 400


def test_chart_custom_date_range(client):
    _seed_holding(client)
    client.post("/api/v1/portfolio-snapshots/run")
    today = date.today().isoformat()
    resp = client.get("/api/v1/portfolio-snapshots/chart", params={"start_date": today, "end_date": today})
    assert len(resp.json()["items"]) == 1
    resp = client.get("/api/v1/portfolio-snapshots/chart", params={"start_date": "2000-01-01", "end_date": "2000-01-02"})
    assert resp.json()["items"] == []


def test_endpoints_require_api_key(client, monkeypatch):
    from atrsite.config import settings
    object.__setattr__(settings, "api_key", "secret-key")
    try:
        resp = client.get("/api/v1/portfolio-snapshots/latest")
        assert resp.status_code == 401
    finally:
        object.__setattr__(settings, "api_key", "")
