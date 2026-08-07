"""사용자 매매계획(트리거 감시) 순수함수 엔진 -- Phase 1, TRAIL만 지원.

설계 근거: PLAN_trade_trigger_v1.md, 2026-08-07 확정 프롬프트.

이 모듈은 signal_engine.py와 동일한 스타일(DB/HTTP를 전혀 모르는 순수함수)을
따른다. 여기서 계산하는 신호는 기존 ATR 매수/손절/익절 신호(signal_engine.py)
와 완전히 독립적인 별도 트랙이다 -- 서로 영향을 주지 않는다.

핵심 원칙(전부 프롬프트에서 확정됨, 임의로 바꾸지 않음):
  - 사용자가 정한 트리거·하락률·매도비율을 프로그램이 ATR 등으로 대체하지 않는다.
  - 계획 확정/트리거 도달 이전의 과거 최고가를 소급 사용하지 않는다.
  - 최고가와 매도선은 오르기만 하고 절대 내려가지 않는다(레칫).
  - 장중 이탈은 예비 알림만, confirm_mode=CLOSE는 확정 종가로만 tier 발동.
  - 데이터 장애 시 기존 상태를 그대로 보존하고 아무 신호도 만들지 않는다
    (2026-08-06 신호소실 방지 수정과 동일 원칙).
  - 갭으로 여러 tier를 동시에 통과하면 해당 tier를 전부 같은 평가에서 발동한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

QUANTITY_EPSILON = 1e-9
DEFAULT_APPROACH_THRESHOLD_PCT = 2.0
FINAL_TIER_CUMULATIVE_EPSILON = 0.01  # 부동소수 누적 오차 흡수(99.99% 이상이면 100%로 간주)

TERMINAL_STATUSES = ("COMPLETED", "CANCELLED")


# ---------------------------------------------------------------------------
# 입력/출력 자료구조
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlanState:
    """trade_plans 테이블 한 행의 평가에 필요한 최소 필드."""

    lifecycle_status: str  # ARMED / ACTIVE / PARTIALLY_FIRED / COMPLETED / CANCELLED
    trigger_price: Optional[float]
    trigger_direction: Optional[str]  # ABOVE / BELOW
    trigger_activated_at: Optional[str]
    peak_price_since_trigger: Optional[float]
    confirm_mode: str  # CLOSE / INTRADAY
    approach_notified_at: Optional[str]


@dataclass(frozen=True)
class TierState:
    tier_order: int
    pullback_pct: float
    sell_pct: float
    fired_at: Optional[str]


@dataclass(frozen=True)
class PlanObservation:
    """한 번의 평가에 쓰이는 시세 관측치. DB/HTTP 조회는 호출자(오케스트레이션
    레이어)가 미리 끝내고 값만 여기 채워 넣는다."""

    intraday_price: Optional[float]
    is_data_valid: bool  # STALE/API_ERROR/가격<=0이면 False
    confirmed_close: Optional[float] = None  # 오늘 거래일 확정 종가(없으면 None)
    atr: Optional[float] = None


@dataclass(frozen=True)
class TierEvent:
    tier_order: int
    kind: str  # 'PREVIEW' | 'FIRED'
    pullback_pct: float
    sell_pct: float
    reference_price: float
    peak_price: float


@dataclass(frozen=True)
class TierUpdate:
    """FIRED 이벤트가 발생한 tier에 실제로 반영할 값(fired_at 등)."""

    tier_order: int
    fired_at: str
    fired_peak_price: float
    fired_reference_price: float


@dataclass(frozen=True)
class PlanEvaluation:
    new_lifecycle_status: str
    new_trigger_activated_at: Optional[str]
    new_peak_price: Optional[float]
    new_approach_notified_at: Optional[str]
    events: tuple  # 'TRIGGER_APPROACH' | 'TRIGGER_REACHED' | 'DATA_STALE' | TierEvent
    tier_updates: tuple  # TierUpdate, ...


def _unchanged(plan: PlanState, events: tuple = ()) -> PlanEvaluation:
    """아무 상태도 안 바뀐 평가 결과(데이터장애/터미널상태 등)."""
    return PlanEvaluation(
        plan.lifecycle_status,
        plan.trigger_activated_at,
        plan.peak_price_since_trigger,
        plan.approach_notified_at,
        events,
        (),
    )


# ---------------------------------------------------------------------------
# 트리거 판정
# ---------------------------------------------------------------------------

def is_trigger_reached(direction: str, price: float, trigger_price: float) -> bool:
    if direction == "ABOVE":
        return price >= trigger_price
    if direction == "BELOW":
        return price <= trigger_price
    raise ValueError(f"알 수 없는 trigger_direction: {direction}")


def is_approaching(
    direction: str, price: float, trigger_price: float, atr: Optional[float],
) -> bool:
    """접근 알림 조건: 트리거까지 거리 <= max(2%, ATR%). ATR 없으면 2%만 쓴다.
    아직 트리거를 통과하지 않은 방향에서만 True가 될 수 있다."""
    if trigger_price is None or trigger_price <= 0 or price <= 0:
        return False
    if is_trigger_reached(direction, price, trigger_price):
        return False  # 이미 도달/통과했으면 "접근"이 아니라 활성화 대상
    atr_pct = (atr / price * 100) if (atr is not None and atr > 0) else None
    threshold_pct = max(DEFAULT_APPROACH_THRESHOLD_PCT, atr_pct) if atr_pct is not None else DEFAULT_APPROACH_THRESHOLD_PCT
    distance_pct = abs(trigger_price - price) / trigger_price * 100
    return distance_pct <= threshold_pct


# ---------------------------------------------------------------------------
# 핵심 평가 함수
# ---------------------------------------------------------------------------

def evaluate_plan(
    plan: PlanState,
    tiers: list[TierState],
    obs: PlanObservation,
    *,
    now_iso: str,
) -> PlanEvaluation:
    """계획 하나를 관측치 하나로 평가한다. 여러 종목이 연결된 계획이라도
    가격은 대표 종목 하나만 조회해서 이 함수를 한 번만 호출한다(계좌별
    매도수량 계산은 이 함수의 책임이 아니다 -- compute_tier_sell_quantities 참고).
    """
    if plan.lifecycle_status in TERMINAL_STATUSES:
        return _unchanged(plan)

    if not obs.is_data_valid or obs.intraday_price is None or obs.intraday_price <= 0:
        # 2026-08-06 신호소실 방지와 동일 원칙: 장애 시 상태를 절대 바꾸지 않는다.
        return _unchanged(plan, events=("DATA_STALE",))

    price = obs.intraday_price

    if plan.lifecycle_status == "ARMED":
        return _evaluate_armed(plan, price, obs, now_iso)

    if plan.lifecycle_status in ("ACTIVE", "PARTIALLY_FIRED"):
        return _evaluate_active(plan, tiers, price, obs, now_iso)

    # 알 수 없는 상태값 -- 방어적으로 아무것도 안 바꿈(임의 추측 금지).
    return _unchanged(plan)


def _evaluate_armed(
    plan: PlanState, price: float, obs: PlanObservation, now_iso: str,
) -> PlanEvaluation:
    if is_trigger_reached(plan.trigger_direction, price, plan.trigger_price):
        # 활성화 순간의 관측가격을 최초 peak로 삼는다 -- 이전의 당일 고가/
        # 과거 일봉 고가/post_entry_high_price는 여기서 절대 참조하지 않는다.
        return PlanEvaluation(
            new_lifecycle_status="ACTIVE",
            new_trigger_activated_at=now_iso,
            new_peak_price=price,
            new_approach_notified_at=plan.approach_notified_at,
            events=("TRIGGER_REACHED",),
            tier_updates=(),
        )

    if plan.approach_notified_at is None and is_approaching(
        plan.trigger_direction, price, plan.trigger_price, obs.atr
    ):
        return PlanEvaluation(
            new_lifecycle_status="ARMED",
            new_trigger_activated_at=None,
            new_peak_price=None,
            new_approach_notified_at=now_iso,
            events=("TRIGGER_APPROACH",),
            tier_updates=(),
        )

    return _unchanged(plan)


def _evaluate_active(
    plan: PlanState, tiers: list[TierState], price: float, obs: PlanObservation, now_iso: str,
) -> PlanEvaluation:
    prev_peak = plan.peak_price_since_trigger
    new_peak = price if (prev_peak is None or price > prev_peak) else prev_peak

    pending = [t for t in tiers if t.fired_at is None]
    pending_sorted = sorted(pending, key=lambda t: t.tier_order)

    events: list = []
    tier_updates: list[TierUpdate] = []

    for tier in pending_sorted:
        tier_line = new_peak * (1 - tier.pullback_pct / 100)
        if price > tier_line:
            continue  # 아직 이 tier 선에 안 닿음

        if plan.confirm_mode == "CLOSE":
            if obs.confirmed_close is not None and obs.confirmed_close <= tier_line:
                events.append(TierEvent(tier.tier_order, "FIRED", tier.pullback_pct, tier.sell_pct, obs.confirmed_close, new_peak))
                tier_updates.append(TierUpdate(tier.tier_order, now_iso, new_peak, obs.confirmed_close))
            else:
                # 장중 이탈만 확인됨(또는 오늘 종가 미확정) -- 예비알림만, fired_at 기록 안 함
                events.append(TierEvent(tier.tier_order, "PREVIEW", tier.pullback_pct, tier.sell_pct, price, new_peak))
        else:  # INTRADAY -- 장중가로 즉시 발동
            events.append(TierEvent(tier.tier_order, "FIRED", tier.pullback_pct, tier.sell_pct, price, new_peak))
            tier_updates.append(TierUpdate(tier.tier_order, now_iso, new_peak, price))

    if tier_updates:
        # 완료 여부는 "tier 행이 남아있는가"가 아니라 "발동된 sell_pct 누적이
        # 100%에 도달했는가"로 판단한다. QQQ처럼 tier가 1개(40%)뿐이고 2차가
        # 아직 계획에 없는 경우, 그 1개가 발동해도 40%<100%이므로
        # PARTIALLY_FIRED로 남아 잔여 60%를 계속 보유한 것으로 취급해야 한다.
        fired_orders_before = {t.tier_order for t in tiers if t.fired_at is not None}
        fired_orders_now = {u.tier_order for u in tier_updates}
        all_fired_orders = fired_orders_before | fired_orders_now
        total_fired_pct = sum(t.sell_pct for t in tiers if t.tier_order in all_fired_orders)
        new_status = "COMPLETED" if total_fired_pct >= 100 - FINAL_TIER_CUMULATIVE_EPSILON else "PARTIALLY_FIRED"
    else:
        new_status = plan.lifecycle_status

    return PlanEvaluation(
        new_lifecycle_status=new_status,
        new_trigger_activated_at=plan.trigger_activated_at,
        new_peak_price=new_peak,
        new_approach_notified_at=plan.approach_notified_at,
        events=tuple(events),
        tier_updates=tuple(tier_updates),
    )


# ---------------------------------------------------------------------------
# 계좌별 매도수량 계산 (baseline_quantity 기준, 실시간 보유수량 사용 안 함)
# ---------------------------------------------------------------------------

def compute_tier_sell_quantities(
    baseline_quantities: dict[str, float],
    sell_pct: float,
    *,
    is_final_tier: bool,
    already_recommended: Optional[dict[str, float]] = None,
) -> dict[str, int]:
    """instrument_id -> 이번 tier 매도 권고수량(정수).

    - 일반 tier: 최대잔여법(largest remainder)으로 반올림해서, 개별 계좌
      반올림 오차가 쌓여도 총 권고수량이 baseline 합계 x sell_pct의 반올림값과
      정확히 일치하게 한다.
    - 마지막 tier(누적 100%): 반올림 대신 각 계좌 baseline의 잔여 전량을 그대로
      권고해서 "전량 청산"이 확실히 0으로 끝나게 한다.

    baseline_quantity는 계획 확정 시점 스냅샷이다 -- 이후 적립매수 등으로
    실보유수량이 늘어도 이 함수의 입력에는 반영하지 않는다(호출자가 절대
    실시간 position_state.quantity를 여기 넣으면 안 된다).
    """
    already_recommended = already_recommended or {}

    if is_final_tier:
        return {
            iid: int(round(qty - already_recommended.get(iid, 0.0)))
            for iid, qty in baseline_quantities.items()
        }

    raw = {iid: qty * sell_pct / 100 for iid, qty in baseline_quantities.items()}
    floors = {iid: int(v) for iid, v in raw.items()}
    total_target = int(round(sum(baseline_quantities.values()) * sell_pct / 100))
    remainder = total_target - sum(floors.values())

    if remainder > 0:
        order = sorted(raw.keys(), key=lambda iid: raw[iid] - floors[iid], reverse=True)
        result = dict(floors)
        for iid in order[:remainder]:
            result[iid] += 1
        return result

    return floors


def is_cumulative_final_tier(tiers: list[TierState], tier_order: int) -> bool:
    """이 tier까지의 누적 sell_pct가 100%(오차 허용)에 도달하는지."""
    cumulative = sum(t.sell_pct for t in tiers if t.tier_order <= tier_order)
    return cumulative >= 100 - FINAL_TIER_CUMULATIVE_EPSILON
