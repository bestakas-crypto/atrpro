"""
position_engine 단위 테스트 — strpro_design_spec.md 19절 "계산 테스트" 1~10번 전부.
"""
import pytest

from atrsite.services.position_engine import (
    OversellError,
    PositionState,
    Trade,
    apply_buy,
    apply_sell,
    replay_trades,
)


def test_01_single_buy():
    """1. 단일 매수"""
    state = apply_buy(PositionState(), price=100, quantity=10)
    assert state.quantity == 10
    assert state.cost_basis == 1000
    assert state.avg_price == 100
    assert state.last_buy_price == 100
    assert state.last_trade_price == 100


def test_02_multiple_buys():
    """2. 복수 매수 — 이동평균 원가법으로 평균단가가 누적 계산되어야 함"""
    state = PositionState()
    state = apply_buy(state, price=100, quantity=10)  # 원가 1000, 10주
    state = apply_buy(state, price=200, quantity=10)  # 원가 +2000 = 3000, 20주
    assert state.quantity == 20
    assert state.cost_basis == 3000
    assert state.avg_price == 150
    assert state.last_buy_price == 200


def test_03_partial_sell_keeps_avg_price():
    """3. 부분매도 — 평균단가는 매도로 인해 변하지 않아야 함(스펙 4.1 핵심)"""
    state = apply_buy(PositionState(), price=100, quantity=10)
    result = apply_sell(state, price=120, quantity=4)
    assert result.state.quantity == 6
    assert result.state.avg_price == 100  # 그대로 유지
    assert result.state.cost_basis == 600
    assert result.realized_pnl == pytest.approx((120 - 100) * 4)
    assert result.state.last_sell_price == 120
    assert result.state.last_buy_price == 100  # 매도가 매수가를 안 건드림


def test_04_full_sell_normalizes_to_zero():
    """4. 전량매도 — 수량/원가/평균단가가 정확히 0으로 정규화되어야 함"""
    state = apply_buy(PositionState(), price=100, quantity=10)
    result = apply_sell(state, price=130, quantity=10)
    assert result.state.quantity == 0
    assert result.state.cost_basis == 0
    assert result.state.avg_price == 0
    assert result.realized_pnl == pytest.approx((130 - 100) * 10)


def test_05_partial_sell_then_rebuy_matches_spec_example():
    """5. 부분매도 후 재매수 — 스펙 4.1 예시와 정확히 일치해야 함.

    10주×100원 매수 -> 5주×120원 매도 -> 5주×200원 재매수
    -> 최종 보유 10주, 평균단가 150원 (133.33원이 아님)
    """
    state = PositionState()
    state = apply_buy(state, price=100, quantity=10)
    sell_result = apply_sell(state, price=120, quantity=5)
    state = sell_result.state
    # 매도 직후 중간 상태 검증: 잔여 5주 원가 500원, 평균단가 100원 유지
    assert state.quantity == 5
    assert state.cost_basis == 500
    assert state.avg_price == 100

    state = apply_buy(state, price=200, quantity=5)

    assert state.quantity == 10
    assert state.avg_price == 150  # 133.33원이 아님
    assert state.cost_basis == 1500
    assert state.last_buy_price == 200


def test_06_multiple_buy_sell_cycles():
    """6. 여러 번의 매수·매도 반복"""
    trades = [
        Trade("buy", price=100, quantity=10),
        Trade("sell", price=110, quantity=3),
        Trade("buy", price=90, quantity=5),
        Trade("sell", price=120, quantity=4),
        Trade("buy", price=150, quantity=2),
    ]
    final_state, steps = replay_trades(trades)
    # 수량 검증: 10 - 3 + 5 - 4 + 2 = 10
    assert final_state.quantity == 10
    # 원가 수기 검증
    # buy10@100 -> cost=1000, avg=100
    # sell3@110 -> deduct 3*100=300, cost=700, qty=7, avg=100, pnl=(110-100)*3=30
    # buy5@90   -> cost=700+450=1150, qty=12, avg=1150/12
    # sell4@120 -> avg_before=1150/12, deduct=avg_before*4, qty=8
    # buy2@150  -> cost += 300
    avg_before_second_sell = 1150 / 12
    expected_cost_after_second_sell = 1150 - avg_before_second_sell * 4
    expected_cost_final = expected_cost_after_second_sell + 150 * 2
    assert final_state.cost_basis == pytest.approx(expected_cost_final)
    assert steps[1].realized_pnl == pytest.approx(30)
    assert steps[3].realized_pnl == pytest.approx((120 - avg_before_second_sell) * 4)
    assert final_state.last_buy_price == 150
    assert final_state.last_sell_price == 120


def test_07_oversell_raises():
    """7. 보유수량 초과 매도 -> OversellError"""
    state = apply_buy(PositionState(), price=100, quantity=10)
    with pytest.raises(OversellError) as excinfo:
        apply_sell(state, price=120, quantity=15)
    assert excinfo.value.requested_quantity == 15
    assert excinfo.value.available_quantity == 10


def test_07b_oversell_exact_boundary_is_allowed():
    """경계값: 보유수량과 정확히 같은 수량 매도는 허용되어야 함(초과가 아님)"""
    state = apply_buy(PositionState(), price=100, quantity=10)
    result = apply_sell(state, price=120, quantity=10)
    assert result.state.quantity == 0


def test_08_recalculate_after_trade_edit():
    """8. 거래 수정 후 재계산 — 과거 거래 하나를 수정하고 전체 재생하면 결과가 바뀌어야 함"""
    original_trades = [
        Trade("buy", price=100, quantity=10),
        Trade("sell", price=120, quantity=5),
    ]
    original_state, _ = replay_trades(original_trades)
    assert original_state.quantity == 5
    assert original_state.avg_price == 100

    # 첫 매수 수량을 10 -> 20으로 수정했다고 가정하고 전체 재생
    edited_trades = [
        Trade("buy", price=100, quantity=20),
        Trade("sell", price=120, quantity=5),
    ]
    edited_state, edited_steps = replay_trades(edited_trades)
    assert edited_state.quantity == 15
    assert edited_state.avg_price == 100
    assert edited_steps[1].realized_pnl == pytest.approx((120 - 100) * 5)


def test_09_recalculate_after_trade_delete():
    """9. 거래 삭제 후 재계산 — 중간 거래 하나를 빼고 재생하면 최종 상태가 바뀌어야 함"""
    trades_with_extra = [
        Trade("buy", price=100, quantity=10),
        Trade("buy", price=110, quantity=5),  # 이후 삭제될 거래
        Trade("sell", price=130, quantity=8),
    ]
    state_with_extra, _ = replay_trades(trades_with_extra)

    trades_after_delete = [
        Trade("buy", price=100, quantity=10),
        Trade("sell", price=130, quantity=8),
    ]
    state_after_delete, _ = replay_trades(trades_after_delete)

    assert state_with_extra.quantity != state_after_delete.quantity or \
        state_with_extra.avg_price != state_after_delete.avg_price
    # 삭제 후: buy10@100 -> sell8@130, 잔여 2주, 평균단가 100원 유지
    assert state_after_delete.quantity == 2
    assert state_after_delete.avg_price == 100


def test_10_same_day_multiple_trades_respect_given_order():
    """10. 같은 날 여러 거래 순서 — 엔진은 정렬을 하지 않고 입력된 순서 그대로 재생해야 함
    (실제 정렬은 executed_at/sequence_no 기준으로 호출자가 미리 함, 스펙 4.5)
    """
    same_day_trades = [
        Trade("buy", price=100, quantity=10),  # 오전 매수
        Trade("sell", price=105, quantity=4),  # 오후 부분매도
        Trade("buy", price=95, quantity=6),  # 마감 전 재매수
    ]
    state, steps = replay_trades(same_day_trades)
    # 순서대로: buy10@100(avg100) -> sell4@105(avg100 유지, qty6, cost600)
    #          -> buy6@95(cost600+570=1170, qty12, avg=97.5)
    assert state.quantity == 12
    assert state.avg_price == pytest.approx(97.5)
    assert steps[1].realized_pnl == pytest.approx((105 - 100) * 4)

    # 순서를 바꾸면 결과가 달라져야 함(엔진이 임의로 재정렬하지 않고 입력 순서를
    # 그대로 존중한다는 증거) — 최종 수량은 우연히 같지만(16매수-4매도=12),
    # 매도 시점의 평균단가가 달라 평균단가/원가는 달라져야 한다.
    reordered_trades = [
        Trade("buy", price=100, quantity=10),
        Trade("buy", price=95, quantity=6),
        Trade("sell", price=105, quantity=4),
    ]
    reordered_state, _ = replay_trades(reordered_trades)
    assert reordered_state.quantity == 12
    assert reordered_state.avg_price == pytest.approx(98.125)
    assert reordered_state.avg_price != state.avg_price
