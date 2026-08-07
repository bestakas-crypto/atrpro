"""repositories/trade_plans.py 통합 테스트 -- 실제 SQLite 대상."""
import pytest

from atrsite.repositories import instruments as instruments_repo
from atrsite.repositories import trade_plans as plans_repo


def _make_instrument(conn, name="QQQ", currency="USD", kis_code="QQQ", kis_market="NAS"):
    inst = instruments_repo.create_instrument(
        conn, name=name, currency=currency, kis_code=kis_code, kis_market=kis_market,
    )
    return inst["id"]


def test_create_single_instrument_plan(db_conn):
    qqq_id = _make_instrument(db_conn)
    plan = plans_repo.create_plan(
        db_conn,
        plan_type="TRAIL", label="QQQ 기존물량 1차 부분익절",
        trigger_price=717.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
        price_reference_instrument_id=qqq_id,
        instruments=[{"instrument_id": qqq_id, "baseline_quantity": 427.0}],
        tiers=[{"tier_order": 1, "pullback_pct": 1.25, "sell_pct": 40.0}],
        reason="717달러 목표구간 도달, 40% 부분익절",
    )
    assert plan["lifecycle_status"] == "ARMED"
    assert plan["version"] == 1
    assert len(plan["instruments"]) == 1
    assert plan["instruments"][0]["baseline_quantity"] == 427.0
    assert len(plan["tiers"]) == 1
    assert plan["tiers"][0]["pullback_pct"] == 1.25


def test_create_multi_instrument_plan_kodex200(db_conn):
    acct_a = _make_instrument(db_conn, name="Kodex 200", kis_code="069500", kis_market="KRX", currency="KRW")
    acct_b = _make_instrument(db_conn, name="kodex 200 New", kis_code="069500", kis_market="KRX", currency="KRW")
    plan = plans_repo.create_plan(
        db_conn,
        plan_type="TRAIL", label="KODEX 200 국내시장 종료",
        trigger_price=115000.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
        price_reference_instrument_id=acct_a,
        instruments=[
            {"instrument_id": acct_a, "baseline_quantity": 908.0},
            {"instrument_id": acct_b, "baseline_quantity": 367.0},
        ],
        tiers=[
            {"tier_order": 1, "pullback_pct": 2.5, "sell_pct": 40.0},
            {"tier_order": 2, "pullback_pct": 6.0, "sell_pct": 60.0},
        ],
        purpose="국내주식 투자 종료",
    )
    assert len(plan["instruments"]) == 2
    total_baseline = sum(i["baseline_quantity"] for i in plan["instruments"])
    assert total_baseline == 1275.0
    assert len(plan["tiers"]) == 2


def test_price_reference_must_be_one_of_connected_instruments(db_conn):
    qqq_id = _make_instrument(db_conn)
    other_id = _make_instrument(db_conn, name="QQQM", kis_code="QQQM")
    with pytest.raises(ValueError):
        plans_repo.create_plan(
            db_conn, plan_type="TRAIL", label="bad",
            trigger_price=100.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
            price_reference_instrument_id=other_id,  # 연결 안 된 종목
            instruments=[{"instrument_id": qqq_id, "baseline_quantity": 10.0}],
            tiers=[],
        )


def test_tier_validation_rejects_over_100_percent(db_conn):
    qqq_id = _make_instrument(db_conn)
    with pytest.raises(ValueError):
        plans_repo.create_plan(
            db_conn, plan_type="TRAIL", label="bad",
            trigger_price=100.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
            price_reference_instrument_id=qqq_id,
            instruments=[{"instrument_id": qqq_id, "baseline_quantity": 10.0}],
            tiers=[
                {"tier_order": 1, "pullback_pct": 1.0, "sell_pct": 60.0},
                {"tier_order": 2, "pullback_pct": 2.0, "sell_pct": 60.0},
            ],
        )


def test_only_trail_plan_type_allowed_in_phase1(db_conn):
    qqq_id = _make_instrument(db_conn)
    with pytest.raises(ValueError):
        plans_repo.create_plan(
            db_conn, plan_type="ACCUMULATE", label="bad",
            trigger_price=100.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
            price_reference_instrument_id=qqq_id,
            instruments=[{"instrument_id": qqq_id, "baseline_quantity": 10.0}],
            tiers=[],
        )


def test_update_plan_bumps_version_and_records_history(db_conn):
    qqq_id = _make_instrument(db_conn)
    plan = plans_repo.create_plan(
        db_conn, plan_type="TRAIL", label="v1",
        trigger_price=717.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
        price_reference_instrument_id=qqq_id,
        instruments=[{"instrument_id": qqq_id, "baseline_quantity": 427.0}],
        tiers=[{"tier_order": 1, "pullback_pct": 1.25, "sell_pct": 40.0}],
    )
    updated = plans_repo.update_plan_fields(
        db_conn, plan["id"], change_reason="실적전망 상향으로 트리거 조정",
        trigger_price=725.0,
    )
    assert updated["version"] == 2
    assert updated["trigger_price"] == 725.0

    history = plans_repo.get_history(db_conn, plan["id"])
    assert len(history) == 1
    assert history[0]["version"] == 2
    assert history[0]["change_reason"] == "실적전망 상향으로 트리거 조정"
    # 스냅샷에 연결 종목/baseline/tiers가 전부 복원 가능하게 들어있어야 함
    assert history[0]["snapshot"]["instruments"][0]["baseline_quantity"] == 427.0
    assert history[0]["snapshot"]["tiers"][0]["pullback_pct"] == 1.25


def test_cancel_is_not_hard_delete(db_conn):
    qqq_id = _make_instrument(db_conn)
    plan = plans_repo.create_plan(
        db_conn, plan_type="TRAIL", label="v1",
        trigger_price=717.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
        price_reference_instrument_id=qqq_id,
        instruments=[{"instrument_id": qqq_id, "baseline_quantity": 427.0}],
        tiers=[],
    )
    cancelled = plans_repo.cancel_plan(db_conn, plan["id"], reason="계획 철회")
    assert cancelled["lifecycle_status"] == "CANCELLED"
    # 실제로 행이 여전히 존재(하드 삭제 아님)
    still_there = plans_repo.get_plan(db_conn, plan["id"])
    assert still_there is not None
    assert still_there["lifecycle_status"] == "CANCELLED"


def test_apply_evaluation_fires_tier_without_touching_others(db_conn):
    qqq_id = _make_instrument(db_conn)
    plan = plans_repo.create_plan(
        db_conn, plan_type="TRAIL", label="v1",
        trigger_price=717.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
        price_reference_instrument_id=qqq_id,
        instruments=[{"instrument_id": qqq_id, "baseline_quantity": 427.0}],
        tiers=[{"tier_order": 1, "pullback_pct": 1.25, "sell_pct": 40.0}],
    )
    plans_repo.apply_evaluation(
        db_conn, plan["id"],
        new_lifecycle_status="ACTIVE", new_trigger_activated_at="2026-08-06T00:00:00+00:00",
        new_peak_price=717.0, new_approach_notified_at=None, tier_updates=[],
    )
    plans_repo.apply_evaluation(
        db_conn, plan["id"],
        new_lifecycle_status="PARTIALLY_FIRED", new_trigger_activated_at="2026-08-06T00:00:00+00:00",
        new_peak_price=717.0, new_approach_notified_at=None,
        tier_updates=[{"tier_order": 1, "fired_at": "2026-08-07T00:00:00+00:00",
                        "fired_peak_price": 717.0, "fired_reference_price": 708.04}],
    )
    result = plans_repo.get_plan(db_conn, plan["id"])
    assert result["lifecycle_status"] == "PARTIALLY_FIRED"
    assert result["tiers"][0]["fired_at"] == "2026-08-07T00:00:00+00:00"

    # 같은 tier를 다시 발동시키려 해도(fired_at IS NULL 조건 때문에) 값이 안 바뀜
    plans_repo.apply_evaluation(
        db_conn, plan["id"],
        new_lifecycle_status="PARTIALLY_FIRED", new_trigger_activated_at="2026-08-06T00:00:00+00:00",
        new_peak_price=720.0, new_approach_notified_at=None,
        tier_updates=[{"tier_order": 1, "fired_at": "2026-08-08T00:00:00+00:00",
                        "fired_peak_price": 720.0, "fired_reference_price": 710.0}],
    )
    result2 = plans_repo.get_plan(db_conn, plan["id"])
    assert result2["tiers"][0]["fired_at"] == "2026-08-07T00:00:00+00:00"  # 안 바뀜


def test_list_plans_for_polling_excludes_terminal_states(db_conn):
    qqq_id = _make_instrument(db_conn)
    active_plan = plans_repo.create_plan(
        db_conn, plan_type="TRAIL", label="active",
        trigger_price=717.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
        price_reference_instrument_id=qqq_id,
        instruments=[{"instrument_id": qqq_id, "baseline_quantity": 427.0}], tiers=[],
    )
    cancelled_plan = plans_repo.create_plan(
        db_conn, plan_type="TRAIL", label="cancelled",
        trigger_price=100.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
        price_reference_instrument_id=qqq_id,
        instruments=[{"instrument_id": qqq_id, "baseline_quantity": 10.0}], tiers=[],
    )
    plans_repo.cancel_plan(db_conn, cancelled_plan["id"], reason="취소")

    polling = plans_repo.list_plans_for_polling(db_conn)
    ids = {p["id"] for p in polling}
    assert active_plan["id"] in ids
    assert cancelled_plan["id"] not in ids


def test_baseline_quantity_persists_isolated_per_account(db_conn):
    """계좌별 baseline_quantity가 서로 독립적으로 저장되고, 다른 계좌의
    나중 변경(이 리포지토리에는 update가 없으므로 재조회로만 검증)에
    영향받지 않는다."""
    acct_a = _make_instrument(db_conn, name="Kodex 200", kis_code="069500")
    acct_b = _make_instrument(db_conn, name="kodex 200 New", kis_code="069500")
    plan = plans_repo.create_plan(
        db_conn, plan_type="TRAIL", label="KODEX 200",
        trigger_price=115000.0, trigger_direction="ABOVE", confirm_mode="CLOSE",
        price_reference_instrument_id=acct_a,
        instruments=[
            {"instrument_id": acct_a, "baseline_quantity": 908.0},
            {"instrument_id": acct_b, "baseline_quantity": 367.0},
        ],
        tiers=[],
    )
    reloaded = plans_repo.get_plan(db_conn, plan["id"])
    by_id = {i["instrument_id"]: i["baseline_quantity"] for i in reloaded["instruments"]}
    assert by_id[acct_a] == 908.0
    assert by_id[acct_b] == 367.0
