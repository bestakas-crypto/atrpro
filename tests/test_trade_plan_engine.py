"""trade_plan_engine 단위 테스트 -- 2026-08-07 확정 프롬프트 7절 필수 항목 +
QQQ/KODEX 200 실제 확정값 회귀 테스트.
"""
from atrsite.services.trade_plan_engine import (
    PlanObservation,
    PlanState,
    TierState,
    compute_tier_sell_quantities,
    evaluate_plan,
    is_approaching,
    is_cumulative_final_tier,
    is_trigger_reached,
)

NOW = "2026-08-07T10:00:00+00:00"


def _armed(trigger_price=717.0, direction="ABOVE", approach_notified_at=None):
    return PlanState(
        lifecycle_status="ARMED",
        trigger_price=trigger_price,
        trigger_direction=direction,
        trigger_activated_at=None,
        peak_price_since_trigger=None,
        confirm_mode="CLOSE",
        approach_notified_at=approach_notified_at,
    )


def _active(peak=717.0, status="ACTIVE", confirm_mode="CLOSE"):
    return PlanState(
        lifecycle_status=status,
        trigger_price=717.0,
        trigger_direction="ABOVE",
        trigger_activated_at="2026-08-06T00:00:00+00:00",
        peak_price_since_trigger=peak,
        confirm_mode=confirm_mode,
        approach_notified_at="2026-08-05T00:00:00+00:00",
    )


def _obs(price, *, valid=True, close=None, atr=None):
    return PlanObservation(intraday_price=price, is_data_valid=valid, confirmed_close=close, atr=atr)


# ---------------------------------------------------------------------------
# ARMED -> 접근 1회 -> 도달 -> ACTIVE
# ---------------------------------------------------------------------------

def test_armed_to_approach_once():
    plan = _armed()
    result = evaluate_plan(plan, [], _obs(710.0, atr=None), now_iso=NOW)
    # 710/717까지 거리 0.976% <= 2% → 접근 알림
    assert result.events == ("TRIGGER_APPROACH",)
    assert result.new_approach_notified_at == NOW
    assert result.new_lifecycle_status == "ARMED"


def test_approach_only_fires_once_per_plan():
    plan = _armed(approach_notified_at="2026-08-01T00:00:00+00:00")
    result = evaluate_plan(plan, [], _obs(710.0), now_iso=NOW)
    assert result.events == ()
    assert result.new_approach_notified_at == "2026-08-01T00:00:00+00:00"


def test_armed_to_active_on_trigger_reached():
    plan = _armed()
    result = evaluate_plan(plan, [], _obs(717.5), now_iso=NOW)
    assert result.new_lifecycle_status == "ACTIVE"
    assert result.new_trigger_activated_at == NOW
    assert result.new_peak_price == 717.5  # 활성화 순간 관측값이 최초 peak
    assert result.events == ("TRIGGER_REACHED",)


def test_no_past_high_leaks_into_new_peak():
    """계획 설정 전/트리거 전의 과거 최고가를 소급 사용하지 않는다 --
    ARMED 상태에서는 peak 필드 자체가 계속 None이어야 하고, 활성화 순간의
    관측가만 최초 peak가 된다(예: 과거에 900까지 갔던 종목이라도 무관)."""
    plan = _armed()
    approach = evaluate_plan(plan, [], _obs(712.0), now_iso=NOW)
    assert approach.new_peak_price is None
    activated = evaluate_plan(plan, [], _obs(718.0), now_iso=NOW)
    assert activated.new_peak_price == 718.0  # 900이 아니라 활성화 시점 관측값


# ---------------------------------------------------------------------------
# peak는 오르기만 하고 내려가지 않음
# ---------------------------------------------------------------------------

def test_peak_only_rises_never_falls():
    plan = _active(peak=720.0)
    lower = evaluate_plan(plan, [], _obs(715.0, close=715.0), now_iso=NOW)
    assert lower.new_peak_price == 720.0  # 하락해도 peak 유지

    higher = evaluate_plan(plan, [], _obs(725.0), now_iso=NOW)
    assert higher.new_peak_price == 725.0  # 상승하면 peak 갱신


# ---------------------------------------------------------------------------
# 장중 이탈은 예비알림만, 종가 확정만 tier 발동
# ---------------------------------------------------------------------------

def test_intraday_breach_only_previews_no_fired_at():
    tier = TierState(tier_order=1, pullback_pct=1.25, sell_pct=40, fired_at=None)
    plan = _active(peak=717.0)
    result = evaluate_plan(plan, [tier], _obs(708.0, close=None), now_iso=NOW)
    assert len(result.events) == 1
    ev = result.events[0]
    assert ev.kind == "PREVIEW"
    assert result.tier_updates == ()
    assert result.new_lifecycle_status == "ACTIVE"  # 아직 발동 안 됨


def test_confirmed_close_breach_fires_tier():
    tier = TierState(tier_order=1, pullback_pct=1.25, sell_pct=40, fired_at=None)
    plan = _active(peak=717.0)
    line = 717.0 * (1 - 0.0125)
    result = evaluate_plan(plan, [tier], _obs(line - 0.5, close=line - 0.5), now_iso=NOW)
    assert len(result.events) == 1
    assert result.events[0].kind == "FIRED"
    assert len(result.tier_updates) == 1
    assert result.new_lifecycle_status == "PARTIALLY_FIRED"


def test_same_tier_never_fires_twice():
    tier_fired = TierState(tier_order=1, pullback_pct=1.25, sell_pct=40, fired_at="2026-08-06T00:00:00+00:00")
    plan = _active(peak=717.0, status="PARTIALLY_FIRED")
    line = 717.0 * (1 - 0.0125)
    result = evaluate_plan(plan, [tier_fired], _obs(line - 5, close=line - 5), now_iso=NOW)
    assert result.events == ()
    assert result.tier_updates == ()


# ---------------------------------------------------------------------------
# 데이터 장애 -- 전 상태 보존, 신호 없음
# ---------------------------------------------------------------------------

def test_data_stale_preserves_everything():
    tier = TierState(tier_order=1, pullback_pct=1.25, sell_pct=40, fired_at=None)
    plan = _active(peak=717.0)
    result = evaluate_plan(plan, [tier], _obs(None, valid=False), now_iso=NOW)
    assert result.new_lifecycle_status == "ACTIVE"
    assert result.new_peak_price == 717.0
    assert result.events == ("DATA_STALE",)
    assert result.tier_updates == ()


def test_zero_or_negative_price_treated_as_invalid():
    plan = _active(peak=717.0)
    result = evaluate_plan(plan, [], _obs(0.0), now_iso=NOW)
    assert result.events == ("DATA_STALE",)
    assert result.new_peak_price == 717.0


def test_resume_from_preserved_state_after_recovery():
    plan = _active(peak=717.0)
    stale = evaluate_plan(plan, [], _obs(None, valid=False), now_iso=NOW)
    resumed_plan = PlanState(
        lifecycle_status=stale.new_lifecycle_status,
        trigger_price=plan.trigger_price,
        trigger_direction=plan.trigger_direction,
        trigger_activated_at=stale.new_trigger_activated_at,
        peak_price_since_trigger=stale.new_peak_price,
        confirm_mode=plan.confirm_mode,
        approach_notified_at=stale.new_approach_notified_at,
    )
    recovered = evaluate_plan(resumed_plan, [], _obs(725.0), now_iso=NOW)
    assert recovered.new_peak_price == 725.0


# ---------------------------------------------------------------------------
# 터미널 상태는 더 이상 평가 안 함
# ---------------------------------------------------------------------------

def test_completed_plan_is_not_reevaluated():
    plan = PlanState("COMPLETED", 717.0, "ABOVE", "2026-08-01T00:00:00+00:00", 730.0, "CLOSE", None)
    result = evaluate_plan(plan, [], _obs(1000.0), now_iso=NOW)
    assert result.new_lifecycle_status == "COMPLETED"
    assert result.new_peak_price == 730.0
    assert result.events == ()


def test_cancelled_plan_is_not_reevaluated():
    plan = PlanState("CANCELLED", 717.0, "ABOVE", None, None, "CLOSE", None)
    result = evaluate_plan(plan, [], _obs(1000.0), now_iso=NOW)
    assert result.new_lifecycle_status == "CANCELLED"
    assert result.events == ()


# ---------------------------------------------------------------------------
# 갭으로 여러 tier 동시 이탈
# ---------------------------------------------------------------------------

def test_gap_down_fires_multiple_tiers_at_once():
    tier1 = TierState(tier_order=1, pullback_pct=2.5, sell_pct=40, fired_at=None)
    tier2 = TierState(tier_order=2, pullback_pct=6.0, sell_pct=60, fired_at=None)
    plan = _active(peak=115000.0)
    gap_price = 107000.0  # 두 선(112,125 / 108,100) 모두 아래
    result = evaluate_plan(plan, [tier1, tier2], _obs(gap_price, close=gap_price), now_iso=NOW)
    fired = [e for e in result.events if e.kind == "FIRED"]
    assert {e.tier_order for e in fired} == {1, 2}
    assert len(result.tier_updates) == 2
    assert result.new_lifecycle_status == "COMPLETED"


def test_no_duplicate_notification_next_poll_after_gap_fire():
    tier1 = TierState(tier_order=1, pullback_pct=2.5, sell_pct=40, fired_at="2026-08-07T09:00:00+00:00")
    tier2 = TierState(tier_order=2, pullback_pct=6.0, sell_pct=60, fired_at="2026-08-07T09:00:00+00:00")
    plan = _active(peak=115000.0, status="COMPLETED")
    result = evaluate_plan(plan, [tier1, tier2], _obs(107000.0, close=107000.0), now_iso=NOW)
    assert result.events == ()
    assert result.tier_updates == ()


# ---------------------------------------------------------------------------
# QQQ 확정 회귀값: baseline 427주, 40% = 171주, 나머지 256주
# ---------------------------------------------------------------------------

def test_qqq_tier1_matches_confirmed_regression():
    for peak, expected_line in [
        (717.0, 708.04), (720.0, 711.00), (725.0, 715.94), (730.0, 720.88), (740.0, 730.75),
    ]:
        line = round(peak * 0.9875, 2)
        assert line == expected_line


def test_qqq_tier1_sell_quantity_is_171_of_427_not_final():
    result = compute_tier_sell_quantities({"qqq": 427.0}, 40.0, is_final_tier=False)
    assert result == {"qqq": 171}


def test_qqq_baseline_unaffected_by_later_accumulation():
    """VXN 적립으로 실보유수량이 늘어도(예: 427 -> 440) baseline_quantity는
    계획 확정 시점 스냅샷(427)을 그대로 쓴다 -- 이 함수는 애초에
    baseline_quantities만 받으므로 호출자가 실시간 수량을 안 넘기면 자동으로
    보장된다."""
    result = compute_tier_sell_quantities({"qqq": 427.0}, 40.0, is_final_tier=False)
    assert result["qqq"] == 171  # 실보유 440이어도 이 값은 안 바뀜(입력에 없음)


# ---------------------------------------------------------------------------
# KODEX 200 확정 회귀값: 908주+367주, 1차 40%=510(363+147), 2차 잔여 765(545+220)
# ---------------------------------------------------------------------------

def test_kodex200_tier1_split_363_147():
    result = compute_tier_sell_quantities({"acct_908": 908.0, "acct_367": 367.0}, 40.0, is_final_tier=False)
    assert result == {"acct_908": 363, "acct_367": 147}
    assert sum(result.values()) == 510


def test_kodex200_tier2_final_remaining_545_220():
    already = {"acct_908": 363.0, "acct_367": 147.0}
    result = compute_tier_sell_quantities(
        {"acct_908": 908.0, "acct_367": 367.0}, 60.0, is_final_tier=True, already_recommended=already,
    )
    assert result == {"acct_908": 545, "acct_367": 220}
    assert sum(result.values()) == 765


def test_kodex200_price_lines_match_confirmed_table():
    for peak, tier1, tier2 in [
        (115000.0, 112125.0, 108100.0),
        (120000.0, 117000.0, 112800.0),
        (125000.0, 121875.0, 117500.0),
        (130000.0, 126750.0, 122200.0),
    ]:
        assert round(peak * 0.975, 0) == tier1
        assert round(peak * 0.94, 0) == tier2


def test_is_cumulative_final_tier():
    tiers = [
        TierState(1, 2.5, 40, None),
        TierState(2, 6.0, 60, None),
    ]
    assert is_cumulative_final_tier(tiers, 1) is False
    assert is_cumulative_final_tier(tiers, 2) is True


def test_qqq_single_tier_is_not_final_partial_hold_remains():
    """QQQ는 현재 1차(40%)만 있고 2차 미확정 -- 누적 40%는 final이 아니므로
    발동 후에도 PARTIALLY_FIRED로 남아 잔여 60%(256주)를 계속 보유해야 한다."""
    tiers = [TierState(1, 1.25, 40, None)]
    assert is_cumulative_final_tier(tiers, 1) is False


# ---------------------------------------------------------------------------
# 보조 판정 함수
# ---------------------------------------------------------------------------

def test_is_trigger_reached_above_and_below():
    assert is_trigger_reached("ABOVE", 717.5, 717.0) is True
    assert is_trigger_reached("ABOVE", 716.9, 717.0) is False
    assert is_trigger_reached("BELOW", 100.0, 105.0) is True
    assert is_trigger_reached("BELOW", 110.0, 105.0) is False


def test_is_approaching_uses_atr_when_wider_than_2pct():
    # ATR%가 2%보다 크면 그 값을 threshold로 쓴다
    assert is_approaching("ABOVE", 700.0, 717.0, atr=30.0) is True  # atr%~4.3%, distance~2.4%
    assert is_approaching("ABOVE", 690.0, 717.0, atr=None) is False  # distance~3.8% > 기본 2%
