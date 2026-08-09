import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path):
    from atrsite.config import settings
    from atrsite.main import create_app

    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "")

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_realized_pnl_api_returns_empty_result(client):
    resp = client.get("/api/v1/realized-pnl", params={"start_date": "2026-08-01", "end_date": "2026-08-09"})

    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["base_currency"] == "KRW"


def test_realized_pnl_api_rejects_reversed_dates(client):
    resp = client.get("/api/v1/realized-pnl", params={"start_date": "2026-08-09", "end_date": "2026-08-01"})

    assert resp.status_code == 400
    assert "start_date" in resp.json()["detail"]


def test_realized_pnl_api_rejects_bad_date_format_as_400(client):
    resp = client.get("/api/v1/realized-pnl", params={"start_date": "2026/08/01", "end_date": "2026-08-09"})

    assert resp.status_code == 400
    assert "YYYY-MM-DD" in resp.json()["detail"]
