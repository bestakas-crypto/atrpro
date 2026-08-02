"""
signal_engine 단위 테스트 — strpro_design_spec.md 19절 "신호 테스트" 1~10번 +
"레칫 테스트" 전부.
"""
import pytest

from atrsite.services.signal_engine import (
    DataStatus,
    SignalInput,
    SignalStatus,
    apply_trailing_stop_ratchet,
    compute_next_buy_price,
    compute_take_profit_price,
    compute_trailing_stop_candidate,
    determine_signal,
    reset_trailing_stop_cycle,
    should_emit_event,
)


# ---------------------------------------------------------------------------
# 신호 테스트 1~2 — 미보유 상태에서는 익절/손절 신호가 아예 발생하지 않아야 함
# ---------------------------------------------------------------------------

def test_01_no_take_profit_signal_without_position():
    """1. 미보유 상태에서 익절 신호가 발생하지 않음.

    보유수량 0인데 현재가가 (잘못 계산된) 익절가 이상이어도 익절 신호가 나오면
    안 된다 — 스펙 4.3의 핵심 버그 재발 방지 테스트.
    """
    result = determine_signal(SignalInput(
        quantity=0,
        current_price=1000,
        next_buy_price=2000,  # 매수가보다도 낮아서 매수신호는 뜰 수 있음(허용)
        take_profit_price=500,  # 현재가가 익절가 이상인 상황을 의도적으로 구성
        trailing_stop_price=None,
    ))
    assert result.status != SignalStatus.TAKE_PROFIT_TRIGGERED
    assert result.status != SignalStatus.SELL_BOTH_TRIGGERED


def test_02_no_stop_signal_without_position():
    """2. 미보유 상태에서 손절 신호가 발생하지 않음."""
    result = determine_signal(SignalInput(
        quantity=0,
        current_price=1000,
        next_buy_price=2000,
        take_profit_price=None,
        trailing_stop_price=1500,  # 현재가가 손절선 이하인 상황을 의도적으로 구성
    ))
    assert result.status != SignalStatus.STOP_TRIGGERED
    assert result.status != SignalStatus.SELL_BOTH_TRIGGERED


def test_03_buy_signal_triggered():
    """3. 매수 기준 도달"""
    result = determine_signal(SignalInput(
        quantity=0,
        current_price=95,
        next_buy_price=100,
        take_profit_price=None,
        trailing_stop_price=None,
    ))
    assert result.status == SignalStatus.BUY_TRIGGERED
    assert result.hit_buy is True


def test_04_take_profit_signal_triggered():
    """4. 익절 기준 도달 (보유 중일 때만)"""
    result = determine_signal(SignalInput(
        quantity=10,
        current_price=150,
        next_buy_price=90,
        take_profit_price=140,
        trailing_stop_price=80,
    ))
    assert result.status == SignalStatus.TAKE_PROFIT_TRIGGERED


def test_05_stop_signal_triggered():
    """5. 손절 기준 도달 (보유 중일 때만)"""
    result = determine_signal(SignalInput(
        quantity=10,
        current_price=79,
        next_buy_price=70,
        take_profit_price=140,
        trailing_stop_price=80,
    ))
    assert result.status == SignalStatus.STOP_TRIGGERED


def test_06_stop_and_profit_simultaneously():
    """6. 손절·익절 동시 충족 -> 최우선순위인 SELL_BOTH_TRIGGERED

    손절선이 익절가보다 높게 설정된 비정상적/극단적 설정(현재가가 두 기준선
    사이가 아니라 손절선 위쪽에 딱 걸치는 상황)에서도 우선순위 로직이 무너지지
    않는지 확인. 현재가 142는 익절가(140) 이상이면서 동시에 손절선(145) 이하다.
    """
    result = determine_signal(SignalInput(
        quantity=10,
        current_price=142,
        next_buy_price=90,
        take_profit_price=140,  # 현재가 >= 익절가
        trailing_stop_price=145,  # 현재가 <= 손절선(의도적으로 익절가보다 위로 설정)
    ))
    assert result.status == SignalStatus.SELL_BOTH_TRIGGERED
    assert result.hit_stop and result.hit_profit


def test_07_no_duplicate_alert_on_repeated_polling():
    """7. 반복 폴링 시 중복 알림 없음"""
    inp = SignalInput(quantity=10, current_price=150, next_buy_price=90, take_profit_price=140, trailing_stop_price=80)
    first = determine_signal(inp)
    # 동일 입력으로 5분 간격 폴링을 4번 더 반복해도 이벤트는 최초 1번만 발생해야 함
    previous_status = None
    emitted_count = 0
    for _ in range(5):
        result = determine_signal(inp)
        if should_emit_event(previous_status, result.status):
            emitted_count += 1
        previous_status = result.status
    assert emitted_count == 1
    assert first.status == SignalStatus.TAKE_PROFIT_TRIGGERED


def test_08_new_event_on_reentry_after_neutral():
    """8. 관망 후 재진입 시 새 이벤트 발생"""
    previous_status = SignalStatus.NEUTRAL
    new_status = SignalStatus.BUY_TRIGGERED
    assert should_emit_event(previous_status, new_status) is True

    # 관망 -> 매수신호 -> (다시 관망으로 돌아갔다가) -> 다시 매수신호: 두 번째도 새 이벤트
    assert should_emit_event(SignalStatus.BUY_TRIGGERED, SignalStatus.NEUTRAL) is True
    assert should_emit_event(SignalStatus.NEUTRAL, SignalStatus.BUY_TRIGGERED) is True


@pytest.mark.parametrize("blocked_status", [
    DataStatus.STALE,
    DataStatus.API_ERROR,
    DataStatus.INSUFFICIENT_DATA,
])
def test_09_new_signal_blocked_when_data_delayed(blocked_status):
    """9. 데이터 지연 시 신규 신호 차단

    조건상으로는 명백히 손절/익절/매수가 전부 도달한 상태를 만들어도, 데이터
    상태가 STALE/API_ERROR/INSUFFICIENT_DATA면 어떤 트리거 신호도 나오면 안 됨.
    """
    result = determine_signal(SignalInput(
        quantity=10,
        current_price=1,  # 극단적으로 낮은 값 -> 손절/매수 전부 조건 충족될 상황
        next_buy_price=1000,
        take_profit_price=1000,
        trailing_stop_price=1000,
        data_status=blocked_status,
    ))
    assert result.status not in (
        SignalStatus.BUY_TRIGGERED,
        SignalStatus.TAKE_PROFIT_TRIGGERED,
        SignalStatus.STOP_TRIGGERED,
        SignalStatus.SELL_BOTH_TRIGGERED,
    )


def test_09b_delayed_status_alone_does_not_block():
    """DELAYED는 차단 목록에 없음(스펙 17.3 — 지연은 '오래된 데이터'보다 약한 단계) —
    정상적으로 신호가 계산되어야 한다."""
    result = determine_signal(SignalInput(
        quantity=0,
        current_price=95,
        next_buy_price=100,
        take_profit_price=None,
        trailing_stop_price=None,
        data_status=DataStatus.DELAYED,
    ))
    assert result.status == SignalStatus.BUY_TRIGGERED


def test_10_deterministic_after_restart_simulation():
    """10. 서버 재시작 후 동일 상태 복원

    determine_signal은 순수 함수이므로, "재시작 전 계산"과 "DB에서 그대로 복원한
    입력으로 재시작 후 다시 계산"이 완전히 동일한 결과를 내야 한다.
    """
    inp = SignalInput(quantity=7, current_price=88, next_buy_price=90, take_profit_price=120, trailing_stop_price=80)
    before_restart = determine_signal(inp)

    # "재시작"을 흉내: 같은 입력값을 처음 보는 것처럼 다시 계산
    restored_inp = SignalInput(
        quantity=inp.quantity,
        current_price=inp.current_price,
        next_buy_price=inp.next_buy_price,
        take_profit_price=inp.take_profit_price,
        trailing_stop_price=inp.trailing_stop_price,
        data_status=inp.data_status,
    )
    after_restart = determine_signal(restored_inp)

    assert before_restart.status == after_restart.status
    assert before_restart.reason == after_restart.reason


# ---------------------------------------------------------------------------
# 레칫 테스트 — 스펙 19절 예시와 정확히 일치
# ---------------------------------------------------------------------------

def test_ratchet_exact_spec_example():
    """
    최고가 100, ATR 10, 배수 2 -> 손절선 80
    최고가 110 -> 손절선 90
    ATR 20으로 증가(후보 70) -> 저장 손절선은 90 유지
    """
    stop_multiple = 2

    candidate1 = compute_trailing_stop_candidate(post_entry_high=100, atr=10, stop_multiple=stop_multiple)
    assert candidate1 == 80
    stop_line = apply_trailing_stop_ratchet(existing_stop=None, candidate=candidate1)
    assert stop_line == 80

    candidate2 = compute_trailing_stop_candidate(post_entry_high=110, atr=10, stop_multiple=stop_multiple)
    assert candidate2 == 90
    stop_line = apply_trailing_stop_ratchet(existing_stop=stop_line, candidate=candidate2)
    assert stop_line == 90

    # ATR이 20으로 커져서 후보가 70으로 낮아져도 저장된 손절선은 90 유지
    candidate3 = compute_trailing_stop_candidate(post_entry_high=110, atr=20, stop_multiple=stop_multiple)
    assert candidate3 == 70
    stop_line = apply_trailing_stop_ratchet(existing_stop=stop_line, candidate=candidate3)
    assert stop_line == 90  # 절대 안 내려감


def test_ratchet_resets_on_full_exit_and_restarts_on_new_position():
    """전량 매도 후 초기화, 새 포지션 진입 시 새 사이클 시작 (스펙 8.4)"""
    post_entry_high, stop_line = None, None
    candidate = compute_trailing_stop_candidate(post_entry_high=100, atr=10, stop_multiple=2)
    stop_line = apply_trailing_stop_ratchet(stop_line, candidate)
    assert stop_line == 80

    # 전량 매도 -> 리셋
    post_entry_high, stop_line = reset_trailing_stop_cycle()
    assert post_entry_high is None
    assert stop_line is None

    # 새 포지션 진입: 이전 손절선(80)의 영향을 전혀 받지 않고 새로 시작해야 함
    post_entry_high = 50
    candidate = compute_trailing_stop_candidate(post_entry_high=post_entry_high, atr=5, stop_multiple=2)
    stop_line = apply_trailing_stop_ratchet(stop_line, candidate)
    assert stop_line == 40  # 50 - 5*2, 과거 80의 흔적이 전혀 없음


def test_ratchet_survives_missing_atr_by_keeping_existing_value():
    """ATR/최고가가 일시적으로 없으면(candidate=None) 기존 손절선을 그대로 유지해야 함
    (서버 재시작 직후 등, 스펙 8.4 '서버 재시작 후에도 DB에 저장된 값을 복원')."""
    stop_line = 90
    candidate = compute_trailing_stop_candidate(post_entry_high=110, atr=None, stop_multiple=2)
    assert candidate is None
    stop_line = apply_trailing_stop_ratchet(stop_line, candidate)
    assert stop_line == 90


# ---------------------------------------------------------------------------
# 8.2 / 8.3 기준선 계산 보조 테스트 (다음 매수가가 last_buy_price 기준인지)
# ---------------------------------------------------------------------------

def test_next_buy_price_uses_last_buy_price_not_last_trade_price():
    """스펙 4.4 — 부분매도가 있어도 다음 매수가는 마지막 '매수'가 기준이어야 한다.

    100원 매수 -> 120원 부분매도 -> ATR 10
    다음 매수가 = 마지막 매수가(100) - 10 = 90원 (매도가 120을 기준으로 삼으면 안 됨)
    """
    last_buy_price = 100  # 부분매도(120)가 있었어도 이 값은 변하지 않음(position_engine이 보장)
    next_buy = compute_next_buy_price(last_buy_price=last_buy_price, atr=10, buy_multiple=1.0)
    assert next_buy == 90


def test_take_profit_inactive_without_position():
    assert compute_take_profit_price(avg_price=150, atr=10, sell_multiple=1.5, quantity=0) is None


def test_next_buy_price_none_without_prior_buy():
    assert compute_next_buy_price(last_buy_price=None, atr=10, buy_multiple=1.0) is None


# ---------------------------------------------------------------------------
# 9.4 히스테리시스가 determine_signal()에 실제로 연결됐는지 (2026-08-02 추가)
# was_hit_*/atr을 안 넘기면(기본값) 예전과 동일한 단순 임계값 판정이어야 한다.
# ---------------------------------------------------------------------------

def test_determine_signal_without_hysteresis_args_matches_legacy_behavior():
    """기본값(모든 was_hit_* False, atr 미지정)이면 히스테리시스 도입 전과 동일하게
    임계값을 살짝만 벗어나도 즉시 꺼져야 한다 -- 하위 호환성 확인."""
    inp = SignalInput(quantity=10, current_price=91, next_buy_price=90, take_profit_price=140, trailing_stop_price=80)
    result = determine_signal(inp)
    assert result.status == SignalStatus.NEUTRAL  # 91 > 90이므로 매수신호 아님, 밴드 없이 즉시 꺼짐


def test_buy_signal_stays_active_within_hysteresis_band_once_triggered():
    """매수가 90, ATR 10 -> 해제가는 90+0.1*10=91. 가격이 90 밑으로 갔다가
    91 미만으로만 살짝 반등하면(예: 90.5) 신호가 계속 유지돼야 한다."""
    inp = SignalInput(
        quantity=0, current_price=90.5, next_buy_price=90, take_profit_price=None,
        trailing_stop_price=None, atr=10,
    )
    result = determine_signal(inp, was_hit_buy=True)
    assert result.status == SignalStatus.BUY_TRIGGERED


def test_buy_signal_releases_once_price_clears_hysteresis_band():
    """91 이상으로 완전히 벗어나면(해제가 91) 그제서야 꺼진다."""
    inp = SignalInput(
        quantity=0, current_price=91, next_buy_price=90, take_profit_price=None,
        trailing_stop_price=None, atr=10,
    )
    result = determine_signal(inp, was_hit_buy=True)
    assert result.status != SignalStatus.BUY_TRIGGERED


def test_take_profit_signal_stays_active_within_hysteresis_band():
    """익절가 140, ATR 10 -> 해제가 140-0.1*10=139. 141까지 올랐다가 139.5로
    살짝 내려와도(139 초과) 여전히 익절 신호가 유지돼야 한다."""
    inp = SignalInput(
        quantity=10, current_price=139.5, next_buy_price=None, take_profit_price=140,
        trailing_stop_price=None, atr=10,
    )
    result = determine_signal(inp, was_hit_profit=True)
    assert result.status == SignalStatus.TAKE_PROFIT_TRIGGERED


def test_stop_signal_does_not_apply_hysteresis_when_was_active_is_false():
    """직전에 신호가 꺼져 있었다면(was_hit_stop=False) 밴드를 적용하지 않고
    임계값 그대로 판정한다 -- 밴드는 "이미 켜져 있던 신호를 유지"할 때만 쓰인다."""
    inp = SignalInput(
        quantity=10, current_price=80.5, next_buy_price=None, take_profit_price=None,
        trailing_stop_price=80, atr=10,
    )
    result = determine_signal(inp, was_hit_stop=False)
    assert result.status == SignalStatus.NEUTRAL  # 80.5 > 80이므로 손절 미충족, 밴드 미적용


def test_hysteresis_falls_back_to_plain_threshold_when_atr_missing():
    """was_active=True여도 atr이 없으면 밴드를 못 그리므로 즉시 해제된다."""
    inp = SignalInput(
        quantity=0, current_price=90.5, next_buy_price=90, take_profit_price=None,
        trailing_stop_price=None, atr=None,
    )
    result = determine_signal(inp, was_hit_buy=True)
    assert result.status != SignalStatus.BUY_TRIGGERED
