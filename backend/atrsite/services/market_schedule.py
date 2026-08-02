"""거래일/장 상태 판정 -- 스펙 11.4.

주의: 아래 공휴일 테이블은 "최소한의 하드코딩 테이블"(프롬프트 3단계 지시)이며
고정일(신정/삼일절/어린이날/광복절/개천절/한글날/성탄절)만 담고 있다. 설날·
추석 같은 음력 연휴, 임시공휴일, 대체공휴일, 수능일 개장시간 변경은 아직
채워 넣지 않았다 -- 실제 배포 전에 한국거래소 공식 휴장일 공지를 보고
`MANUAL_HOLIDAYS`/`MANUAL_SCHEDULE_OVERRIDES`에 직접 추가해야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum

KST_REGULAR_OPEN = time(9, 0)
KST_REGULAR_CLOSE = time(15, 30)

# 고정일 공휴일만 (연도 무관하게 매년 반복되는 것들).
FIXED_HOLIDAYS_MM_DD = {
    (1, 1),   # 신정
    (3, 1),   # 삼일절
    (5, 5),   # 어린이날
    (6, 6),   # 현충일
    (8, 15),  # 광복절
    (10, 3),  # 개천절
    (10, 9),  # 한글절
    (12, 25),  # 성탄절
}

# 음력 연휴·임시공휴일·대체공휴일 등, 연도별로 직접 채워 넣어야 하는 휴장일.
# 형식: date(YYYY, MM, DD). 배포 전 한국거래소 공지 기준으로 갱신할 것.
MANUAL_HOLIDAYS: set[date] = set()

# 수능일 개장시간 변경, 임시 단축장 등 "휴장은 아니지만 정규 시간이 다른 날".
# 형식: {date(...): (open_time, close_time)}
MANUAL_SCHEDULE_OVERRIDES: dict[date, tuple[time, time]] = {}


class MarketPhase(str, Enum):
    PRE_MARKET = "PRE_MARKET"    # 개장 전 -- 토큰/거래일/종목 상태 확인 (11.3)
    REGULAR = "REGULAR"          # 정규장 -- 5분마다 현재가/당일고가 폴링
    POST_MARKET = "POST_MARKET"  # 마감 후 -- 확정 일봉 수집, ATR 갱신, 백업
    HOLIDAY = "HOLIDAY"          # 비거래일 -- 폴링하지 않음


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:  # 토(5)/일(6)
        return False
    if (d.month, d.day) in FIXED_HOLIDAYS_MM_DD:
        return False
    if d in MANUAL_HOLIDAYS:
        return False
    return True


def regular_hours_for(d: date) -> tuple[time, time]:
    return MANUAL_SCHEDULE_OVERRIDES.get(d, (KST_REGULAR_OPEN, KST_REGULAR_CLOSE))


def determine_phase(now: datetime) -> MarketPhase:
    """now는 한국 표준시(KST) 기준 naive datetime이라고 가정한다 (스펙 11.3/11.4).

    호출자(worker.py)가 이미 KST로 변환해서 넘겨야 한다 -- 이 함수 자체는
    타임존 변환을 하지 않는다(서버 로케일에 대한 가정을 최소화하기 위함).
    """
    d = now.date()
    if not is_trading_day(d):
        return MarketPhase.HOLIDAY
    open_time, close_time = regular_hours_for(d)
    t = now.time()
    if t < open_time:
        return MarketPhase.PRE_MARKET
    if t <= close_time:
        return MarketPhase.REGULAR
    return MarketPhase.POST_MARKET


@dataclass(frozen=True)
class PollingDecision:
    phase: MarketPhase
    should_poll_quotes: bool
    should_collect_daily_bars: bool
    reason: str


def decide_polling(now: datetime) -> PollingDecision:
    """스펙 11.3 초기 폴링 정책을 그대로 옮긴 결정표.

    - REGULAR: 현재가/당일고가 폴링만 (일봉은 아직 확정되지 않았으므로 수집 안 함)
    - POST_MARKET: 확정 일봉 수집(+ATR 갱신은 worker.py가 수행)
    - PRE_MARKET/HOLIDAY: 시세 폴링 없음
    """
    phase = determine_phase(now)
    if phase == MarketPhase.REGULAR:
        return PollingDecision(phase, True, False, "정규장 -- 5분 폴링")
    if phase == MarketPhase.POST_MARKET:
        return PollingDecision(phase, False, True, "마감 후 -- 확정 일봉 수집")
    if phase == MarketPhase.PRE_MARKET:
        return PollingDecision(phase, False, False, "개장 전 -- 토큰/거래일 확인만")
    return PollingDecision(phase, False, False, "비거래일")
