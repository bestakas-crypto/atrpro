"""tests/test_withdrawals.py -- 현금 출금기록(v1.1) 리포지토리 + API 테스트.

API 레벨(TestClient)과 리포지토리 레벨(db_conn, 기간 경계값처럼 now를 직접
주입해야 하는 케이스)을 섞어서 쓴다 -- period_summary(now=...)가 now를
주입 가능하게 만들어둔 게 바로 이 이유(월말/연말 경계를 결정론적으로 검증).
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from atrsite.repositories import withdrawals as withdrawals_repo
from atrsite.repositories.withdrawals import WithdrawalFilter, WithdrawalValidationError


# ---------------------------------------------------------------------------
# API 레벨 테스트 (TestClient) -- test_api.py의 client 픽스처와 동일한 패턴.
# ---------------------------------------------------------------------------
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


def _make_deposit(client, name="생활비 통장", currency="KRW", amount=1_000_000):
    return client.post(
        "/api/v1/deposits", json={"account_name": name, "amount": amount, "currency": currency}
    ).json()


def test_create_withdrawal_with_registered_account(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 50000, "currency": "KRW",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["account_name_snapshot"] == "생활비 통장"  # 계좌 선택 -> 스냅샷 자동 채움
    assert body["is_edited"] is False


def test_create_withdrawal_rejects_unknown_deposit_account(client):
    resp = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": "no-such-id",
        "purpose": "생활비", "amount": 50000, "currency": "KRW",
    })
    assert resp.status_code == 400


def test_create_withdrawal_rejects_when_no_deposit_accounts_exist(client):
    """스펙 4.4/4.5 -- 등록된 예금계좌가 하나도 없으면 저장 자체가 거부돼야 함
    (프론트에서 안내문을 보여주는 건 별개로, 서버도 반드시 막아야 함)."""
    resp = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": "anything",
        "purpose": "생활비", "amount": 1000, "currency": "KRW",
    })
    assert resp.status_code == 400


def test_create_withdrawal_rejects_zero_and_negative_amount(client):
    dep = _make_deposit(client)
    for bad_amount in (0, -100):
        resp = client.post("/api/v1/withdrawals", json={
            "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
            "purpose": "생활비", "amount": bad_amount, "currency": "KRW",
        })
        assert resp.status_code == 422  # Pydantic Field(gt=0)에서 먼저 걸림


def test_create_withdrawal_rejects_blank_purpose(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "   ", "amount": 1000, "currency": "KRW",
    })
    assert resp.status_code == 400


def test_create_withdrawal_rejects_unsupported_currency(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 1000, "currency": "EUR",
    })
    assert resp.status_code == 400


def test_list_get_update_delete_withdrawal(client):
    dep = _make_deposit(client)
    created = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 50000, "currency": "KRW",
    }).json()

    listed = client.get("/api/v1/withdrawals").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == created["id"]

    got = client.get(f"/api/v1/withdrawals/{created['id']}")
    assert got.status_code == 200

    updated = client.patch(f"/api/v1/withdrawals/{created['id']}", json={"amount": 60000, "memo": "택시비 포함"})
    assert updated.status_code == 200
    assert updated.json()["amount"] == pytest.approx(60000)
    assert updated.json()["is_edited"] is True  # updated_at != created_at (스펙 9.5)

    deleted = client.delete(f"/api/v1/withdrawals/{created['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/v1/withdrawals").json()["total"] == 0


def test_purpose_search_korean_partial_match(client):
    dep = _make_deposit(client)
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비 이체", "amount": 1000, "currency": "KRW",
    })
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "병원비", "amount": 2000, "currency": "KRW",
    })
    resp = client.get("/api/v1/withdrawals", params={"purpose": "생활"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["purpose"] == "생활비 이체"


def test_purpose_search_english_case_insensitive(client):
    dep = _make_deposit(client)
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "Car Maintenance", "amount": 1000, "currency": "KRW",
    })
    resp = client.get("/api/v1/withdrawals", params={"purpose": "car"})
    assert resp.json()["total"] == 1
    resp2 = client.get("/api/v1/withdrawals", params={"purpose": "CAR"})
    assert resp2.json()["total"] == 1


def test_search_result_totals_match_filtered_full_set_not_just_page(client):
    """스펙 5.2/7 -- 목록의 페이지(limit)와 무관하게, 필터 전체 합계가 나와야 함."""
    dep = _make_deposit(client)
    for i in range(5):
        client.post("/api/v1/withdrawals", json={
            "withdrawn_at": f"2026-08-0{i+1}T10:00:00", "deposit_account_id": dep["id"],
            "purpose": "생활비", "amount": 10000, "currency": "KRW",
        })
    resp = client.get("/api/v1/withdrawals", params={"purpose": "생활비", "limit": 2}).json()
    assert len(resp["items"]) == 2  # 페이지는 2건만
    assert resp["total"] == 5
    assert resp["sum_by_currency"]["KRW"] == pytest.approx(50000)  # 합계는 전체 5건 기준


def test_currencies_not_mixed_in_sum(client):
    dep_krw = _make_deposit(client, name="원화계좌", currency="KRW")
    dep_usd = _make_deposit(client, name="달러계좌", currency="USD")
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep_krw["id"],
        "purpose": "생활비", "amount": 100000, "currency": "KRW",
    })
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep_usd["id"],
        "purpose": "여행", "amount": 200, "currency": "USD",
    })
    body = client.get("/api/v1/withdrawals").json()
    assert body["sum_by_currency"] == {"KRW": pytest.approx(100000), "USD": pytest.approx(200)}


def test_account_level_totals(client):
    dep = _make_deposit(client, name="생활비 통장")
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 30000, "currency": "KRW",
    })
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "세금", "amount": 20000, "currency": "KRW",
    })
    body = client.get("/api/v1/withdrawals").json()
    assert len(body["sum_by_account"]) == 1
    assert body["sum_by_account"][0]["total"] == pytest.approx(50000)
    assert body["sum_by_account"][0]["count"] == 2


def test_withdrawal_does_not_change_deposit_balance(client):
    """스펙 10 -- 가장 중요한 비연동 원칙. 출금기록 등록/삭제 후에도
    deposits.amount는 절대 자동 변경되지 않아야 한다."""
    dep = _make_deposit(client, amount=1_000_000)
    withdrawal = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 500000, "currency": "KRW",
    }).json()

    dep_after_create = client.get("/api/v1/deposits").json()[0]
    assert dep_after_create["amount"] == pytest.approx(1_000_000)  # 변화 없음

    client.delete(f"/api/v1/withdrawals/{withdrawal['id']}")
    dep_after_delete = client.get("/api/v1/deposits").json()[0]
    assert dep_after_delete["amount"] == pytest.approx(1_000_000)  # 삭제해도 여전히 변화 없음


def test_deleting_withdrawal_does_not_delete_deposit_account(client):
    dep = _make_deposit(client)
    withdrawal = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 1000, "currency": "KRW",
    }).json()
    client.delete(f"/api/v1/withdrawals/{withdrawal['id']}")
    assert len(client.get("/api/v1/deposits").json()) == 1  # 계좌는 그대로


def test_deleted_deposit_account_keeps_snapshot_on_past_withdrawal(client):
    """스펙 4 권장 정책 -- 연결 계좌가 삭제돼도 출금기록 자체와
    account_name_snapshot은 남아야 함."""
    dep = _make_deposit(client, name="곧 삭제될 계좌")
    withdrawal = client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 1000, "currency": "KRW",
    }).json()

    client.delete(f"/api/v1/deposits/{dep['id']}")

    still_there = client.get(f"/api/v1/withdrawals/{withdrawal['id']}")
    assert still_there.status_code == 200
    assert still_there.json()["account_name_snapshot"] == "곧 삭제될 계좌"
    assert still_there.json()["deposit_account_id"] is None  # FK ON DELETE SET NULL


def test_unauthenticated_request_rejected_when_api_key_configured(tmp_path):
    """스펙 13 -- API 키가 설정된 배포 환경에서는 인증 없는 요청을 거부해야 함."""
    from atrsite.config import settings
    from atrsite.main import create_app

    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "secret-key-123")
    try:
        app = create_app()
        with TestClient(app) as c:
            resp = c.get("/api/v1/withdrawals")
            assert resp.status_code == 401
    finally:
        object.__setattr__(settings, "api_key", "")


def test_csv_export_contains_korean_and_amount_no_internal_ids(client):
    dep = _make_deposit(client, name="생활비 통장")
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "병원비", "amount": 35000, "currency": "KRW", "memo": "감기약",
    })
    resp = client.get("/api/v1/withdrawals/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.content.decode("utf-8-sig")  # BOM 포함 여부까지 같이 검증
    assert "출금일시,출금계좌,용도,금액,통화,메모" in text
    assert "생활비 통장" in text
    assert "병원비" in text
    assert "35000" in text
    assert dep["id"] not in text  # 내부 ID 미노출(스펙 9.3/13)


def test_recent_purposes_endpoint(client):
    dep = _make_deposit(client)
    for p in ("생활비", "생활비", "세금"):
        client.post("/api/v1/withdrawals", json={
            "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
            "purpose": p, "amount": 1000, "currency": "KRW",
        })
    resp = client.get("/api/v1/withdrawals/purposes").json()
    assert resp["purposes"][0] == "생활비"  # 더 자주 쓴 용도가 먼저


def test_check_duplicate_detects_identical_entry(client):
    dep = _make_deposit(client)
    payload = {
        "withdrawn_at": "2026-08-05T10:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 1000, "currency": "KRW",
    }
    client.post("/api/v1/withdrawals", json=payload)
    dup = client.post("/api/v1/withdrawals/check-duplicate", json=payload).json()
    assert dup["duplicate"] is not None
    # 저장 자체를 막지는 않음(스펙 9.2) -- 한 번 더 저장해도 성공해야 함
    resp2 = client.post("/api/v1/withdrawals", json=payload)
    assert resp2.status_code == 201


# ---------------------------------------------------------------------------
# 기간 경계값 -- period_summary(now=...)로 now를 직접 주입해 결정론적으로 검증.
# ---------------------------------------------------------------------------
def test_today_summary_boundary(db_conn):
    withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2026-08-05T23:59:59", deposit_account_id=_seed_deposit(db_conn),
        purpose="생활비", amount=1000, currency="KRW",
    )
    withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2026-08-06T00:00:00", deposit_account_id=_seed_deposit(db_conn, "다른계좌"),
        purpose="생활비", amount=2000, currency="KRW",
    )
    summary = withdrawals_repo.period_summary(db_conn, now=datetime(2026, 8, 5, 12, 0, 0))
    assert summary["today"] == {"KRW": pytest.approx(1000)}  # 8/6 00:00 기록은 "오늘"(8/5)에 안 걸림


def test_this_week_starts_monday(db_conn):
    dep = _seed_deposit(db_conn)
    # 2026-08-03(월)이 그 주의 시작 -- 2026-08-02(일)는 지난주.
    withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2026-08-02T23:00:00", deposit_account_id=dep,
        purpose="생활비", amount=100, currency="KRW",
    )
    withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2026-08-03T00:00:00", deposit_account_id=dep,
        purpose="생활비", amount=200, currency="KRW",
    )
    summary = withdrawals_repo.period_summary(db_conn, now=datetime(2026, 8, 5, 12, 0, 0))  # 8/5(수)
    assert summary["this_week"] == {"KRW": pytest.approx(200)}


def test_month_end_boundary(db_conn):
    dep = _seed_deposit(db_conn)
    withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2026-07-31T23:59:59", deposit_account_id=dep,
        purpose="생활비", amount=100, currency="KRW",
    )
    withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2026-08-01T00:00:00", deposit_account_id=dep,
        purpose="생활비", amount=200, currency="KRW",
    )
    summary = withdrawals_repo.period_summary(db_conn, now=datetime(2026, 8, 5, 12, 0, 0))
    assert summary["this_month"] == {"KRW": pytest.approx(200)}  # 7/31 기록은 이번 달(8월)에 안 걸림


def test_year_end_boundary_ytd(db_conn):
    dep = _seed_deposit(db_conn)
    withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2025-12-31T23:59:59", deposit_account_id=dep,
        purpose="생활비", amount=100, currency="KRW",
    )
    withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2026-01-01T00:00:00", deposit_account_id=dep,
        purpose="생활비", amount=200, currency="KRW",
    )
    summary = withdrawals_repo.period_summary(db_conn, now=datetime(2026, 8, 5, 12, 0, 0))
    assert summary["ytd"] == {"KRW": pytest.approx(200)}  # 작년 말 기록은 올해 YTD에 안 걸림


def _seed_deposit(conn, name="테스트 계좌") -> str:
    from atrsite.repositories import deposits as deposits_repo
    return deposits_repo.create_deposit(conn, account_name=name, amount=0, currency="KRW")["id"]
