"""backend/atrsite/api/schedules.py 통합 테스트 -- TestClient로 실제 HTTP 경로 검증."""
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


@pytest.fixture()
def instrument_a(client):
    resp = client.post("/api/v1/instruments", json={"name": "TIGER KOFR금리액티브", "currency": "KRW", "kis_code": "449170", "is_etf": True})
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture()
def instrument_b(client):
    resp = client.post("/api/v1/instruments", json={"name": "KODEX iShares 미국하이일드액티브", "currency": "KRW", "kis_code": "468380", "is_etf": True})
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture()
def deposit_account(client):
    resp = client.post("/api/v1/deposits", json={"account_name": "ISA계좌", "amount": 0, "currency": "KRW"})
    assert resp.status_code == 201
    return resp.json()["id"]


# ---- investment-plans -----------------------------------------------------------

def test_create_and_get_plan(client, instrument_a, deposit_account):
    resp = client.post(
        "/api/v1/investment-plans",
        json={
            "name": "ISA 적립 플랜", "deposit_account_id": deposit_account,
            "total_amount": 1000000, "currency": "KRW",
            "items": [{"instrument_id": instrument_a, "ratio_percent": 100}],
        },
    )
    assert resp.status_code == 201
    plan = resp.json()
    assert plan["account_name_snapshot"] == "ISA계좌"
    assert plan["status"] == "ACTIVE"
    assert len(plan["items"]) == 1
    assert plan["items"][0]["instrument_name_snapshot"] == "TIGER KOFR금리액티브"

    resp = client.get(f"/api/v1/investment-plans/{plan['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "ISA 적립 플랜"


def test_plan_unknown_deposit_account_rejected(client):
    resp = client.post("/api/v1/investment-plans", json={"name": "x", "deposit_account_id": "no-such-id"})
    assert resp.status_code == 400


def test_update_and_delete_plan(client):
    resp = client.post("/api/v1/investment-plans", json={"name": "임시 플랜"})
    plan_id = resp.json()["id"]

    resp = client.patch(f"/api/v1/investment-plans/{plan_id}", json={"status": "PAUSED"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "PAUSED"

    resp = client.delete(f"/api/v1/investment-plans/{plan_id}")
    assert resp.status_code == 204
    assert client.get(f"/api/v1/investment-plans/{plan_id}").status_code == 404


# ---- investment-schedules + occurrences -----------------------------------------

def test_create_weekly_schedule_generates_occurrences_and_items(client, instrument_a, instrument_b):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={
            "schedule_type": "BUY", "title": "ISA 주간 적립", "market": "KRX",
            "recurrence_type": "WEEKLY", "recurrence_interval": 1,
            "start_date": "2026-08-10", "occurrence_count": 9,
            "holiday_policy": "NEXT_BUSINESS_DAY", "total_amount": 74918796, "currency": "KRW",
            "items": [
                {"instrument_id": instrument_a, "ratio_percent": 50},
                {"instrument_id": instrument_b, "ratio_percent": 50},
            ],
        },
    )
    assert resp.status_code == 201
    sched = resp.json()
    assert len(sched["occurrences"]) == 9
    assert sched["occurrences"][0]["occurrence_index"] == 1

    amounts = [o["planned_amount"] for o in sched["occurrences"]]
    assert amounts[:8] == [8324310] * 8
    assert amounts[8] == 8324316
    assert sum(amounts) == 74918796

    occ_id = sched["occurrences"][0]["id"]
    resp = client.get(f"/api/v1/schedule-occurrences/{occ_id}")
    assert resp.status_code == 200
    occ = resp.json()
    assert len(occ["items"]) == 2
    assert {i["planned_amount"] for i in occ["items"]} == {4162155}


def test_schedule_requires_valid_schedule_type(client):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "NOT_A_TYPE", "title": "x", "recurrence_type": "ONCE", "start_date": "2026-08-10"},
    )
    assert resp.status_code == 400


def test_once_schedule_single_occurrence(client):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "GENERAL", "title": "만기 확인", "recurrence_type": "ONCE", "start_date": "2026-09-01"},
    )
    assert resp.status_code == 201
    sched = resp.json()
    assert len(sched["occurrences"]) == 1
    assert sched["occurrences"][0]["adjusted_scheduled_date"] == "2026-09-01"


def test_non_krx_market_flags_confirmation(client):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={
            "schedule_type": "DIVIDEND_CHECK", "title": "미국 배당 확인", "market": "NYSE_NASDAQ",
            "recurrence_type": "ONCE", "start_date": "2026-09-01",
        },
    )
    assert resp.status_code == 201
    occ = resp.json()["occurrences"][0]
    assert occ["needs_holiday_confirmation"] is True


def test_cancelling_schedule_cancels_future_occurrences(client):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "GENERAL", "title": "정기 점검", "recurrence_type": "WEEKLY", "start_date": "2026-08-10", "occurrence_count": 3},
    )
    schedule_id = resp.json()["id"]

    resp = client.patch(f"/api/v1/investment-schedules/{schedule_id}", json={"status": "CANCELLED"})
    assert resp.status_code == 200
    sched = resp.json()
    assert all(o["status"] == "CANCELLED" for o in sched["occurrences"])


def test_list_occurrences_filter_by_schedule(client):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "GENERAL", "title": "A", "recurrence_type": "ONCE", "start_date": "2026-08-10"},
    )
    sid = resp.json()["id"]
    resp = client.get("/api/v1/schedule-occurrences", params={"schedule_id": sid})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


# ---- badge-count / today ---------------------------------------------------------

def test_badge_count_excludes_future_scheduled(client):
    # 미래 날짜(먼 훗날) -- 아직 배지에 안 잡혀야 함
    client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "GENERAL", "title": "먼미래", "recurrence_type": "ONCE", "start_date": "2099-01-01", "market": "NONE"},
    )
    resp = client.get("/api/v1/schedule-occurrences/badge-count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_badge_count_includes_overdue(client):
    client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "GENERAL", "title": "지난 일정", "recurrence_type": "ONCE", "start_date": "2020-01-01", "market": "NONE"},
    )
    resp = client.get("/api/v1/schedule-occurrences/badge-count")
    assert resp.json()["count"] == 1


def test_badge_count_display_caps_at_9_plus(client):
    for i in range(11):
        client.post(
            "/api/v1/investment-schedules",
            json={"schedule_type": "GENERAL", "title": f"지난 일정{i}", "recurrence_type": "ONCE", "start_date": "2020-01-01", "market": "NONE"},
        )
    resp = client.get("/api/v1/schedule-occurrences/badge-count")
    body = resp.json()
    assert body["count"] == 11
    assert body["display"] == "9+"


def test_acknowledged_notified_excluded_from_badge(client):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "GENERAL", "title": "확인용", "recurrence_type": "ONCE", "start_date": "2020-01-01", "market": "NONE"},
    )
    occ_id = resp.json()["occurrences"][0]["id"]
    client.patch(f"/api/v1/schedule-occurrences/{occ_id}", json={"status": "NOTIFIED"})
    assert client.get("/api/v1/schedule-occurrences/badge-count").json()["count"] == 1

    client.patch(f"/api/v1/schedule-occurrences/{occ_id}", json={"acknowledged": True})
    assert client.get("/api/v1/schedule-occurrences/badge-count").json()["count"] == 0


def test_today_endpoint_returns_overdue_section(client):
    client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "GENERAL", "title": "지난 일정", "recurrence_type": "ONCE", "start_date": "2020-01-01", "market": "NONE"},
    )
    resp = client.get("/api/v1/schedule-occurrences/today")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["overdue"]) == 1
    assert body["today"] == []


# ---- executions (계획 vs 실제 분리) -----------------------------------------------

def test_execution_links_to_existing_withdrawal(client, deposit_account):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "WITHDRAWAL", "title": "생활비 출금", "recurrence_type": "ONCE", "start_date": "2026-08-10", "total_amount": 500000},
    )
    occ_id = resp.json()["occurrences"][0]["id"]

    resp = client.post(
        "/api/v1/withdrawals",
        json={"withdrawn_at": "2026-08-10T10:00:00", "deposit_account_id": deposit_account, "purpose": "생활비", "amount": 500000, "currency": "KRW"},
    )
    withdrawal_id = resp.json()["id"]

    resp = client.post(
        f"/api/v1/schedule-occurrences/{occ_id}/executions",
        json={"execution_type": "WITHDRAWAL", "linked_withdrawal_id": withdrawal_id, "executed_amount": 500000},
    )
    assert resp.status_code == 201
    occ = resp.json()
    assert len(occ["executions"]) == 1
    assert occ["executions"][0]["linked_withdrawal_id"] == withdrawal_id
    # 실행 기록을 남겨도 occurrence의 status는 자동으로 안 바뀐다(계획 vs 실제 분리 --
    # 완료 처리는 사용자가 별도로 PATCH status=COMPLETED 해야 함).
    assert occ["status"] == "SCHEDULED"


def test_execution_rejects_unknown_withdrawal_id(client):
    resp = client.post(
        "/api/v1/investment-schedules",
        json={"schedule_type": "WITHDRAWAL", "title": "x", "recurrence_type": "ONCE", "start_date": "2026-08-10"},
    )
    occ_id = resp.json()["occurrences"][0]["id"]
    resp = client.post(
        f"/api/v1/schedule-occurrences/{occ_id}/executions",
        json={"execution_type": "WITHDRAWAL", "linked_withdrawal_id": "no-such-id"},
    )
    assert resp.status_code == 400


# ---- 실제 ISA 플랜 전체 시나리오(v1.2 스펙 명시 검증용) ---------------------------

def test_full_isa_plan_scenario_via_api(client, instrument_a, instrument_b, deposit_account):
    resp = client.post(
        "/api/v1/investment-plans",
        json={
            "name": "ISA 9회 분할매수", "deposit_account_id": deposit_account,
            "total_amount": 74918796, "currency": "KRW",
            "items": [
                {"instrument_id": instrument_a, "ratio_percent": 50},
                {"instrument_id": instrument_b, "ratio_percent": 50},
            ],
        },
    )
    assert resp.status_code == 201
    plan_id = resp.json()["id"]

    resp = client.post(
        "/api/v1/investment-schedules",
        json={
            "plan_id": plan_id, "schedule_type": "BUY", "title": "ISA 9회 분할매수 일정",
            "market": "KRX", "recurrence_type": "WEEKLY", "recurrence_interval": 1,
            "start_date": "2026-08-10", "occurrence_count": 9, "holiday_policy": "NEXT_BUSINESS_DAY",
            "total_amount": 74918796, "currency": "KRW",
            "items": [
                {"instrument_id": instrument_a, "ratio_percent": 50},
                {"instrument_id": instrument_b, "ratio_percent": 50},
            ],
        },
    )
    assert resp.status_code == 201
    sched = resp.json()
    assert sched["plan_id"] == plan_id
    assert len(sched["occurrences"]) == 9
    assert sum(o["planned_amount"] for o in sched["occurrences"]) == 74918796

    total_item_amount = 0
    for occ in sched["occurrences"]:
        detail = client.get(f"/api/v1/schedule-occurrences/{occ['id']}").json()
        assert len(detail["items"]) == 2
        total_item_amount += sum(i["planned_amount"] for i in detail["items"])
    assert total_item_amount == 74918796
