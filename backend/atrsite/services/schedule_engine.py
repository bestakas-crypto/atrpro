"""backend/atrsite/services/schedule_engine.py -- 투자 스케줄(v1.2) 순수 계산 엔진.

DB/HTTP에 전혀 의존하지 않는 순수 함수 모음이다(signal_engine.py/market_schedule.py와
같은 위치·같은 성격). 3가지 책임만 진다:

  1. 반복 규칙(ONCE/DAILY/WEEKLY/BIWEEKLY/MONTHLY/YEARLY/CUSTOM) -> 날짜 목록 확장
  2. 시장별 휴장일 보정 -- KRX는 실거래에 이미 쓰이는 market_schedule.is_trading_day를
     그대로 재사용(주말+고정공휴일+MANUAL_HOLIDAYS). NYSE_NASDAQ/JPX는 검증된 휴장일
     데이터가 이 프로젝트에 전혀 없으므로 주말만 판정하고, 매 회차마다
     needs_confirmation=True를 무조건 세운다 -- "확인 못했으면 날짜를 추측해서
     확정하지 않는다"는 원칙을 지키기 위함(평일이라고 자동으로 개장이라 단정하지 않음).
  3. 예산 배분 -- 총액을 회차/종목 비율로 나눌 때 나머지는 항상 마지막 항목에 몰아준다
     (스펙 예시: 74,918,796원 / 9회 -> 8회는 8,324,310원, 마지막 1회만 8,324,316원).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .market_schedule import is_trading_day as _krx_is_trading_day

RECURRENCE_TYPES = ("ONCE", "DAILY", "WEEKLY", "BIWEEKLY", "MONTHLY", "YEARLY", "CUSTOM")
HOLIDAY_POLICIES = ("PREVIOUS_BUSINESS_DAY", "NEXT_BUSINESS_DAY", "USER_CONFIRM", "NO_ADJUSTMENT")
MARKETS = ("KRX", "NYSE_NASDAQ", "JPX", "NONE")

# 안전장치 -- occurrence_count/end_date를 둘 다 안 넘겼거나 잘못 넘겨서 무한/과도
# 생성되는 걸 막는다. 하루 단위 5년치(약 1826일)보다 넉넉하게 잡는다.
MAX_OCCURRENCES = 2000
_HOLIDAY_ADJUST_GUARD_DAYS = 30


class ScheduleEngineError(ValueError):
    """엔진 입력 검증 오류 -- 호출자(리포지토리/API)가 400으로 변환한다."""


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _occurrence_date_at(start_date: date, recurrence_type: str, interval: int, idx: int) -> date:
    if recurrence_type == "ONCE":
        return start_date
    if recurrence_type == "DAILY":
        return start_date + timedelta(days=idx * interval)
    if recurrence_type == "WEEKLY":
        return start_date + timedelta(days=idx * interval * 7)
    if recurrence_type == "BIWEEKLY":
        return start_date + timedelta(days=idx * interval * 14)
    if recurrence_type == "MONTHLY":
        return _add_months(start_date, idx * interval)
    if recurrence_type == "YEARLY":
        return _add_months(start_date, idx * interval * 12)
    raise ScheduleEngineError(f"지원하지 않는 recurrence_type: {recurrence_type}")


def generate_occurrence_dates(
    *,
    start_date: date,
    recurrence_type: str,
    interval: int = 1,
    end_date: Optional[date] = None,
    occurrence_count: Optional[int] = None,
    custom_dates: Optional[list[date]] = None,
) -> list[date]:
    """반복 규칙을 실제 날짜 목록으로 확장한다.

    ONCE/CUSTOM을 제외하면 occurrence_count와 end_date 중 최소 하나는 필수다
    (둘 다 없으면 무한정 생성될 수 있어 명시적으로 막는다 -- MAX_OCCURRENCES 안전장치도
    병행한다).
    """
    if recurrence_type not in RECURRENCE_TYPES:
        raise ScheduleEngineError(f"지원하지 않는 recurrence_type: {recurrence_type}")

    if recurrence_type == "CUSTOM":
        if not custom_dates:
            raise ScheduleEngineError("CUSTOM 반복은 custom_dates가 최소 1개 필요합니다")
        return sorted(custom_dates)

    if recurrence_type == "ONCE":
        return [start_date]

    if interval < 1:
        raise ScheduleEngineError("interval은 1 이상이어야 합니다")
    if occurrence_count is None and end_date is None:
        raise ScheduleEngineError("occurrence_count 또는 end_date 중 하나는 반드시 지정해야 합니다")
    if occurrence_count is not None and occurrence_count < 1:
        raise ScheduleEngineError("occurrence_count는 1 이상이어야 합니다")

    dates: list[date] = []
    idx = 0
    while True:
        if occurrence_count is not None and idx >= occurrence_count:
            break
        if idx >= MAX_OCCURRENCES:
            raise ScheduleEngineError(f"생성 가능한 회차 수({MAX_OCCURRENCES})를 초과했습니다 -- 기간/종료일을 확인하세요")
        d = _occurrence_date_at(start_date, recurrence_type, interval, idx)
        if end_date is not None and d > end_date:
            break
        dates.append(d)
        idx += 1
    return dates


@dataclass(frozen=True)
class AdjustedDate:
    original: date
    adjusted: date
    needs_confirmation: bool
    note: str


def adjust_for_holiday(d: date, *, market: str, holiday_policy: str) -> AdjustedDate:
    """휴장일 정책에 따라 원래 날짜를 보정한다.

    - market=NONE 또는 holiday_policy=NO_ADJUSTMENT: 보정 없음, 확인 불필요.
    - market=KRX: market_schedule.is_trading_day(주말+고정공휴일+MANUAL_HOLIDAYS)로
      실제 판정 가능 -- 확인 불필요(단, 이 함수 자체가 음력 연휴 등 MANUAL_HOLIDAYS에
      아직 채워지지 않은 날은 개장으로 오판할 수 있음 -- 이는 실거래 폴링 로직도 이미
      안고 있는 기존 한계이며 배포 전 갱신 대상임. 새로 만드는 한계가 아니다).
    - market=NYSE_NASDAQ/JPX: 이 프로젝트엔 검증된 휴장일 데이터가 전혀 없다. 주말만
      판정 가능하므로 주말 보정은 하되, 평일이어도 실제 휴장일 수 있어 매 회차마다
      needs_confirmation=True를 무조건 세운다 -- "모르면 확정하지 않는다" 원칙.
    - holiday_policy=USER_CONFIRM: 휴장 여부와 무관하게 항상 사용자 확인을 요구하고
      날짜 자체는 보정하지 않는다(원본 그대로 두고 "확인 필요"만 표시).
    """
    if market not in MARKETS:
        raise ScheduleEngineError(f"지원하지 않는 market: {market}")
    if holiday_policy not in HOLIDAY_POLICIES:
        raise ScheduleEngineError(f"지원하지 않는 holiday_policy: {holiday_policy}")

    if market == "NONE" or holiday_policy == "NO_ADJUSTMENT":
        return AdjustedDate(original=d, adjusted=d, needs_confirmation=False, note="")

    if market == "KRX":
        is_open = _krx_is_trading_day
        always_confirm = False
        base_note = ""
    else:
        is_open = lambda dd: dd.weekday() < 5  # noqa: E731 -- 주말 여부만 판정 가능
        always_confirm = True
        base_note = f"{market} 공휴일 데이터 미확보 -- 주말만 자동 보정됨, 실제 휴장 여부 확인 필요"

    if holiday_policy == "USER_CONFIRM":
        return AdjustedDate(original=d, adjusted=d, needs_confirmation=True, note=base_note or "사용자 확인 필요(USER_CONFIRM 정책)")

    step = timedelta(days=-1 if holiday_policy == "PREVIOUS_BUSINESS_DAY" else 1)
    adjusted = d
    guard = 0
    while not is_open(adjusted) and guard < _HOLIDAY_ADJUST_GUARD_DAYS:
        adjusted += step
        guard += 1

    return AdjustedDate(original=d, adjusted=adjusted, needs_confirmation=always_confirm, note=base_note)


def _smallest_unit(currency: str) -> float:
    """CSV 내보내기(withdrawals.py)와 동일한 관례 -- KRW/JPY는 정수 단위, 그 외는 센트 단위."""
    return 1.0 if currency in ("KRW", "JPY") else 0.01


def allocate_evenly(total_amount: float, count: int, *, currency: str = "KRW") -> list[float]:
    """총액을 count개로 균등 분할하고, 나머지는 마지막 항목에 몰아준다."""
    if count < 1:
        raise ScheduleEngineError("count는 1 이상이어야 합니다")
    unit = _smallest_unit(currency)
    total_units = round(total_amount / unit)
    base = total_units // count
    remainder = total_units - base * count
    amounts = [base] * count
    amounts[-1] = base + remainder
    return [round(a * unit, 2) for a in amounts]


def allocate_by_ratio(amount: float, ratios: list[float], *, currency: str = "KRW") -> list[float]:
    """금액을 비율(합계가 반드시 100일 필요는 없음 -- 상대 가중치로 취급)대로 분할하고,
    반올림 나머지는 마지막 항목에 몰아준다."""
    if not ratios:
        raise ScheduleEngineError("ratios는 최소 1개 필요합니다")
    weight_sum = sum(ratios)
    if weight_sum <= 0:
        raise ScheduleEngineError("ratios 합계는 0보다 커야 합니다")
    unit = _smallest_unit(currency)
    total_units = round(amount / unit)
    raw = [total_units * r / weight_sum for r in ratios]
    floors = [int(x) for x in raw]
    remainder = total_units - sum(floors)
    floors[-1] += remainder
    return [round(f * unit, 2) for f in floors]
