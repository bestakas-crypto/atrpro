"""
atr_engine.py — Wilder ATR(14) 계산 엔진, 확정 일봉 기반

스펙 참조: strpro_design_spec.md 8.5(ATR 정책), 8.6(atrAtExecution)

정책 (반드시 지킬 것)
--------------------
- Wilder 방식 평활화(단순 이동평균이 아님)만 사용한다.
- 입력은 항상 "확정된" 일봉이어야 한다 — 장중 미완성 봉을 넣지 않는다. 그 판단은
  이 모듈이 아니라 market_schedule / worker 쪽 책임이고, 이 모듈은 받은 봉을
  전부 확정 봉으로 간주하고 그대로 계산한다.
- 장중에는 이 함수를 다시 호출하지 않는다 — 전일까지 확정된 ATR을 그대로 쓴다
  (worker.py가 장 마감 후에만 재계산을 트리거).

Wilder 평활화 공식
-------------------
TR_t        = max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|)
seed ATR    = TR의 첫 period개 단순평균
ATR_t       = (ATR_{t-1} * (period - 1) + TR_t) / period   (seed 이후)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

DEFAULT_ATR_PERIOD = 14


class InsufficientDataError(ValueError):
    """ATR 계산에 필요한 확정 일봉 개수가 부족할 때 발생.

    Wilder ATR(period)은 TR을 period개 평균 내는 seed 단계가 필요하고, TR 계산
    자체가 전일종가를 요구하므로 최소 (period + 1)개의 연속 확정 일봉이 있어야
    첫 ATR 값을 하나라도 낼 수 있다.
    """

    def __init__(self, required: int, actual: int):
        self.required = required
        self.actual = actual
        super().__init__(f"ATR 계산에 최소 {required}개의 확정 일봉이 필요합니다 (현재 {actual}개).")


@dataclass(frozen=True)
class DailyBar:
    """ATR 계산에 필요한 최소 필드만 가진 확정 일봉 1건."""

    trade_date: str  # 'YYYY-MM-DD', 오름차순(과거 -> 최신) 정렬 전제
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class AtrPoint:
    trade_date: str
    atr: float


def true_range(bar: DailyBar, prev_close: float) -> float:
    """단일 TR(True Range) 계산."""
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(bar.low - prev_close),
    )


def compute_wilder_atr(bars: List[DailyBar], period: int = DEFAULT_ATR_PERIOD) -> List[AtrPoint]:
    """확정 일봉 리스트(오름차순)로부터 Wilder ATR 시계열 전체를 계산한다.

    반환 리스트의 첫 원소는 bars[period]에 해당하는 날짜(= period개의 TR로
    seed한 최초 ATR)이고, 이후 매 원소는 그 다음 거래일에 대응한다.
    """
    if period <= 0:
        raise ValueError(f"ATR 기간은 1 이상이어야 합니다: {period}")
    if len(bars) < period + 1:
        raise InsufficientDataError(required=period + 1, actual=len(bars))

    trs = [true_range(bars[i], bars[i - 1].close) for i in range(1, len(bars))]
    # trs[k] 는 bars[k+1] 시점의 TR.

    seed = sum(trs[:period]) / period
    points: List[AtrPoint] = [AtrPoint(trade_date=bars[period].trade_date, atr=seed)]

    prev_atr = seed
    for i in range(period, len(trs)):
        atr = (prev_atr * (period - 1) + trs[i]) / period
        points.append(AtrPoint(trade_date=bars[i + 1].trade_date, atr=atr))
        prev_atr = atr

    return points


def latest_atr(bars: List[DailyBar], period: int = DEFAULT_ATR_PERIOD) -> AtrPoint:
    """가장 최근 확정 ATR 한 점만 필요할 때 쓰는 편의 함수."""
    return compute_wilder_atr(bars, period)[-1]
