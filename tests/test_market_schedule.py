"""market_schedule 단위 테스트 -- 스펙 11.4 거래일 판정 + 11.3 폴링 정책."""
from datetime import date, datetime

from atrsite.services.market_schedule import (
    MarketPhase,
    decide_polling,
    determine_phase,
    is_trading_day,
)


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
