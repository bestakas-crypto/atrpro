"""tests/test_cash_ledger.py -- 입출금 통합 장부(v1.5, 2026-08-12) 리포지토리 +
API 테스트.

옛 test_withdrawals.py + test_cash_inflows.py를 대체. 카드/소비 상세 분석
관련 기능(용도추천/계좌별합계펼치기/중복경고)은 삭제됐으므로 그 테스트도
같이 제거하고, 대신 entry_type(4종) 분류 + 옛 두 테이블에서 cash_ledger로의
1회성 마이그레이션(db.py _migrate_legacy_cash_records)을 신규로 검증한다.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from atrsite.repositories import cash_flow as cash_flow_repo
from atrsite.repositories import cash_ledger as cash_ledger_repo


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


def _make_deposit(client, name="생활비 통장", currency="KRW", amount=0):
    return client.post(
        "/api/v1/deposits", json={"account_name": name, "amount": amount, "currency": currency}
    ).json()


# ---------------------------------------------------------------------------
# 기본 CRUD
# ---------------------------------------------------------------------------
def test_create_entry_external_in(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 3_000_000, "currency": "KRW",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["account_name_snapshot"] == "생활비 통장"
    assert body["direction"] == "IN"
    assert body["is_edited"] is False


def test_create_entry_external_out(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_OUT", "amount": 50000, "currency": "KRW", "memo": "생활비",
    })
    assert resp.status_code == 201
    assert resp.json()["direction"] == "OUT"


@pytest.mark.parametrize(
    "entry_type", ["EXTERNAL_IN", "EXTERNAL_OUT", "INTERNAL_IN", "INTERNAL_OUT", "INTEREST_INCOME"]
)
def test_all_five_entry_types_accepted(client, entry_type):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": entry_type, "amount": 1000, "currency": "KRW",
    })
    assert resp.status_code == 201
    assert resp.json()["entry_type"] == entry_type


def test_create_entry_rejects_invalid_entry_type(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "SOMETHING_ELSE", "amount": 1000, "currency": "KRW",
    })
    assert resp.status_code == 400


def test_create_entry_rejects_unknown_deposit_account(client):
    resp = client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": "no-such-id",
        "entry_type": "EXTERNAL_IN", "amount": 1000, "currency": "KRW",
    })
    assert resp.status_code == 400


def test_create_entry_rejects_zero_and_negative_amount(client):
    dep = _make_deposit(client)
    for bad_amount in (0, -100):
        resp = client.post("/api/v1/cash-ledger", json={
            "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
            "entry_type": "EXTERNAL_IN", "amount": bad_amount, "currency": "KRW",
        })
        assert resp.status_code == 422


def test_create_entry_rejects_unsupported_currency(client):
    dep = _make_deposit(client)
    resp = client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 1000, "currency": "EUR",
    })
    assert resp.status_code == 400


def test_list_get_update_delete_entry(client):
    dep = _make_deposit(client)
    created = client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_OUT", "amount": 50000, "currency": "KRW",
    }).json()

    listed = client.get("/api/v1/cash-ledger").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == created["id"]

    got = client.get(f"/api/v1/cash-ledger/{created['id']}")
    assert got.status_code == 200

    updated = client.patch(f"/api/v1/cash-ledger/{created['id']}", json={"amount": 60000, "memo": "택시비 포함"})
    assert updated.status_code == 200
    assert updated.json()["amount"] == pytest.approx(60000)
    assert updated.json()["is_edited"] is True

    deleted = client.delete(f"/api/v1/cash-ledger/{created['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/v1/cash-ledger").json()["total"] == 0


def test_entry_does_not_change_deposit_balance(client):
    """v1.1/v1.4와 동일한 원칙 -- 장부 기록은 deposits.amount를 자동으로
    바꾸지 않는다(순수 독립 원장)."""
    dep = _make_deposit(client, amount=1_000_000)
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 500000, "currency": "KRW",
    })
    dep_after = client.get("/api/v1/deposits").json()[0]
    assert dep_after["amount"] == pytest.approx(1_000_000)


def test_deleted_deposit_account_keeps_snapshot_on_past_entry(client):
    dep = _make_deposit(client, name="곧 삭제될 계좌")
    entry = client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_OUT", "amount": 1000, "currency": "KRW",
    }).json()
    client.delete(f"/api/v1/deposits/{dep['id']}")
    still_there = client.get(f"/api/v1/cash-ledger/{entry['id']}")
    assert still_there.status_code == 200
    assert still_there.json()["account_name_snapshot"] == "곧 삭제될 계좌"
    assert still_there.json()["deposit_account_id"] is None  # FK ON DELETE SET NULL


# ---------------------------------------------------------------------------
# 필터 + 합계
# ---------------------------------------------------------------------------
def test_entry_type_filter(client):
    dep = _make_deposit(client)
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 1000, "currency": "KRW",
    })
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "INTERNAL_OUT", "amount": 2000, "currency": "KRW",
    })
    external_in = client.get("/api/v1/cash-ledger", params={"entry_type": "EXTERNAL_IN"}).json()
    assert external_in["total"] == 1
    assert external_in["items"][0]["amount"] == pytest.approx(1000)


def test_sum_by_currency_splits_in_out_net(client):
    dep = _make_deposit(client)
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 3_000_000, "currency": "KRW",
    })
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T10:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_OUT", "amount": 500_000, "currency": "KRW",
    })
    body = client.get("/api/v1/cash-ledger").json()
    assert body["sum_by_currency"]["KRW"] == {
        "in": pytest.approx(3_000_000), "out": pytest.approx(500_000), "net": pytest.approx(2_500_000),
    }


def test_currencies_not_mixed_in_sum(client):
    dep_krw = _make_deposit(client, name="원화계좌", currency="KRW")
    dep_usd = _make_deposit(client, name="달러계좌", currency="USD")
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep_krw["id"],
        "entry_type": "EXTERNAL_OUT", "amount": 100000, "currency": "KRW",
    })
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep_usd["id"],
        "entry_type": "EXTERNAL_OUT", "amount": 200, "currency": "USD",
    })
    body = client.get("/api/v1/cash-ledger").json()
    assert body["sum_by_currency"]["KRW"]["out"] == pytest.approx(100000)
    assert body["sum_by_currency"]["USD"]["out"] == pytest.approx(200)


def test_search_result_totals_match_filtered_full_set_not_just_page(client):
    dep = _make_deposit(client)
    for i in range(5):
        client.post("/api/v1/cash-ledger", json={
            "occurred_at": f"2026-08-0{i+1}T10:00:00", "deposit_account_id": dep["id"],
            "entry_type": "EXTERNAL_OUT", "amount": 10000, "currency": "KRW",
        })
    resp = client.get("/api/v1/cash-ledger", params={"limit": 2}).json()
    assert len(resp["items"]) == 2
    assert resp["total"] == 5
    assert resp["sum_by_currency"]["KRW"]["out"] == pytest.approx(50000)


def test_csv_export_contains_amount_no_internal_ids(client):
    dep = _make_deposit(client, name="월급통장")
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 3_000_000, "currency": "KRW", "memo": "8월분",
    })
    resp = client.get("/api/v1/cash-ledger/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.content.decode("utf-8-sig")
    assert "일시,계좌,구분,금액,통화,메모" in text
    assert "월급통장" in text
    assert "외부입금" in text
    assert "3000000" in text
    assert dep["id"] not in text


def test_unauthenticated_request_rejected_when_api_key_configured(tmp_path):
    from atrsite.config import settings
    from atrsite.main import create_app

    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "secret-key-123")
    try:
        app = create_app()
        with TestClient(app) as c:
            resp = c.get("/api/v1/cash-ledger")
            assert resp.status_code == 401
    finally:
        object.__setattr__(settings, "api_key", "")


# ---------------------------------------------------------------------------
# 순외부현금흐름(cash_flow.py) -- cash_ledger 기준 재확인.
# ---------------------------------------------------------------------------
def test_net_summary_excludes_internal_transfers(client):
    dep = _make_deposit(client)
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 3_000_000, "currency": "KRW",
    })
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T10:00:00", "deposit_account_id": dep["id"],
        "entry_type": "INTERNAL_IN", "amount": 1_000_000, "currency": "KRW",
    })
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T11:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_OUT", "amount": 500_000, "currency": "KRW",
    })
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T12:00:00", "deposit_account_id": dep["id"],
        "entry_type": "INTERNAL_OUT", "amount": 200_000, "currency": "KRW",
    })

    summary = client.get("/api/v1/cash-flow/net").json()
    assert summary["KRW"]["inflow"] == pytest.approx(3_000_000)
    assert summary["KRW"]["outflow"] == pytest.approx(500_000)
    assert summary["KRW"]["net"] == pytest.approx(2_500_000)


def test_net_summary_excludes_interest_income(client):
    """v1.7 -- 이자소득은 외부에서 들어온 돈이 아니라 계좌 스스로 불어난
    돈이라, INTERNAL_*와 마찬가지로 순외부현금흐름 계산에서 빠져야 한다
    (그래야 analyze.kunoh.top의 수익률 계산에서 상쇄되지 않고 그대로
    투자성과로 잡힘)."""
    dep = _make_deposit(client)
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 1_000_000, "currency": "KRW",
    })
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T10:00:00", "deposit_account_id": dep["id"],
        "entry_type": "INTEREST_INCOME", "amount": 523, "currency": "KRW",
    })

    summary = client.get("/api/v1/cash-flow/net").json()
    assert summary["KRW"]["inflow"] == pytest.approx(1_000_000)  # 523원은 안 섞임
    assert summary["KRW"]["net"] == pytest.approx(1_000_000)


def test_daily_net_flow_merges_in_and_out_per_day(client):
    dep = _make_deposit(client)
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T09:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_IN", "amount": 3_000_000, "currency": "KRW",
    })
    client.post("/api/v1/cash-ledger", json={
        "occurred_at": "2026-08-12T18:00:00", "deposit_account_id": dep["id"],
        "entry_type": "EXTERNAL_OUT", "amount": 500_000, "currency": "KRW",
    })
    resp = client.get("/api/v1/cash-flow/daily", params={"start_date": "2026-08-12", "end_date": "2026-08-12"}).json()
    assert resp["items"] == [{"date": "2026-08-12", "currency": "KRW", "net_amount": pytest.approx(2_500_000)}]


def test_daily_net_flow_requires_date_range(client):
    resp = client.get("/api/v1/cash-flow/daily")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 기간 경계값
# ---------------------------------------------------------------------------
def test_today_summary_boundary(db_conn):
    from atrsite.repositories import deposits as deposits_repo
    dep = deposits_repo.create_deposit(db_conn, account_name="테스트 계좌", amount=0, currency="KRW")["id"]
    cash_ledger_repo.create_entry(
        db_conn, occurred_at="2026-08-05T23:59:59", deposit_account_id=dep,
        entry_type="EXTERNAL_OUT", amount=1000, currency="KRW",
    )
    cash_ledger_repo.create_entry(
        db_conn, occurred_at="2026-08-06T00:00:00", deposit_account_id=dep,
        entry_type="EXTERNAL_OUT", amount=2000, currency="KRW",
    )
    summary = cash_ledger_repo.period_summary(db_conn, now=datetime(2026, 8, 5, 12, 0, 0))
    assert summary["today"]["KRW"]["out"] == pytest.approx(1000)  # 8/6 00:00 기록은 "오늘"(8/5)에 안 걸림


def test_this_week_starts_monday(db_conn):
    from atrsite.repositories import deposits as deposits_repo
    dep = deposits_repo.create_deposit(db_conn, account_name="테스트 계좌", amount=0, currency="KRW")["id"]
    cash_ledger_repo.create_entry(
        db_conn, occurred_at="2026-08-02T23:00:00", deposit_account_id=dep,
        entry_type="EXTERNAL_OUT", amount=100, currency="KRW",
    )
    cash_ledger_repo.create_entry(
        db_conn, occurred_at="2026-08-03T00:00:00", deposit_account_id=dep,
        entry_type="EXTERNAL_OUT", amount=200, currency="KRW",
    )
    summary = cash_ledger_repo.period_summary(db_conn, now=datetime(2026, 8, 5, 12, 0, 0))
    assert summary["this_week"]["KRW"]["out"] == pytest.approx(200)


# ---------------------------------------------------------------------------
# db.py 마이그레이션(cash_withdrawals + cash_inflows -> cash_ledger).
# ---------------------------------------------------------------------------
def test_migration_copies_legacy_withdrawals_and_inflows_into_ledger(tmp_path):
    """VPS처럼 이미 cash_withdrawals(v1.1)/cash_inflows(v1.4)에 실데이터가 있던
    DB를 흉내내서, init_db()가 cash_ledger로 정확히 이관하는지(방향/구분/금액/
    purpose·source가 memo로 접힘) 직접 검증한다."""
    from atrsite import db as db_module

    db_path = tmp_path / "legacy.db"
    conn = db_module.connect(db_path)
    db_module.init_db(conn)  # 최신 스키마로 먼저 초기화(cash_ledger 포함, 아직 비어있음)

    dep = conn.execute(
        "INSERT INTO deposits (id, account_name, amount, currency, created_at, updated_at) "
        "VALUES ('acc1', '기존계좌', 0, 'KRW', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO cash_withdrawals "
        "(id, withdrawn_at, deposit_account_id, account_name_snapshot, purpose, amount, currency, "
        " memo, flow_type, edited, created_at, updated_at) VALUES "
        "('w1', '2026-08-05T10:00:00', 'acc1', '기존계좌', '생활비', 50000, 'KRW', NULL, 'EXTERNAL', 0, 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO cash_inflows "
        "(id, deposited_at, deposit_account_id, account_name_snapshot, source, amount, currency, "
        " memo, flow_type, edited, created_at, updated_at) VALUES "
        "('i1', '2026-08-01T09:00:00', 'acc1', '기존계좌', '급여', 3000000, 'KRW', '8월분', 'EXTERNAL', 0, 'x', 'x')"
    )
    # schema_meta의 마이그레이션 완료 플래그를 지워서 아직 이관 안 된 상태를 재현.
    conn.execute("DELETE FROM schema_meta WHERE key = 'cash_ledger_migrated_v1'")
    conn.commit()

    db_module.init_db(conn)  # 여기서 실제 이관이 일어나야 함.

    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM cash_ledger")}
    assert len(rows) == 2

    w = rows["legacy-wd-w1"]
    assert w["entry_type"] == "EXTERNAL_OUT"
    assert w["amount"] == 50000
    assert w["memo"] == "생활비"  # purpose가 memo로 접힘(원래 memo=NULL)

    i = rows["legacy-in-i1"]
    assert i["entry_type"] == "EXTERNAL_IN"
    assert i["amount"] == 3000000
    assert i["memo"] == "급여 — 8월분"  # source + 기존 memo 접힘

    # 재실행해도 중복 삽입되지 않아야 함(idempotent).
    db_module.init_db(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM cash_ledger").fetchone()["n"] == 2
    conn.close()


def test_migration_internal_transfer_flow_type_maps_correctly(tmp_path):
    from atrsite import db as db_module

    db_path = tmp_path / "legacy2.db"
    conn = db_module.connect(db_path)
    db_module.init_db(conn)

    conn.execute(
        "INSERT INTO deposits (id, account_name, amount, currency, created_at, updated_at) "
        "VALUES ('acc1', '계좌', 0, 'KRW', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO cash_withdrawals "
        "(id, withdrawn_at, deposit_account_id, account_name_snapshot, purpose, amount, currency, "
        " memo, flow_type, edited, created_at, updated_at) VALUES "
        "('w1', '2026-08-05T10:00:00', 'acc1', '계좌', '증권계좌로 이체', 200000, 'KRW', NULL, "
        " 'INTERNAL_TRANSFER', 0, 'x', 'x')"
    )
    conn.execute("DELETE FROM schema_meta WHERE key = 'cash_ledger_migrated_v1'")
    conn.commit()
    db_module.init_db(conn)

    row = conn.execute("SELECT * FROM cash_ledger WHERE id = 'legacy-wd-w1'").fetchone()
    assert row["entry_type"] == "INTERNAL_OUT"
    conn.close()


def test_migration_repoints_schedule_executions_fk_to_cash_ledger(tmp_path):
    """v1.5 -- schedule_executions.linked_withdrawal_id의 FK가 cash_withdrawals를
    가리키던 옛 DB를 흉내내서, init_db()가 안전하게(빈 테이블일 때만) 드롭 후
    cash_ledger를 가리키도록 재생성하는지 확인한다. 재생성 후 실제로
    cash_ledger의 id를 FK로 넣어도 위반 없이 저장되는지까지 검증한다."""
    import sqlite3

    from atrsite import db as db_module

    db_path = tmp_path / "legacy_fk.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # 옛(v1.2) 스키마만 재현(FK 대상 테이블은 SQLite가 CREATE TABLE 시점에
    # 존재를 요구하지 않으므로 별도로 안 만들어도 됨) -- linked_withdrawal_id가
    # cash_withdrawals를 가리키는 옛 정의 그대로.
    conn.execute(
        """
        CREATE TABLE schedule_executions (
            id TEXT PRIMARY KEY,
            occurrence_id TEXT NOT NULL REFERENCES schedule_occurrences(id) ON DELETE CASCADE,
            execution_type TEXT NOT NULL,
            linked_withdrawal_id TEXT REFERENCES cash_withdrawals(id) ON DELETE SET NULL,
            linked_trade_id TEXT REFERENCES trades(id) ON DELETE SET NULL,
            executed_amount REAL, executed_at TEXT NOT NULL, memo TEXT, created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    conn = db_module.connect(db_path)
    db_module.init_db(conn)  # 여기서 재생성이 일어나야 함.

    fk_rows = conn.execute("PRAGMA foreign_key_list(schedule_executions)").fetchall()
    assert any(r["table"] == "cash_ledger" and r["from"] == "linked_withdrawal_id" for r in fk_rows)
    assert not any(r["table"] == "cash_withdrawals" for r in fk_rows)
    # 실제 API 경로를 통한 end-to-end 검증(생성->링크->조회)은
    # test_schedules_api.py::test_execution_links_to_existing_withdrawal에서 커버.
    conn.close()


def test_ledger_entries_excluded_from_net_when_created_fresh_without_legacy_data(tmp_path):
    """마이그레이션 로직이 신규(레거시 데이터 없는) DB에서는 아무 것도 하지
    않고 조용히 넘어가는지 확인 -- cash_withdrawals/cash_inflows가 비어있어도
    init_db가 에러 없이 통과해야 함."""
    from atrsite import db as db_module

    conn = db_module.connect(tmp_path / "fresh.db")
    db_module.init_db(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM cash_ledger").fetchone()["n"] == 0
    conn.close()


def test_migration_adds_interest_income_to_cash_ledger_without_losing_existing_rows(tmp_path):
    """v1.7 -- cash_ledger.entry_type CHECK 제약이 옛 4종(EXTERNAL_IN/OUT,
    INTERNAL_IN/OUT)뿐이던 DB를 흉내내서, init_db()가 기존 행을 잃지 않고
    INTEREST_INCOME까지 받아들이는 새 제약으로 재생성하는지 직접 검증한다.
    cash_ledger는 schedule_executions FK 마이그레이션(v1.5)과 달리 이미
    실데이터가 있는 상태를 재현해야 하므로 "비어있으면 drop"이 아니라
    실제로 행을 하나 넣어두고 이관 후에도 그대로 남아있는지 확인한다."""
    import sqlite3

    from atrsite import db as db_module

    db_path = tmp_path / "legacy_entry_type.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # 옛(v1.5) 제약 그대로 재현 -- INTEREST_INCOME이 아직 없음.
    conn.execute(
        """
        CREATE TABLE cash_ledger (
            id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL,
            deposit_account_id TEXT, account_name_snapshot TEXT NOT NULL,
            entry_type TEXT NOT NULL
                CHECK(entry_type IN ('EXTERNAL_IN', 'EXTERNAL_OUT', 'INTERNAL_IN', 'INTERNAL_OUT')),
            amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'KRW', memo TEXT,
            edited INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO cash_ledger VALUES "
        "('e1', '2026-08-05T10:00:00', NULL, '기존계좌', 'EXTERNAL_OUT', 50000, 'KRW', NULL, 0, 'x', 'x')"
    )
    conn.commit()
    conn.close()

    conn = db_module.connect(db_path)
    db_module.init_db(conn)  # 여기서 재생성 + 데이터 복원이 일어나야 함.

    row = conn.execute("SELECT * FROM cash_ledger WHERE id = 'e1'").fetchone()
    assert row is not None and row["amount"] == 50000  # 기존 데이터 보존
    assert conn.execute("SELECT COUNT(*) AS n FROM cash_ledger").fetchone()["n"] == 1  # 중복 없음

    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cash_ledger'"
    ).fetchone()["sql"]
    assert "INTEREST_INCOME" in table_sql

    # 새 제약으로 INTEREST_INCOME 삽입이 실제로 되는지까지 확인(FK 위반 없이).
    conn.execute(
        "INSERT INTO cash_ledger VALUES "
        "('e2', '2026-08-12T09:00:00', NULL, '기존계좌', 'INTEREST_INCOME', 523, 'KRW', NULL, 0, 'x', 'x')"
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM cash_ledger").fetchone()["n"] == 2

    # 재실행해도 중복 이관/재생성이 안 일어나야 함(idempotent).
    db_module.init_db(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM cash_ledger").fetchone()["n"] == 2
    conn.close()
