"""market_schedule 단위 테스트 -- 스펙 11.4 거래일 판정 + 11.3 폴링 정책."""
from datetime import date, datetime, timedelta, timezone

from atrsite.services.market_schedule import (
    MarketPhase,
    compute_data_status,
    decide_polling,
    determine_phase,
    is_trading_day,
)
from atrsite.services.signal_engine import DataStatus


def test_weekend_is_not_a_trading_day():
    assert is_trading_day(date(2026, 8, 1)) is False  # 토요일
    assert is_trading_day(date(2026, 8, 2)) is False  # 일요일
    assert is_trading_day(date(2026, 8, 3)) is True   # 월요일


def test_fixed_holiday_is_not_a_trading_day():
    assert is_trading_day(date(2026, 1, 1)) is False   # 신정
    assert is_trading_day(date(2026, 12, 25)) is False  # 성탄절


def test_weekday_non_holiday_is_trading_day():
    assert is_trading_day(date(2026, 8, 4)) is True  # 화요일, 공휴일 아님


def test_phase_before_open_is_pre_market():
    assert determine_phase(datetime(2026, 8, 4, 8, 30)) == MarketPhase.PRE_MARKET


def test_phase_during_regular_hours_is_regular():
    assert determine_phase(datetime(2026, 8, 4, 9, 0)) == MarketPhase.REGULAR
    assert determine_phase(datetime(2026, 8, 4, 12, 0)) == MarketPhase.REGULAR
    assert determine_phase(datetime(2026, 8, 4, 15, 30)) == MarketPhase.REGULAR


def test_phase_after_close_is_post_market():
    assert determine_phase(datetime(2026, 8, 4, 15, 31)) == MarketPhase.POST_MARKET
    assert determine_phase(datetime(2026, 8, 4, 20, 0)) == MarketPhase.POST_MARKET


def test_phase_on_holiday_is_holiday_regardless_of_time():
    assert determine_phase(datetime(2026, 1, 1, 10, 0)) == MarketPhase.HOLIDAY


def test_decide_polling_matches_phase_semantics():
    regular = decide_polling(datetime(2026, 8, 4, 10, 0))
    assert regular.should_poll_quotes is True
    assert regular.should_collect_daily_bars is False

    post = decide_polling(datetime(2026, 8, 4, 16, 0))
    assert post.should_poll_quotes is False
    assert post.should_collect_daily_bars is True

    pre = decide_polling(datetime(2026, 8, 4, 8, 0))
    assert pre.should_poll_quotes is False
    assert pre.should_collect_daily_bars is False

    holiday = decide_polling(datetime(2026, 1, 1, 10, 0))
    assert holiday.should_poll_quotes is False
    assert holiday.should_collect_daily_bars is False


# ---------------------------------------------------------------------------
# compute_data_status -- 스펙 17.3 신선도 등급 자동 전환 (2026-08-02 추가)
# ---------------------------------------------------------------------------

def test_no_quote_ever_is_insufficient_data():
    now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    assert compute_data_status(quoted_at=None, now=now, consecutive_failures=0) == DataStatus.INSUFFICIENT_DATA


def test_fresh_within_seven_minutes():
    now = datetime(2026, 8, 4, 10, 7, tzinfo=timezone.utc)
    quoted_at = (now - timedelta(minutes=7)).isoformat()
    assert compute_data_status(quoted_at=quoted_at, now=now, consecutive_failures=0) == DataStatus.FRESH


def test_delayed_between_seven_and_fifteen_minutes():
    now = datetime(2026, 8, 4, 10, 10, tzinfo=timezone.utc)
    quoted_at = (now - timedelta(minutes=10)).isoformat()
    assert compute_data_status(quoted_at=quoted_at, now=now, consecutive_failures=0) == DataStatus.DELAYED


def test_stale_after_fifteen_minutes():
    now = datetime(2026, 8, 4, 10, 20, tzinfo=timezone.utc)
    quoted_at = (now - timedelta(minutes=16)).isoformat()
    assert compute_data_status(quoted_at=quoted_at, now=now, consecutive_failures=0) == DataStatus.STALE


def test_three_consecutive_failures_is_api_error_regardless_of_elapsed_time():
    """방금 실패했어도(경과시간 0에 가까워도) 연속 3회째면 API_ERROR가 우선한다."""
    now = datetime(2026, 8, 4, 10, 0, 30, tzinfo=timezone.utc)
    quoted_at = (now - timedelta(seconds=30)).isoformat()
    assert compute_data_status(quoted_at=quoted_at, now=now, consecutive_failures=3) == DataStatus.API_ERROR


def test_two_consecutive_failures_alone_does_not_force_api_error():
    now = datetime(2026, 8, 4, 10, 0, 30, tzinfo=timezone.utc)
    quoted_at = (now - timedelta(seconds=30)).isoformat()
    assert compute_data_status(quoted_at=quoted_at, now=now, consecutive_failures=2) == DataStatus.FRESH


def test_naive_quoted_at_is_treated_as_utc():
    """quoted_at에 타임존 정보가 없어도(과거 더미 데이터 등) UTC로 간주해서
    비교해야 한다 -- naive/aware 비교 시 예외가 나면 안 됨."""
    now = datetime(2026, 8, 4, 10, 5, tzinfo=timezone.utc)
    naive_quoted_at = "2026-08-04T10:00:00"  # tzinfo 없음
    assert compute_data_status(quoted_at=naive_quoted_at, now=now, consecutive_failures=0) == DataStatus.FRESH
