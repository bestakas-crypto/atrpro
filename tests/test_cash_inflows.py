"""tests/test_cash_inflows.py -- 현금 입금기록(v1.4) 리포지토리 + API 테스트.

withdrawals.py 테스트와 동일한 패턴을 그대로 따른다. 추가로 flow_type
(EXTERNAL/INTERNAL_TRANSFER) 필터링과 cash_flow.py(순입금 요약) 테스트를 더한다
-- analyze.kunoh.top 1단계의 핵심 요구사항(순외부현금흐름 계산)이라 이 부분은
withdrawals 테스트에 없던 새 검증이다.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from atrsite.repositories import cash_flow as cash_flow_repo
from atrsite.repositories import cash_inflows as cash_inflows_repo
from atrsite.repositories import withdrawals as withdrawals_repo


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


def _make_deposit(client, name="월급통장", currency="KRW", amount=0):
    return client.post(
        "/api/v1/deposits", json={"account_name": name, "amount": amount, "currency": currency}
    ).json()


def test_create_cash_inflow_with_registered_account(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 3_000_000, "currency": "KRW",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["account_name_snapshot"] == "월급통장"
    assert body["flow_type"] == "EXTERNAL"  # 기본값
    assert body["is_edited"] is False


def test_create_cash_inflow_rejects_unknown_deposit_account(client):
    resp = client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": "no-such-id",
        "source": "급여", "amount": 1000, "currency": "KRW",
    })
    assert resp.status_code == 400


def test_create_cash_inflow_rejects_zero_and_negative_amount(client):
    dep = _make_deposit(client)
    for bad_amount in (0, -100):
        resp = client.post("/api/v1/cash-inflows", json={
            "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
            "source": "급여", "amount": bad_amount, "currency": "KRW",
        })
        assert resp.status_code == 422


def test_create_cash_inflow_rejects_unsupported_currency(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 1000, "currency": "EUR",
    })
    assert resp.status_code == 400


def test_create_cash_inflow_rejects_invalid_flow_type(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 1000, "currency": "KRW", "flow_type": "SOMETHING_ELSE",
    })
    assert resp.status_code == 400


def test_internal_transfer_flow_type_accepted(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "증권계좌에서 이체", "amount": 500000, "currency": "KRW",
        "flow_type": "INTERNAL_TRANSFER",
    })
    assert resp.status_code == 201
    assert resp.json()["flow_type"] == "INTERNAL_TRANSFER"


def test_list_get_update_delete_cash_inflow(client):
    dep = _make_deposit(client)
    created = client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 3_000_000, "currency": "KRW",
    }).json()

    listed = client.get("/api/v1/cash-inflows").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == created["id"]

    got = client.get(f"/api/v1/cash-inflows/{created['id']}")
    assert got.status_code == 200

    updated = client.patch(f"/api/v1/cash-inflows/{created['id']}", json={"amount": 3_100_000, "memo": "상여 포함"})
    assert updated.status_code == 200
    assert updated.json()["amount"] == pytest.approx(3_100_000)
    assert updated.json()["is_edited"] is True

    deleted = client.delete(f"/api/v1/cash-inflows/{created['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/v1/cash-inflows").json()["total"] == 0


def test_flow_type_filter(client):
    dep = _make_deposit(client)
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 1000, "currency": "KRW", "flow_type": "EXTERNAL",
    })
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "계좌이동", "amount": 2000, "currency": "KRW", "flow_type": "INTERNAL_TRANSFER",
    })
    external = client.get("/api/v1/cash-inflows", params={"flow_type": "EXTERNAL"}).json()
    assert external["total"] == 1
    assert external["items"][0]["source"] == "급여"


def test_cash_inflow_does_not_change_deposit_balance(client):
    """withdrawals와 동일한 v1.1/v1.4 공통 원칙 -- 입금기록은 deposits.amount를
    자동으로 바꾸지 않는다(순수 독립 원장)."""
    dep = _make_deposit(client, amount=1_000_000)
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 500000, "currency": "KRW",
    })
    dep_after = client.get("/api/v1/deposits").json()[0]
    assert dep_after["amount"] == pytest.approx(1_000_000)


def test_check_duplicate_detects_identical_entry(client):
    dep = _make_deposit(client)
    payload = {
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 1000, "currency": "KRW",
    }
    client.post("/api/v1/cash-inflows", json=payload)
    dup = client.post("/api/v1/cash-inflows/check-duplicate", json=payload).json()
    assert dup["duplicate"] is not None
    resp2 = client.post("/api/v1/cash-inflows", json=payload)
    assert resp2.status_code == 201


def test_csv_export_contains_korean_and_amount_no_internal_ids(client):
    dep = _make_deposit(client, name="월급통장")
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 3_000_000, "currency": "KRW", "memo": "8월분",
    })
    resp = client.get("/api/v1/cash-inflows/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.content.decode("utf-8-sig")
    assert "입금일시,입금계좌,출처,금액,통화,구분,메모" in text
    assert "월급통장" in text
    assert "급여" in text
    assert "3000000" in text
    assert dep["id"] not in text


def test_recent_sources_endpoint(client):
    dep = _make_deposit(client)
    for s in ("급여", "급여", "용돈"):
        client.post("/api/v1/cash-inflows", json={
            "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
            "source": s, "amount": 1000, "currency": "KRW",
        })
    resp = client.get("/api/v1/cash-inflows/sources").json()
    assert resp["sources"][0] == "급여"


def test_unauthenticated_request_rejected_when_api_key_configured(tmp_path):
    from atrsite.config import settings
    from atrsite.main import create_app

    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "secret-key-123")
    try:
        app = create_app()
        with TestClient(app) as c:
            resp = c.get("/api/v1/cash-inflows")
            assert resp.status_code == 401
    finally:
        object.__setattr__(settings, "api_key", "")


# ---------------------------------------------------------------------------
# 순외부현금흐름(cash_flow.py) -- analyze.kunoh.top 1단계 핵심 검증.
# ---------------------------------------------------------------------------
def test_net_summary_excludes_internal_transfers(client):
    dep = _make_deposit(client)
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 3_000_000, "currency": "KRW", "flow_type": "EXTERNAL",
    })
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T10:00:00", "deposit_account_id": dep["id"],
        "source": "증권계좌 매도자금", "amount": 1_000_000, "currency": "KRW", "flow_type": "INTERNAL_TRANSFER",
    })
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-12T11:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 500_000, "currency": "KRW", "flow_type": "EXTERNAL",
    })
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-12T12:00:00", "deposit_account_id": dep["id"],
        "purpose": "증권계좌로 이체", "amount": 200_000, "currency": "KRW", "flow_type": "INTERNAL_TRANSFER",
    })

    summary = client.get("/api/v1/cash-flow/net").json()
    # INTERNAL_TRANSFER(입금 100만/출금 20만)는 완전히 제외되고 EXTERNAL만 집계.
    assert summary["KRW"]["inflow"] == pytest.approx(3_000_000)
    assert summary["KRW"]["outflow"] == pytest.approx(500_000)
    assert summary["KRW"]["net"] == pytest.approx(2_500_000)


def test_net_summary_date_range_filter(client):
    dep = _make_deposit(client)
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-07-31T09:00:00", "deposit_account_id": dep["id"],
        "source": "7월 급여", "amount": 1000, "currency": "KRW",
    })
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-01T09:00:00", "deposit_account_id": dep["id"],
        "source": "8월 급여", "amount": 2000, "currency": "KRW",
    })
    summary = client.get("/api/v1/cash-flow/net", params={"start_date": "2026-08-01", "end_date": "2026-08-31"}).json()
    assert summary["KRW"]["inflow"] == pytest.approx(2000)  # 7월 기록은 제외


def test_daily_net_flow_requires_date_range(client):
    resp = client.get("/api/v1/cash-flow/daily")
    assert resp.status_code == 422  # start_date/end_date는 필수 query param


def test_daily_net_flow_merges_inflow_and_outflow_per_day(client):
    dep = _make_deposit(client)
    client.post("/api/v1/cash-inflows", json={
        "deposited_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "source": "급여", "amount": 3_000_000, "currency": "KRW",
    })
    client.post("/api/v1/withdrawals", json={
        "withdrawn_at": "2026-08-12T18:00:00", "deposit_account_id": dep["id"],
        "purpose": "생활비", "amount": 500_000, "currency": "KRW",
    })
    resp = client.get("/api/v1/cash-flow/daily", params={"start_date": "2026-08-12", "end_date": "2026-08-12"}).json()
    assert resp["items"] == [{"date": "2026-08-12", "currency": "KRW", "net_amount": pytest.approx(2_500_000)}]


# ---------------------------------------------------------------------------
# 기간 경계값 -- withdrawals 테스트와 동일한 방식(now 직접 주입).
# ---------------------------------------------------------------------------
def test_today_summary_boundary(db_conn):
    from atrsite.repositories import deposits as deposits_repo
    dep = deposits_repo.create_deposit(db_conn, account_name="테스트 계좌", amount=0, currency="KRW")["id"]
    cash_inflows_repo.create_cash_inflow(
        db_conn, deposited_at="2026-08-05T23:59:59", deposit_account_id=dep,
        source="급여", amount=1000, currency="KRW",
    )
    cash_inflows_repo.create_cash_inflow(
        db_conn, deposited_at="2026-08-06T00:00:00", deposit_account_id=dep,
        source="급여", amount=2000, currency="KRW",
    )
    summary = cash_inflows_repo.period_summary(db_conn, now=datetime(2026, 8, 5, 12, 0, 0))
    assert summary["today"] == {"KRW": pytest.approx(1000)}


def test_migration_adds_flow_type_to_existing_live_cash_withdrawals_table(tmp_path):
    """VPS처럼 이미 cash_withdrawals가 flow_type 없이 운영 중이던 DB를 흉내내서,
    init_db()의 ALTER TABLE 보충 로직(db.py _add_column_if_missing)이 기존 행을
    깨지 않고 flow_type='EXTERNAL'로 채우는지 직접 검증한다."""
    import sqlite3

    from atrsite import db as db_module

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # v1.1 당시의(flow_type 없는) 구버전 cash_withdrawals 스키마를 그대로 재현.
    conn.execute(
        """
        CREATE TABLE cash_withdrawals (
            id TEXT PRIMARY KEY, withdrawn_at TEXT NOT NULL,
            deposit_account_id TEXT, account_name_snapshot TEXT NOT NULL,
            purpose TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'KRW',
            memo TEXT, edited INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO cash_withdrawals VALUES "
        "('w1', '2026-08-05T10:00:00', NULL, '기존계좌', '생활비', 50000, 'KRW', NULL, 0, 'x', 'x')"
    )
    conn.commit()
    conn.close()

    conn = db_module.connect(db_path)
    db_module.init_db(conn)  # 여기서 ALTER TABLE ADD COLUMN flow_type이 실행돼야 함.

    row = conn.execute("SELECT * FROM cash_withdrawals WHERE id = 'w1'").fetchone()
    assert row["flow_type"] == "EXTERNAL"  # 기존 행도 기본값으로 채워짐, 데이터 손실 없음
    assert row["amount"] == 50000  # 기존 데이터는 그대로 보존
    conn.close()


def test_withdrawals_flow_type_defaults_to_external_for_backward_compat(db_conn):
    """기존(v1.1) 출금기록 생성 API는 flow_type을 안 넘겨도 EXTERNAL로 채워져야
    한다 -- v1.4 추가 전 코드/데이터와의 하위호환."""
    from atrsite.repositories import deposits as deposits_repo
    dep = deposits_repo.create_deposit(db_conn, account_name="테스트 계좌", amount=0, currency="KRW")["id"]
    w = withdrawals_repo.create_withdrawal(
        db_conn, withdrawn_at="2026-08-05T10:00:00", deposit_account_id=dep,
        purpose="생활비", amount=1000, currency="KRW",
    )
    assert w["flow_type"] == "EXTERNAL"
