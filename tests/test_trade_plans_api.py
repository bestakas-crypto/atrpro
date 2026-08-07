"""backend/atrsite/api/trade_plans.py 통합 테스트."""
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


def _create_instrument(client, name="QQQ", currency="USD"):
    resp = client.post("/api/v1/instruments", json={"name": name, "currency": currency})
    return resp.json()["id"]


def test_create_and_get_plan(client):
    qqq_id = _create_instrument(client)
    resp = client.post("/api/v1/trade-plans", json={
        "label": "QQQ 기존물량 1차 부분익절",
        "trigger_price": 717.0, "trigger_direction": "ABOVE", "confirm_mode": "CLOSE",
        "price_reference_instrument_id": qqq_id,
        "instruments": [{"instrument_id": qqq_id, "baseline_quantity": 427.0}],
        "tiers": [{"tier_order": 1, "pullback_pct": 1.25, "sell_pct": 40.0}],
        "reason": "717달러 목표구간 도달 후 40% 부분익절",
    })
    assert resp.status_code == 201
    plan = resp.json()
    assert plan["lifecycle_status"] == "ARMED"
    plan_id = plan["id"]

    fetched = client.get(f"/api/v1/trade-plans/{plan_id}")
    assert fetched.status_code == 200
    assert fetched.json()["trigger_price"] == 717.0


def test_create_rejects_non_trail_plan_type(client):
    qqq_id = _create_instrument(client)
    resp = client.post("/api/v1/trade-plans", json={
        "plan_type": "ACCUMULATE",
        "label": "bad", "trigger_price": 100.0, "trigger_direction": "ABOVE",
        "price_reference_instrument_id": qqq_id,
        "instruments": [{"instrument_id": qqq_id, "baseline_quantity": 10.0}],
    })
    assert resp.status_code == 422  # pydantic Literal 검증에서 이미 막힘


def test_create_rejects_tier_sum_over_100(client):
    qqq_id = _create_instrument(client)
    resp = client.post("/api/v1/trade-plans", json={
        "label": "bad", "trigger_price": 100.0, "trigger_direction": "ABOVE",
        "price_reference_instrument_id": qqq_id,
        "instruments": [{"instrument_id": qqq_id, "baseline_quantity": 10.0}],
        "tiers": [
            {"tier_order": 1, "pullback_pct": 1.0, "sell_pct": 60.0},
            {"tier_order": 2, "pullback_pct": 2.0, "sell_pct": 60.0},
        ],
    })
    assert resp.status_code == 422
    assert "100" in resp.json()["detail"]  # 한국어 오류 메시지 그대로 노출


def test_list_filters_by_status(client):
    qqq_id = _create_instrument(client)
    resp = client.post("/api/v1/trade-plans", json={
        "label": "v1", "trigger_price": 717.0, "trigger_direction": "ABOVE",
        "price_reference_instrument_id": qqq_id,
        "instruments": [{"instrument_id": qqq_id, "baseline_quantity": 427.0}],
    })
    plan_id = resp.json()["id"]
    client.delete(f"/api/v1/trade-plans/{plan_id}", params={"reason": "테스트 취소"})

    armed_only = client.get("/api/v1/trade-plans", params={"status": "ARMED"})
    assert plan_id not in [p["id"] for p in armed_only.json()]

    cancelled_only = client.get("/api/v1/trade-plans", params={"status": "CANCELLED"})
    assert plan_id in [p["id"] for p in cancelled_only.json()]


def test_patch_updates_and_records_history(client):
    qqq_id = _create_instrument(client)
    created = client.post("/api/v1/trade-plans", json={
        "label": "v1", "trigger_price": 717.0, "trigger_direction": "ABOVE",
        "price_reference_instrument_id": qqq_id,
        "instruments": [{"instrument_id": qqq_id, "baseline_quantity": 427.0}],
    }).json()

    patched = client.patch(f"/api/v1/trade-plans/{created['id']}", json={
        "change_reason": "실적전망 상향으로 트리거 조정", "trigger_price": 725.0,
    })
    assert patched.status_code == 200
    assert patched.json()["trigger_price"] == 725.0
    assert patched.json()["version"] == 2

    history = client.get(f"/api/v1/trade-plans/{created['id']}/history")
    assert len(history.json()) == 1
    assert history.json()[0]["change_reason"] == "실적전망 상향으로 트리거 조정"


def test_delete_is_cancel_not_hard_delete(client):
    qqq_id = _create_instrument(client)
    created = client.post("/api/v1/trade-plans", json={
        "label": "v1", "trigger_price": 717.0, "trigger_direction": "ABOVE",
        "price_reference_instrument_id": qqq_id,
        "instruments": [{"instrument_id": qqq_id, "baseline_quantity": 427.0}],
    }).json()

    cancel_resp = client.delete(f"/api/v1/trade-plans/{created['id']}", params={"reason": "계획 철회"})
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["lifecycle_status"] == "CANCELLED"

    still_there = client.get(f"/api/v1/trade-plans/{created['id']}")
    assert still_there.status_code == 200
    assert still_there.json()["lifecycle_status"] == "CANCELLED"


def test_get_unknown_plan_returns_404(client):
    resp = client.get("/api/v1/trade-plans/does-not-exist")
    assert resp.status_code == 404


def test_kodex200_multi_instrument_plan_creation(client):
    """실제 확정된 KODEX 200 계획(두 계좌 908+367주)을 그대로 API로 등록."""
    acct_a = _create_instrument(client, name="Kodex 200", currency="KRW")
    acct_b = _create_instrument(client, name="kodex 200 New", currency="KRW")
    resp = client.post("/api/v1/trade-plans", json={
        "label": "KODEX 200 국내시장 종료",
        "trigger_price": 115000.0, "trigger_direction": "ABOVE", "confirm_mode": "CLOSE",
        "price_reference_instrument_id": acct_a,
        "instruments": [
            {"instrument_id": acct_a, "baseline_quantity": 908.0},
            {"instrument_id": acct_b, "baseline_quantity": 367.0},
        ],
        "tiers": [
            {"tier_order": 1, "pullback_pct": 2.5, "sell_pct": 40.0},
            {"tier_order": 2, "pullback_pct": 6.0, "sell_pct": 60.0},
        ],
        "purpose": "국내주식 투자 종료",
    })
    assert resp.status_code == 201
    plan = resp.json()
    assert sum(i["baseline_quantity"] for i in plan["instruments"]) == 1275.0
