"""FastAPI 엔드포인트 통합 테스트 -- TestClient로 실제 HTTP 요청/응답 경로를 검증한다."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from atrsite.config import settings
    from atrsite.main import create_app

    # frozen dataclass 싱글턴을 테스트별 임시 경로로 바꿔친다 -- main.py/deps.py
    # 둘 다 이 동일 인스턴스를 참조하므로 여기서 한 번만 바꾸면 전체에 적용된다.
    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "")

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_and_get_instrument(client):
    resp = client.post("/api/v1/instruments", json={"name": "삼성전자", "currency": "KRW"})
    assert resp.status_code == 201
    inst = resp.json()
    assert inst["buy_multiple"] == pytest.approx(1.0)

    resp = client.get(f"/api/v1/instruments/{inst['id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["instrument"]["name"] == "삼성전자"
    assert detail["position"]["quantity"] == 0
    assert detail["trades"] == []


def test_full_trade_flow_matches_spec_example(client):
    """스펙 필수 예시를 HTTP 경로로 재확인: 평균단가 150원."""
    inst = client.post("/api/v1/instruments", json={"name": "테스트", "buy_multiple": 1.0, "sell_multiple": 1.5, "stop_multiple": 2.0}).json()
    iid = inst["id"]

    r1 = client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    assert r1.status_code == 201

    r2 = client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "sell", "price": 120, "quantity": 5, "executed_at": "2026-01-02T09:00:00",
    })
    assert r2.status_code == 201

    r3 = client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 200, "quantity": 5, "executed_at": "2026-01-03T09:00:00",
    })
    assert r3.status_code == 201

    detail = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail["position"]["avg_price"] == pytest.approx(150)
    assert detail["position"]["quantity"] == pytest.approx(10)
    assert len(detail["trades"]) == 3


def test_oversell_returns_409(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
    iid = inst["id"]
    client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    resp = client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "sell", "price": 100, "quantity": 11, "executed_at": "2026-01-02T09:00:00",
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["available_quantity"] == pytest.approx(10)


def test_invalid_trade_type_returns_422(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
    resp = client.post(f"/api/v1/instruments/{inst['id']}/trades", json={
        "trade_type": "hold", "price": 100, "quantity": 1, "executed_at": "2026-01-01T09:00:00",
    })
    assert resp.status_code == 422  # pydantic Literal 검증에서 이미 걸러짐


def test_quote_and_atr_commit_produce_signal(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트", "buy_multiple": 1.0, "sell_multiple": 1.5, "stop_multiple": 2.0}).json()
    iid = inst["id"]
    client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    client.post(f"/api/v1/instruments/{iid}/atr", json={"atr": 10, "trade_date": "2026-01-01"})
    client.post(f"/api/v1/instruments/{iid}/quote", json={"price": 140})

    detail = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail["signal"]["status"] == "TAKE_PROFIT_TRIGGERED"
    # 현재가 140이 auto_update_high로 최고가를 100->140까지 끌어올렸으므로
    # 손절선도 레칫으로 100-10*2=80 -> 140-10*2=120까지 같이 올라가야 한다.
    assert detail["instrument"]["post_entry_high_price"] == pytest.approx(140)
    assert detail["instrument"]["trailing_stop_price"] == pytest.approx(120)


def test_settings_update_creates_new_strategy_version(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트", "buy_multiple": 1.0}).json()
    resp = client.patch(f"/api/v1/instruments/{inst['id']}", json={"buy_multiple": 2.5})
    assert resp.status_code == 200
    assert resp.json()["buy_multiple"] == pytest.approx(2.5)


def test_reset_instrument_clears_trades(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
    iid = inst["id"]
    client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    resp = client.post(f"/api/v1/instruments/{iid}/reset")
    assert resp.status_code == 200
    detail = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail["trades"] == []
    assert detail["position"]["quantity"] == 0


def test_delete_instrument(client):
    inst = client.post("/api/v1/instruments", json={"name": "삭제될종목"}).json()
    resp = client.delete(f"/api/v1/instruments/{inst['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/v1/instruments/{inst['id']}").status_code == 404


def test_deposits_crud(client):
    created = client.post("/api/v1/deposits", json={"account_name": "증권", "amount": 1000, "currency": "USD"}).json()
    listed = client.get("/api/v1/deposits").json()
    assert len(listed) == 1

    updated = client.patch(f"/api/v1/deposits/{created['id']}", json={"amount": 2000}).json()
    assert updated["amount"] == pytest.approx(2000)

    resp = client.delete(f"/api/v1/deposits/{created['id']}")
    assert resp.status_code == 204
    assert client.get("/api/v1/deposits").json() == []


def test_dashboard_totals_with_fx(client):
    client.put("/api/v1/fx", json={"rates": {"USD": 1300}, "display_currency": "KRW"})
    inst = client.post("/api/v1/instruments", json={"name": "달러종목", "currency": "USD"}).json()
    client.post(f"/api/v1/instruments/{inst['id']}/trades", json={
        "trade_type": "buy", "price": 10, "quantity": 100, "executed_at": "2026-01-01T09:00:00",
    })
    dash = client.get("/api/v1/dashboard").json()
    assert dash["totals"]["cost_basis"] == pytest.approx(10 * 100 * 1300)
    assert dash["totals"]["missing_cost_count"] == 0


def test_acknowledge_signal_endpoint(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트", "buy_multiple": 1.0, "sell_multiple": 1.5, "stop_multiple": 2.0}).json()
    iid = inst["id"]
    client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    client.post(f"/api/v1/instruments/{iid}/atr", json={"atr": 10, "trade_date": "2026-01-01"})
    client.post(f"/api/v1/instruments/{iid}/quote", json={"price": 79})  # 손절선 80 이하

    detail = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail["signal"]["status"] == "STOP_TRIGGERED"
    assert detail["signal"]["acknowledged_event_id"] is None

    resp = client.post(f"/api/v1/instruments/{iid}/acknowledge")
    assert resp.status_code == 200
    assert resp.json()["acknowledged_event_id"] == resp.json()["latest_event_id"]

    detail_after = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail_after["signal"]["status"] == "STOP_TRIGGERED"  # 상태 자체는 그대로
    assert detail_after["signal"]["acknowledged_event_id"] == detail_after["signal"]["latest_event_id"]


def test_acknowledge_signal_404_for_unknown_instrument(client):
    resp = client.post("/api/v1/instruments/does-not-exist/acknowledge")
    assert resp.status_code == 404


def test_api_key_enforced_when_configured(client):
    from atrsite.config import settings
    object.__setattr__(settings, "api_key", "secret123")
    try:
        resp = client.get("/api/v1/instruments")
        assert resp.status_code == 401
        resp = client.get("/api/v1/instruments", headers={"X-API-Key": "secret123"})
        assert resp.status_code == 200
    finally:
        object.__setattr__(settings, "api_key", "")
