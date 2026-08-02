"""
signal_engine.py — 매매 기준선 계산 + 신호 우선순위 판정

스펙 참조: strpro_design_spec.md 4.3(포지션 상태 선행조건 표), 4.4(마지막 매수가
           기준), 8.2(다음 매수가), 8.3(익절가), 8.4(추적 손절선/레칫), 9.1~9.4
           (신호 상태 설계)

절대 원칙 재확인 (스펙 2절)
---------------------------
이 모듈은 "지금이 매매 타이밍이다"를 판정만 한다. 어떤 함수도 주문을 넣거나
수량/금액을 확정하지 않는다.

포지션 상태 × 신호 가능 여부 (스펙 4.3 표 그대로)
--------------------------------------------------
    상태          매수신호   익절신호   손절신호
    미보유          가능       불가       불가
    보유            가능       가능       가능
    전량매도완료     가능       불가       불가
    데이터 지연    (신규 신호 전체 금지)

"미보유"와 "전량매도완료"는 둘 다 quantity == 0 이므로 이 엔진 안에서는 동일하게
취급한다(quantity>0 여부 하나로 익절/손절 게이트를 건다). 두 상태를 UI 문구로
구분하는 것은 이 모듈이 아니라 상위 계층(포지션 이력 유무) 책임이다.

DELAYED 상태 처리 -- 스펙 4.3 표와 9.1 본문의 명시적 충돌 해소 (2026-08-02 결정)
--------------------------------------------------------------------------
스펙 4.3의 표는 "데이터 지연"일 때 매수/익절/손절 신규 신호를 전부 금지한다고
적혀 있다. 반면 9.1 본문은 "데이터 상태가 STALE, API_ERROR, INSUFFICIENT_DATA
이면 신규 거래 신호를 발송하지 않는다"라고 못박으면서 DataStatus.DELAYED는
이 목록에서 의도적으로 빠져 있다(DELAYED가 열거값에 존재하는데 굳이 나열
안 한 것은 실수라기보다 등급을 구분하려는 의도로 해석). 두 조항이 문자 그대로
충돌하므로, 이 구현은 **9.1의 명시적 목록을 따른다** — 즉 BLOCKED_DATA_STATUSES는
{STALE, API_ERROR, INSUFFICIENT_DATA}만 포함하고 DELAYED는 신호 판정을 계속
허용한다. 근거:
  1) 9.1은 4.3보다 훨씬 구체적으로(열거형까지) 규정하고 있어 더 나중/구체적인
     의도로 보는 것이 합리적이다.
  2) 스펙 17.3이 "0~7분 정상 / 7~15분 지연 / 15분 초과 오래된 데이터"로 등급을
     나눈 것과도 일치한다 -- 8분 지연됐다고 매수 기준 도달 알림 자체를 통째로
     막는 것은 실사용상 지나치게 보수적이다.
DELAYED는 신호 판정에는 관여하되, 화면/알림 문구에서는 "지연된 데이터 기준"
임을 별도로 표시해야 한다(17.3) -- 그 표시 책임은 이 모듈이 아니라 호출자다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .position_engine import QUANTITY_EPSILON

DEFAULT_HYSTERESIS_ATR_MULTIPLE = 0.1  # 스펙 9.4 초기값. 실사용 기록 보고 조정 예정.


# ---------------------------------------------------------------------------
# 상태 enum (스펙 9.1 — 거래 신호와 데이터 상태는 반드시 분리해서 관리한다)
# ---------------------------------------------------------------------------

class SignalStatus(str, Enum):
    NO_POSITION = "NO_POSITION"
    NEUTRAL = "NEUTRAL"
    BUY_TRIGGERED = "BUY_TRIGGERED"
    TAKE_PROFIT_TRIGGERED = "TAKE_PROFIT_TRIGGERED"
    STOP_TRIGGERED = "STOP_TRIGGERED"
    SELL_BOTH_TRIGGERED = "SELL_BOTH_TRIGGERED"


class DataStatus(str, Enum):
    FRESH = "FRESH"
    DELAYED = "DELAYED"
    STALE = "STALE"
    MARKET_CLOSED = "MARKET_CLOSED"
    API_ERROR = "API_ERROR"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


# 이 데이터 상태들에서는 "신규" 거래 신호를 만들지 않는다 (스펙 9.1).
# 주의: 기존 signal_state는 그대로 유지하고 새로 계산만 안 하는 것 — 상위 계층이
# "이 신호는 마지막 정상 데이터 기준" 배너를 같이 보여줘야 한다(스펙 17.3).
BLOCKED_DATA_STATUSES = frozenset({DataStatus.STALE, DataStatus.API_ERROR, DataStatus.INSUFFICIENT_DATA})


# ---------------------------------------------------------------------------
# 8.2 / 8.3 / 8.4 — 기준선 계산
# ---------------------------------------------------------------------------

def compute_next_buy_price(
    last_buy_price: Optional[float], atr: Optional[float], buy_multiple: float
) -> Optional[float]:
    """다음 매수가 = 마지막 매수가 - 확정 ATR × 매수배수 (스펙 4.4, 8.2).

    최초 진입 전(last_buy_price 없음) 또는 ATR 미확정 시 None을 반환한다 —
    호출자는 None을 "표시할 기준가 없음"으로 다뤄야 한다. 부분매도는
    last_buy_price에 영향을 주지 않으므로(포지션 엔진이 이미 보장) 이 함수도
    자동으로 매도가 기준선에 영향을 주지 않는다.
    """
    if last_buy_price is None or atr is None:
        return None
    return last_buy_price - atr * buy_multiple


def compute_take_profit_price(
    avg_price: float, atr: Optional[float], sell_multiple: float, quantity: float
) -> Optional[float]:
    """익절가 = 평균단가 + 확정 ATR × 매도배수, 보유수량 > 0일 때만 활성 (스펙 8.3)."""
    if quantity <= QUANTITY_EPSILON:
        return None
    if atr is None:
        return None
    return avg_price + atr * sell_multiple


def compute_trailing_stop_candidate(
    post_entry_high: Optional[float], atr: Optional[float], stop_multiple: float
) -> Optional[float]:
    """후보 손절선 = 보유 후 최고가 - 확정 ATR × 손절배수 (스펙 8.4)."""
    if post_entry_high is None or atr is None:
        return None
    return post_entry_high - atr * stop_multiple


def apply_trailing_stop_ratchet(
    existing_stop: Optional[float], candidate: Optional[float]
) -> Optional[float]:
    """레칫: 새 손절선 = MAX(기존 손절선, 후보 손절선) — 절대 내려가지 않는다.

    candidate가 없으면(ATR/최고가 미확정) 기존값을 그대로 유지한다.
    기존값이 없으면(이번 사이클 최초 계산) candidate를 그대로 채택한다.
    """
    if candidate is None:
        return existing_stop
    if existing_stop is None:
        return candidate
    return max(existing_stop, candidate)


def reset_trailing_stop_cycle() -> tuple[None, None]:
    """전량 매도 후 (post_entry_high, trailing_stop_line)을 초기화할 때 사용.

    스펙 8.4: "전량 매도 후 초기화 / 새 포지션 진입 시 새 사이클 시작".
    """
    return None, None


# ---------------------------------------------------------------------------
# 9.2 / 4.3 — 신호 판정
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalInput:
    quantity: float
    current_price: Optional[float]
    next_buy_price: Optional[float]
    take_profit_price: Optional[float]
    trailing_stop_price: Optional[float]
    data_status: DataStatus = DataStatus.FRESH
    # 히스테리시스 밴드(9.4) 계산에만 쓰인다 -- None이면 히스테리시스가 자동으로
    # 비활성화되고 예전과 동일한 단순 임계값 교차 판정으로 폴백한다(하위 호환).
    atr: Optional[float] = None


@dataclass(frozen=True)
class SignalResult:
    status: SignalStatus
    reason: str
    hit_stop: bool = False
    hit_profit: bool = False
    hit_buy: bool = False


def _hysteresis_active(
    *, current_price: float, threshold: Optional[float], direction: str,
    atr: Optional[float], was_active: bool, hysteresis_atr_multiple: float,
) -> bool:
    """진입/해제 기준가가 다른 "밴드" 판정 (스펙 9.4). direction:
      "below" -- 진입: price <= threshold, 해제: price >= threshold + 밴드 (매수/손절)
      "above" -- 진입: price >= threshold, 해제: price <= threshold - 밴드 (익절)
    was_active=False 또는 atr=None이면 밴드를 그릴 수 없으므로 단순 임계값
    교차 판정으로 정확히 폴백한다 -- 이 폴백이 곧 히스테리시스 도입 전 동작과
    100% 동일해서, 이 함수를 쓰지 않던 기존 호출부/테스트는 전혀 안 바뀐다.
    """
    if threshold is None:
        return False
    if direction == "below":
        if current_price <= threshold:
            return True
        if not was_active or atr is None:
            return False
        release_price = threshold + hysteresis_atr_multiple * atr
        return current_price < release_price
    else:  # "above"
        if current_price >= threshold:
            return True
        if not was_active or atr is None:
            return False
        release_price = threshold - hysteresis_atr_multiple * atr
        return current_price > release_price


def determine_signal(
    inp: SignalInput,
    *,
    was_hit_buy: bool = False,
    was_hit_profit: bool = False,
    was_hit_stop: bool = False,
    hysteresis_atr_multiple: float = DEFAULT_HYSTERESIS_ATR_MULTIPLE,
) -> SignalResult:
    """신호 우선순위(스펙 9.2)대로 하나의 상태만 반환한다.

    1. 손절선 + 익절가 동시 도달 -> SELL_BOTH_TRIGGERED
    2. 손절선 도달              -> STOP_TRIGGERED
    3. 익절가 도달              -> TAKE_PROFIT_TRIGGERED
    4. 매수가 도달              -> BUY_TRIGGERED
    5. 그 외                    -> NEUTRAL (보유) / NO_POSITION (미보유)

    포지션 상태 게이트(스펙 4.3): 익절·손절 신호는 quantity > 0 일 때만 검사한다.
    매수 신호는 포지션 상태와 무관하게 항상 검사 대상이다.

    was_hit_*/hysteresis_atr_multiple (스펙 9.4, 2026-08-02 추가): 직전 계산에서
    해당 신호가 이미 켜져 있었는지를 넘기면, 기준선을 살짝 반복 통과하는
    가격에도 신호가 매 폴링마다 깜빡이지 않고 밴드를 벗어날 때까지 유지된다.
    호출자(portfolio_service)가 이전 signal_state로부터 이 값들을 채워 넘긴다 --
    기본값(False)으로 두면 예전과 동일한 단순 임계값 판정이다.
    """
    has_position = inp.quantity > QUANTITY_EPSILON

    if inp.data_status in BLOCKED_DATA_STATUSES:
        return SignalResult(
            status=SignalStatus.NEUTRAL if has_position else SignalStatus.NO_POSITION,
            reason=f"데이터 상태({inp.data_status.value})로 신규 신호 판정을 보류합니다.",
        )

    if inp.current_price is None:
        return SignalResult(
            status=SignalStatus.NEUTRAL if has_position else SignalStatus.NO_POSITION,
            reason="현재가가 없어 신호를 계산할 수 없습니다.",
        )

    hit_stop = has_position and _hysteresis_active(
        current_price=inp.current_price, threshold=inp.trailing_stop_price, direction="below",
        atr=inp.atr, was_active=was_hit_stop, hysteresis_atr_multiple=hysteresis_atr_multiple,
    )
    hit_profit = has_position and _hysteresis_active(
        current_price=inp.current_price, threshold=inp.take_profit_price, direction="above",
        atr=inp.atr, was_active=was_hit_profit, hysteresis_atr_multiple=hysteresis_atr_multiple,
    )
    hit_buy = _hysteresis_active(
        current_price=inp.current_price, threshold=inp.next_buy_price, direction="below",
        atr=inp.atr, was_active=was_hit_buy, hysteresis_atr_multiple=hysteresis_atr_multiple,
    )

    if hit_stop and hit_profit:
        return SignalResult(SignalStatus.SELL_BOTH_TRIGGERED, "추적 손절선과 익절가에 동시 도달했습니다.", hit_stop, hit_profit, hit_buy)
    if hit_stop:
        return SignalResult(SignalStatus.STOP_TRIGGERED, "추적 손절선에 도달했습니다.", hit_stop, hit_profit, hit_buy)
    if hit_profit:
        return SignalResult(SignalStatus.TAKE_PROFIT_TRIGGERED, "익절가에 도달했습니다.", hit_stop, hit_profit, hit_buy)
    if hit_buy:
        return SignalResult(SignalStatus.BUY_TRIGGERED, "다음 매수가에 도달했습니다.", hit_stop, hit_profit, hit_buy)

    if not has_position:
        return SignalResult(SignalStatus.NO_POSITION, "보유 중인 수량이 없습니다.", hit_stop, hit_profit, hit_buy)
    return SignalResult(SignalStatus.NEUTRAL, "관망 구간입니다.", hit_stop, hit_profit, hit_buy)


# ---------------------------------------------------------------------------
# 9.3 — 중복 알림 방지
# ---------------------------------------------------------------------------

def should_emit_event(previous_status: Optional[SignalStatus], new_status: SignalStatus) -> bool:
    """이전 신호 상태와 다를 때만 신규 이벤트를 만든다 (스펙 9.3).

    같은 상태가 반복 폴링될 때마다 True를 반환하면 안 된다 — 상위 계층(worker)이
    signal_events/notification_outbox를 매번 새로 만드는 걸 막는 게이트.
    """
    return previous_status != new_status


# ---------------------------------------------------------------------------
# 9.4 — 기준선 반복 통과 방지(히스테리시스, 초기값)
# ---------------------------------------------------------------------------

def is_buy_signal_active(
    current_price: float,
    next_buy_price: float,
    atr: Optional[float],
    was_active: bool,
    hysteresis_atr_multiple: float = DEFAULT_HYSTERESIS_ATR_MULTIPLE,
) -> bool:
    """매수 신호 on/off를 히스테리시스 밴드로 판정한다 (스펙 9.4 초기 예시).

    진입: 현재가 <= 매수 기준가
    해제: 현재가 >= 매수 기준가 + hysteresis_atr_multiple * ATR

    ATR이 없으면 밴드를 적용할 수 없으므로 단순 임계값 비교로 폴백한다.
    이 함수는 독립적으로도 쓸 수 있는 단독 유틸리티다. determine_signal()은
    내부적으로 동일한 로직(_hysteresis_active)을 매수/익절/손절 세 가지 모두에
    적용하므로, 이 함수를 직접 호출하지 않아도 신호 판정에는 이미 반영된다.
    """
    return _hysteresis_active(
        current_price=current_price, threshold=next_buy_price, direction="below",
        atr=atr, was_active=was_active, hysteresis_atr_multiple=hysteresis_atr_multiple,
    )
