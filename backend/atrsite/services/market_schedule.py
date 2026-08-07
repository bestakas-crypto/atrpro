"""거래일/장 상태 판정 -- 스펙 11.4.

주의: 아래 공휴일 테이블은 "최소한의 하드코딩 테이블"(프롬프트 3단계 지시)이며
고정일(신정/삼일절/어린이날/광복절/개천절/한글날/성탄절)만 담고 있다. 설날·
추석 같은 음력 연휴, 임시공휴일, 대체공휴일, 수능일 개장시간 변경은 아직
채워 넣지 않았다 -- 실제 배포 전에 한국거래소 공식 휴장일 공지를 보고
`MANUAL_HOLIDAYS`/`MANUAL_SCHEDULE_OVERRIDES`에 직접 추가해야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from .signal_engine import DataStatus

KST_REGULAR_OPEN = time(9, 0)
KST_REGULAR_CLOSE = time(15, 30)

# 스펙 17.3 -- "5분 폴링 기준 예시" 신선도 등급표.
FRESH_MAX_MINUTES = 7
DELAYED_MAX_MINUTES = 15
CONSECUTIVE_FAILURE_THRESHOLD = 3

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


# ---------------------------------------------------------------------------
# 미국장(NASDAQ/NYSE/AMEX) 폴링 -- 2026-08-07 매매계획(트리거 감시) Phase 1
# 추가. 위 KRX 로직과 완전히 독립적으로 동작한다(worker.py가 두 결정을
# 각각 계산해서 각자의 종목군만 폴링한다).
#
# 확인된 사실: 이 프로젝트에는 지금까지 미국장 시간대 판정 자체가 전혀
# 없었다(worker.py의 유일한 폴링 게이트가 KRX 09:00~15:30 뿐이었음) --
# 즉 QQQ/NVDA/QLD 등은 한국 야간(=미국 정규장 시간)에 자동 폴링된 적이
# 없고 수동 "주가 갱신" 버튼에만 의존했다. 이 절이 그 공백을 메운다.
#
# 서머타임: 직접 날짜를 계산하지 않고 표준 라이브러리 zoneinfo(IANA tz DB
# "America/New_York")에 위임한다 -- DST 전환일을 손으로 계산하는 것보다
# 훨씬 안전하다("정교한 달력을 발명하지 말라"는 지시에 따른 최소·안전 구현).
#
# 남은 제약(명시): 미국 공휴일 중 "몇째 요일" 규칙(추수감사절=11월 넷째
# 목요일, 노동절=9월 첫째 월요일, MLK Day, 대통령의 날, 성금요일, 메모리얼
# 데이, 준틴스 등)은 계산 로직이 없고 MANUAL_US_HOLIDAYS가 비어있다 --
# 이 날짜들에 실제로는 휴장인데 이 코드는 PRE_MARKET/POST_MARKET으로
# 오판할 수 있다(폴링 자체는 안 되지만 phase 값이 부정확할 수 있다는
# 뜻). 배포 전 또는 운영 중 NYSE 공식 휴장일 캘린더를 보고 채워야 한다.
US_EASTERN = ZoneInfo("America/New_York")

US_REGULAR_OPEN_ET = time(9, 30)
US_REGULAR_CLOSE_ET = time(16, 0)

# 고정일 공휴일만(연도 무관 반복). "몇째 요일" 규칙 공휴일은 위 주석 참고.
US_FIXED_HOLIDAYS_MM_DD = {
    (1, 1),   # New Year's Day
    (6, 19),  # Juneteenth (2021년 연방공휴일 지정, 고정일이라 여기 포함 가능)
    (7, 4),   # Independence Day
    (12, 25),  # Christmas
}

# 추수감사절/노동절/MLK Day 등 "몇째 요일" 규칙 + 임시휴장은 직접 채워야
# 한다(형식: date(YYYY, MM, DD)).
MANUAL_US_HOLIDAYS: set[date] = set()

US_MARKET_CODES = {"NAS", "NYS", "AMS"}


def is_us_listed(kis_market: Optional[str]) -> bool:
    return bool(kis_market) and kis_market.upper() in US_MARKET_CODES


def is_us_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if (d.month, d.day) in US_FIXED_HOLIDAYS_MM_DD:
        return False
    if d in MANUAL_US_HOLIDAYS:
        return False
    return True


def determine_us_market_phase(now: datetime) -> MarketPhase:
    """now는 timezone-aware(권장) 또는 naive(UTC로 간주)여도 된다 -- 내부에서
    zoneinfo로 America/New_York 현지시각으로 변환해서 판정한다."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    et_now = now.astimezone(US_EASTERN)
    d = et_now.date()
    if not is_us_trading_day(d):
        return MarketPhase.HOLIDAY
    t = et_now.time()
    if t < US_REGULAR_OPEN_ET:
        return MarketPhase.PRE_MARKET
    if t <= US_REGULAR_CLOSE_ET:
        return MarketPhase.REGULAR
    return MarketPhase.POST_MARKET


def decide_us_polling(now: datetime) -> PollingDecision:
    """KRX decide_polling()과 동일한 결정표를 미국장 시간대에 적용한다."""
    phase = determine_us_market_phase(now)
    if phase == MarketPhase.REGULAR:
        return PollingDecision(phase, True, False, "미국 정규장 -- 5분 폴링")
    if phase == MarketPhase.POST_MARKET:
        return PollingDecision(phase, False, True, "미국 마감 후 -- 확정 일봉 수집")
    if phase == MarketPhase.PRE_MARKET:
        return PollingDecision(phase, False, False, "미국 개장 전")
    return PollingDecision(phase, False, False, "미국 비거래일")


def compute_data_status(
    *, quoted_at: Optional[str], now: datetime, consecutive_failures: int = 0,
) -> DataStatus:
    """KIS 소스 시세의 신선도를 "지금" 기준으로 매번 다시 계산한다 (스펙 17.3).

        0~7분 경과   -> FRESH
        7~15분 경과  -> DELAYED
        15분 초과    -> STALE
        연속 3회 조회 실패 -> API_ERROR (경과시간과 무관하게 우선)

    quoted_at가 없으면(아직 한 번도 성공한 조회가 없음) INSUFFICIENT_DATA.
    수동 입력(MANUAL_OVERRIDE) 시세는 이 함수를 거치지 않는다 -- 호출자가
    source == "manual"이면 저장된 data_status를 그대로 쓰고 이 함수를 부르지
    않아야 한다(사용자가 방금 입력한 값을 "오래된 데이터"로 재판정하면 안 됨).
    """
    if quoted_at is None:
        return DataStatus.INSUFFICIENT_DATA
    if consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
        return DataStatus.API_ERROR

    quoted_dt = datetime.fromisoformat(quoted_at)
    if quoted_dt.tzinfo is None:
        quoted_dt = quoted_dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    elapsed_minutes = (now - quoted_dt).total_seconds() / 60
    if elapsed_minutes <= FRESH_MAX_MINUTES:
        return DataStatus.FRESH
    if elapsed_minutes <= DELAYED_MAX_MINUTES:
        return DataStatus.DELAYED
    return DataStatus.STALE
