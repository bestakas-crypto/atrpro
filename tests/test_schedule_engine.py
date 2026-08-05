"""schedule_engine.py 단위 테스트 -- v1.2 반복일정/휴장일보정/예산배분."""
from datetime import date

import pytest

from atrsite.services import schedule_engine as se


# ---- generate_occurrence_dates -------------------------------------------------

def test_once_returns_single_date():
    assert se.generate_occurrence_dates(start_date=date(2026, 9, 1), recurrence_type="ONCE") == [date(2026, 9, 1)]


def test_weekly_by_count():
    dates = se.generate_occurrence_dates(
        start_date=date(2026, 8, 10), recurrence_type="WEEKLY", interval=1, occurrence_count=9,
    )
    assert len(dates) == 9
    assert dates[0] == date(2026, 8, 10)
    assert dates[1] == date(2026, 8, 17)
    assert dates[-1] == date(2026, 10, 5)
    # 항상 같은 요일(월요일) 유지
    assert len({d.weekday() for d in dates}) == 1


def test_biweekly_by_end_date():
    dates = se.generate_occurrence_dates(
        start_date=date(2026, 1, 1), recurrence_type="BIWEEKLY", interval=1, end_date=date(2026, 2, 1),
    )
    assert dates == [date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 29)]


def test_monthly_clips_to_month_end():
    dates = se.generate_occurrence_dates(
        start_date=date(2026, 1, 31), recurrence_type="MONTHLY", interval=1, occurrence_count=3,
    )
    # 2월은 28일까지만 있음(2026년은 평년)
    assert dates == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]


def test_yearly_leap_day_clips():
    dates = se.generate_occurrence_dates(
        start_date=date(2028, 2, 29), recurrence_type="YEARLY", interval=1, occurrence_count=2,
    )
    assert dates == [date(2028, 2, 29), date(2029, 2, 28)]


def test_custom_dates_sorted():
    dates = se.generate_occurrence_dates(
        start_date=date(2026, 1, 1), recurrence_type="CUSTOM",
        custom_dates=[date(2026, 5, 1), date(2026, 1, 1), date(2026, 3, 1)],
    )
    assert dates == [date(2026, 1, 1), date(2026, 3, 1), date(2026, 5, 1)]


def test_custom_requires_dates():
    with pytest.raises(se.ScheduleEngineError):
        se.generate_occurrence_dates(start_date=date(2026, 1, 1), recurrence_type="CUSTOM")


def test_recurring_requires_count_or_end_date():
    with pytest.raises(se.ScheduleEngineError):
        se.generate_occurrence_dates(start_date=date(2026, 1, 1), recurrence_type="WEEKLY")


def test_max_occurrences_guard():
    with pytest.raises(se.ScheduleEngineError):
        se.generate_occurrence_dates(
            start_date=date(2000, 1, 1), recurrence_type="DAILY", occurrence_count=se.MAX_OCCURRENCES + 1,
        )


# ---- adjust_for_holiday ---------------------------------------------------------

def test_none_market_never_adjusts():
    result = se.adjust_for_holiday(date(2026, 1, 1), market="NONE", holiday_policy="NEXT_BUSINESS_DAY")
    assert result.adjusted == date(2026, 1, 1)
    assert result.needs_confirmation is False


def test_no_adjustment_policy_passthrough():
    result = se.adjust_for_holiday(date(2026, 1, 1), market="KRX", holiday_policy="NO_ADJUSTMENT")
    assert result.adjusted == date(2026, 1, 1)
    assert result.needs_confirmation is False


def test_krx_new_years_day_shifts_to_next_business_day():
    # 2026-01-01은 목요일이면서 신정(고정공휴일)
    result = se.adjust_for_holiday(date(2026, 1, 1), market="KRX", holiday_policy="NEXT_BUSINESS_DAY")
    assert result.original == date(2026, 1, 1)
    assert result.adjusted == date(2026, 1, 2)
    assert result.needs_confirmation is False


def test_krx_weekend_shifts_to_previous_business_day():
    # 2026-01-03은 토요일
    result = se.adjust_for_holiday(date(2026, 1, 3), market="KRX", holiday_policy="PREVIOUS_BUSINESS_DAY")
    assert result.adjusted.weekday() < 5
    assert result.adjusted < date(2026, 1, 3)
    assert result.needs_confirmation is False


def test_krx_weekday_no_holiday_unchanged():
    # 평범한 평일(공휴일 아님)은 그대로
    result = se.adjust_for_holiday(date(2026, 8, 12), market="KRX", holiday_policy="NEXT_BUSINESS_DAY")
    assert result.adjusted == date(2026, 8, 12)


def test_nyse_always_needs_confirmation_even_on_weekday():
    result = se.adjust_for_holiday(date(2026, 7, 4), market="NYSE_NASDAQ", holiday_policy="NEXT_BUSINESS_DAY")
    # 미국 공휴일 데이터가 없으므로 평일이라 해도 무조건 확인 필요
    assert result.needs_confirmation is True
    assert "확인 필요" in result.note


def test_nyse_weekend_still_shifts_but_flags_confirmation():
    result = se.adjust_for_holiday(date(2026, 8, 15), market="NYSE_NASDAQ", holiday_policy="NEXT_BUSINESS_DAY")  # 토요일
    assert result.adjusted.weekday() < 5
    assert result.needs_confirmation is True


def test_user_confirm_policy_forces_confirmation_without_moving_date():
    result = se.adjust_for_holiday(date(2026, 1, 3), market="KRX", holiday_policy="USER_CONFIRM")
    assert result.adjusted == date(2026, 1, 3)
    assert result.needs_confirmation is True


# ---- allocate_evenly / allocate_by_ratio -----------------------------------------

def test_allocate_evenly_remainder_goes_to_last():
    amounts = se.allocate_evenly(100, 3, currency="KRW")
    assert amounts == [33, 33, 34]
    assert sum(amounts) == 100


def test_allocate_evenly_exact_division():
    amounts = se.allocate_evenly(300, 3, currency="KRW")
    assert amounts == [100, 100, 100]


def test_allocate_by_ratio_50_50():
    amounts = se.allocate_by_ratio(8_324_310, [50, 50], currency="KRW")
    assert amounts == [4_162_155, 4_162_155]
    assert sum(amounts) == 8_324_310


def test_allocate_by_ratio_remainder_to_last():
    amounts = se.allocate_by_ratio(101, [50, 50], currency="KRW")
    assert amounts == [50, 51]


# ---- 실제 ISA 플랜 사례(v1.2 스펙 명시 검증용) -----------------------------------
# 74,918,796원을 9회 주간 분할, 각 회차를 TIGER KOFR금리액티브(449170)/
# KODEX iShares 미국하이일드액티브(468380) 50:50으로 배분.

def test_isa_real_plan_case_matches_spec_numbers():
    total_amount = 74_918_796
    occurrence_amounts = se.allocate_evenly(total_amount, 9, currency="KRW")

    assert occurrence_amounts[:8] == [8_324_310] * 8
    assert occurrence_amounts[8] == 8_324_316
    assert sum(occurrence_amounts) == total_amount

    for i, occ_amount in enumerate(occurrence_amounts):
        item_amounts = se.allocate_by_ratio(occ_amount, [50, 50], currency="KRW")
        assert sum(item_amounts) == occ_amount
        if i < 8:
            assert item_amounts == [4_162_155, 4_162_155]
        else:
            assert item_amounts == [4_162_158, 4_162_158]

    dates = se.generate_occurrence_dates(
        start_date=date(2026, 8, 10), recurrence_type="WEEKLY", interval=1, occurrence_count=9,
    )
    assert len(dates) == 9
