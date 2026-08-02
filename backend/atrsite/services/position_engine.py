"""
position_engine.py — 이동평균 원가법 포지션 계산 엔진

스펙 참조: strpro_design_spec.md 4.1(매도 후 재매수 평균단가 오류 수정 원칙),
           4.2(초과매도 차단), 4.4(마지막 매수가), 8.1(평균단가/보유원가 공식)

핵심 불변식
-----------
- 평균단가 = 보유원가 ÷ 보유수량 (매도는 이 값을 바꾸지 않는다 — 잔여 원가에서
  "매도 직전 평균단가 × 매도수량"만큼만 차감한다)
- 매도수량은 보유수량을 초과할 수 없다 (초과 시 OversellError)
- 전량 매도 후에는 수량/원가/평균단가를 정확히 0으로 정규화한다
  (부동소수점 잔여 오차 방지, 스펙 8.1)
- last_buy_price / last_sell_price / last_trade_price 를 각각 별도로 유지한다
  (스펙 4.4 — 다음 매수가는 반드시 last_buy_price 기준이어야 하고, 매도 체결이
  이 값을 절대 건드리면 안 된다)

이 모듈은 순수 계산 함수만 제공한다. DB/HTTP 등 I/O는 repositories/api 계층의 몫이다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Literal, Optional

# 부동소수점 비교/정규화 허용 오차. 수량·원가 비교에 공통 사용.
QUANTITY_EPSILON = 1e-9


class OversellError(ValueError):
    """매도수량이 현재 보유수량을 초과할 때 발생 (스펙 4.2).

    서비스/API 계층에서는 이 예외를 HTTP 409로 매핑하고, 화면에는
    `available_quantity`를 그대로 노출해 "최대 매도 가능 수량"을 표시하면 된다.
    """

    def __init__(self, requested_quantity: float, available_quantity: float):
        self.requested_quantity = requested_quantity
        self.available_quantity = available_quantity
        super().__init__(
            f"매도수량({requested_quantity})이 보유수량({available_quantity})을 초과합니다."
        )


class InvalidTradeError(ValueError):
    """가격·수량이 0 이하이거나 거래 유형을 알 수 없을 때 발생."""


TradeType = Literal["buy", "sell"]


@dataclass(frozen=True)
class PositionState:
    """특정 시점의 포지션 스냅샷. 불변(frozen) — 매 거래마다 새 인스턴스를 만든다."""

    quantity: float = 0.0
    cost_basis: float = 0.0  # 보유원가
    avg_price: float = 0.0  # 평균단가 = cost_basis / quantity (quantity==0이면 0)
    last_buy_price: Optional[float] = None
    last_sell_price: Optional[float] = None
    last_trade_price: Optional[float] = None

    @property
    def has_position(self) -> bool:
        return self.quantity > QUANTITY_EPSILON


@dataclass(frozen=True)
class Trade:
    """엔진에 입력되는 거래 1건. DB의 trades 테이블 행에 대응."""

    trade_type: TradeType
    price: float
    quantity: float
    fee: float = 0.0
    tax: float = 0.0
    # 정렬 키 (스펙 4.5) — 같은 날짜 여러 거래의 순서를 결정. 엔진 자체는 정렬을
    # 강제하지 않고 "입력된 순서대로" 재생한다 — 정렬은 호출자(repository)가
    # executed_at, sequence_no 기준으로 미리 해서 넘긴다.
    trade_id: Optional[str] = None


@dataclass(frozen=True)
class SellResult:
    state: PositionState
    realized_pnl: float
    proceeds: float
    deducted_cost: float
    avg_price_before_sell: float


@dataclass(frozen=True)
class ReplayStep:
    """replay_trades()가 반환하는 거래별 결과 1건."""

    trade: Trade
    state_after: PositionState
    realized_pnl: Optional[float]  # 매수 거래는 None


def _normalize_if_flat(quantity: float, cost_basis: float) -> tuple[float, float, float]:
    """수량이 사실상 0이면 (quantity, cost_basis, avg_price) 전부 정확히 0으로.

    스펙 8.1: "전량 매도 시 부동소수점 잔여 오차를 막기 위해 다음 값을 0으로
    정규화한다: 보유수량 / 보유원가 / 평균단가"
    """
    if quantity <= QUANTITY_EPSILON:
        return 0.0, 0.0, 0.0
    return quantity, cost_basis, cost_basis / quantity


def apply_buy(state: PositionState, price: float, quantity: float, fee: float = 0.0) -> PositionState:
    """매수 체결을 반영한 새 PositionState를 반환한다 (스펙 8.1 매수 공식).

    새 보유수량 = 기존 보유수량 + 매수수량
    새 보유원가 = 기존 보유원가 + 매수가 × 매수수량 + 매수수수료
    새 평균단가 = 새 보유원가 ÷ 새 보유수량
    """
    if price <= 0:
        raise InvalidTradeError(f"매수가는 0보다 커야 합니다: {price}")
    if quantity <= 0:
        raise InvalidTradeError(f"매수수량은 0보다 커야 합니다: {quantity}")
    if fee < 0:
        raise InvalidTradeError(f"수수료는 음수일 수 없습니다: {fee}")

    new_quantity = state.quantity + quantity
    new_cost_basis = state.cost_basis + price * quantity + fee
    new_quantity, new_cost_basis, new_avg_price = _normalize_if_flat(new_quantity, new_cost_basis)

    return replace(
        state,
        quantity=new_quantity,
        cost_basis=new_cost_basis,
        avg_price=new_avg_price,
        last_buy_price=price,
        last_trade_price=price,
    )


def apply_sell(
    state: PositionState, price: float, quantity: float, fee: float = 0.0, tax: float = 0.0
) -> SellResult:
    """매도 체결을 반영한다 (스펙 8.1 매도 공식, 4.1/4.2 수정 원칙).

    매도 직전 평균단가 = 기존 보유원가 ÷ 기존 보유수량
    차감 원가 = 매도 직전 평균단가 × 매도수량
    새 보유원가 = 기존 보유원가 - 차감 원가
    새 보유수량 = 기존 보유수량 - 매도수량
    실현손익 = 매도가 × 매도수량 - 차감 원가 - 수수료 - 세금

    핵심: 평균단가 자체는 매도로 인해 변하지 않는다(잔여분 평균단가는 매도 전과
    동일) — 이것이 4.1에서 지적한 기존 버그(runQty/runCost를 안 줄이던 것)의
    정반대 수정이다.
    """
    if price <= 0:
        raise InvalidTradeError(f"매도가는 0보다 커야 합니다: {price}")
    if quantity <= 0:
        raise InvalidTradeError(f"매도수량은 0보다 커야 합니다: {quantity}")
    if fee < 0 or tax < 0:
        raise InvalidTradeError("수수료·세금은 음수일 수 없습니다.")
    if quantity > state.quantity + QUANTITY_EPSILON:
        raise OversellError(requested_quantity=quantity, available_quantity=state.quantity)

    avg_price_before_sell = state.cost_basis / state.quantity if state.quantity > QUANTITY_EPSILON else 0.0
    deducted_cost = avg_price_before_sell * quantity
    proceeds = price * quantity
    realized_pnl = proceeds - deducted_cost - fee - tax

    raw_new_quantity = state.quantity - quantity
    raw_new_cost_basis = state.cost_basis - deducted_cost
    new_quantity, new_cost_basis, new_avg_price = _normalize_if_flat(raw_new_quantity, raw_new_cost_basis)

    new_state = replace(
        state,
        quantity=new_quantity,
        cost_basis=new_cost_basis,
        avg_price=new_avg_price,
        last_sell_price=price,
        last_trade_price=price,
    )

    return SellResult(
        state=new_state,
        realized_pnl=realized_pnl,
        proceeds=proceeds,
        deducted_cost=deducted_cost,
        avg_price_before_sell=avg_price_before_sell,
    )


def apply_trade(state: PositionState, trade: Trade) -> tuple[PositionState, Optional[float]]:
    """단일 진입점 — trade_type을 보고 apply_buy/apply_sell로 분기.

    반환: (새 상태, 실현손익 — 매수면 None)
    """
    if trade.trade_type == "buy":
        return apply_buy(state, trade.price, trade.quantity, trade.fee), None
    if trade.trade_type == "sell":
        result = apply_sell(state, trade.price, trade.quantity, trade.fee, trade.tax)
        return result.state, result.realized_pnl
    raise InvalidTradeError(f"알 수 없는 거래 유형: {trade.trade_type!r}")


def replay_trades(trades: List[Trade], initial_state: Optional[PositionState] = None) -> tuple[PositionState, List[ReplayStep]]:
    """거래 이력 전체를 시간순으로 재생해 최종 상태 + 단계별 결과를 반환한다.

    스펙 4.7 "포지션 구조 수정" 시 사용할 재계산 진입점:
    거래 유형/가격/수량/날짜가 바뀌거나 거래가 삭제되면, 그 시점 이후 전체
    거래를 이 함수로 다시 재생해서 보유수량/평균단가/보유원가/실현손익/
    마지막 매수가를 전부 다시 계산한다. 호출자는 trades를
    (executed_at, sequence_no) 기준으로 정렬해서 넘겨야 한다.
    """
    state = initial_state if initial_state is not None else PositionState()
    steps: List[ReplayStep] = []
    for trade in trades:
        state, realized_pnl = apply_trade(state, trade)
        steps.append(ReplayStep(trade=trade, state_after=state, realized_pnl=realized_pnl))
    return state, steps
