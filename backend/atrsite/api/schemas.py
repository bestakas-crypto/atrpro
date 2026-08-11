"""요청 바디 검증용 pydantic 모델. 응답은 리포지토리가 만드는 dict를 그대로 반환한다."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class InstrumentCreate(BaseModel):
    name: str = Field(min_length=1)
    currency: str = "KRW"
    buy_multiple: float = Field(default=1.0, ge=0)
    sell_multiple: float = Field(default=1.5, ge=0)
    stop_multiple: float = Field(default=2.0, ge=0)
    tranche_amount: Optional[float] = Field(default=None, ge=0)
    kis_code: Optional[str] = None
    kis_market: Optional[str] = None
    is_etf: bool = False


class InstrumentSettingsUpdate(BaseModel):
    name: Optional[str] = None
    currency: Optional[str] = None
    buy_multiple: Optional[float] = Field(default=None, ge=0)
    sell_multiple: Optional[float] = Field(default=None, ge=0)
    stop_multiple: Optional[float] = Field(default=None, ge=0)
    tranche_amount: Optional[float] = Field(default=None, ge=0)
    kis_code: Optional[str] = None
    kis_market: Optional[str] = None
    is_etf: Optional[bool] = None


class InstrumentManualUpdate(BaseModel):
    post_entry_high_price: Optional[float] = None
    auto_update_high: Optional[bool] = None


class QuoteCommit(BaseModel):
    price: float = Field(gt=0)


class AtrCommit(BaseModel):
    atr: float = Field(gt=0)
    trade_date: Optional[str] = None


class TradeCreate(BaseModel):
    trade_type: Literal["buy", "sell"]
    price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    executed_at: str
    fee: Optional[float] = Field(default=None, ge=0)
    tax: Optional[float] = Field(default=None, ge=0)
    memo: Optional[str] = None


class TradeUpdate(BaseModel):
    price: Optional[float] = Field(default=None, gt=0)
    quantity: Optional[float] = Field(default=None, gt=0)
    executed_at: Optional[str] = None
    fee: Optional[float] = Field(default=None, ge=0)
    tax: Optional[float] = Field(default=None, ge=0)
    memo: Optional[str] = None


class DepositCreate(BaseModel):
    account_name: str = Field(min_length=1)
    amount: float = Field(ge=0)
    currency: str = "KRW"


class DepositUpdate(BaseModel):
    account_name: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None


# backend/atrsite/api/schemas.py -- v1.5 입출금 통합 장부(2026-08-12 추가).
# 옛 WithdrawalCreate/Update + CashInflowCreate/Update를 하나로 합침 -- 카드
# 소비분석 역할은 card-kunoh/Kunoh's Sheet로 옮겨가서 이 장부는 순수 입출금
# 기록+analyze.kunoh.top 데이터 제공용으로 축소(사용자, 2026-08-12).
class CashLedgerEntryCreate(BaseModel):
    occurred_at: str = Field(min_length=1)
    deposit_account_id: str = Field(min_length=1)
    entry_type: str = Field(min_length=1)  # EXTERNAL_IN/EXTERNAL_OUT/INTERNAL_IN/INTERNAL_OUT
    amount: float = Field(gt=0)
    currency: str = Field(min_length=1)
    memo: Optional[str] = None


class CashLedgerEntryUpdate(BaseModel):
    occurred_at: Optional[str] = None
    deposit_account_id: Optional[str] = None
    entry_type: Optional[str] = None
    amount: Optional[float] = Field(default=None, gt=0)
    currency: Optional[str] = None
    memo: Optional[str] = None


class FxRateUpdate(BaseModel):
    rates: dict[str, float] = Field(default_factory=dict)
    display_currency: Optional[str] = None


class CompanyResolveUS(BaseModel):
    """스펙 4.2 "이 종목 분석" 확인 -- /company/search 결과 중 하나를 그대로
    에코해서 실제 companies 행으로 만든다."""
    cik: int
    ticker: str = Field(min_length=1)
    name: str = Field(min_length=1)


class CompanyResolveKR(BaseModel):
    corp_code: str = Field(min_length=1)
    corp_name: str = Field(min_length=1)
    stock_code: str = Field(min_length=1)


class CompanyAnalysisRun(BaseModel):
    company_id: str = Field(min_length=1)


# backend/atrsite/api/schemas.py -- v1.2 통합 투자 스케줄(2026-08-05 추가).
class PlanItemInput(BaseModel):
    instrument_id: str = Field(min_length=1)
    ratio_percent: float = Field(gt=0)


class InvestmentPlanCreate(BaseModel):
    name: str = Field(min_length=1)
    deposit_account_id: Optional[str] = None
    total_amount: Optional[float] = Field(default=None, ge=0)
    currency: str = "KRW"
    memo: Optional[str] = None
    items: list[PlanItemInput] = Field(default_factory=list)


class InvestmentPlanUpdate(BaseModel):
    name: Optional[str] = None
    deposit_account_id: Optional[str] = None
    total_amount: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    status: Optional[str] = None
    memo: Optional[str] = None


class ScheduleItemInput(BaseModel):
    instrument_id: str = Field(min_length=1)
    ratio_percent: float = Field(gt=0)


class InvestmentScheduleCreate(BaseModel):
    plan_id: Optional[str] = None
    schedule_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    market: str = "NONE"
    recurrence_type: str = Field(min_length=1)
    recurrence_interval: int = Field(default=1, ge=1)
    start_date: str = Field(min_length=1)
    end_date: Optional[str] = None
    occurrence_count: Optional[int] = Field(default=None, ge=1)
    custom_dates: Optional[list[str]] = None
    holiday_policy: str = "NEXT_BUSINESS_DAY"
    notify_days_before: int = Field(default=0, ge=0)
    total_amount: Optional[float] = Field(default=None, ge=0)
    currency: str = "KRW"
    deposit_account_id: Optional[str] = None
    memo: Optional[str] = None
    items: list[ScheduleItemInput] = Field(default_factory=list)


class InvestmentScheduleUpdate(BaseModel):
    """title/market/holiday_policy/notify_days_before/status/memo는 반복 유형과
    무관하게 언제든 수정 가능. start_date/total_amount/currency/items(날짜·금액·
    종목배분)는 ONCE 일정에서만 허용된다 -- 반복 일정은 회차가 여러 개라 "어느
    회차를 바꾸는지" 모호해지므로 취소 후 재등록을 안내한다(repositories/
    schedules.py의 update_schedule 참고)."""
    title: Optional[str] = None
    market: Optional[str] = None
    holiday_policy: Optional[str] = None
    notify_days_before: Optional[int] = Field(default=None, ge=0)
    status: Optional[str] = None
    memo: Optional[str] = None
    deposit_account_id: Optional[str] = None
    start_date: Optional[str] = None
    total_amount: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    items: Optional[list[ScheduleItemInput]] = None


class ScheduleOccurrenceUpdate(BaseModel):
    status: Optional[str] = None
    acknowledged: Optional[bool] = None


class ScheduleExecutionCreate(BaseModel):
    execution_type: str = Field(min_length=1)
    linked_withdrawal_id: Optional[str] = None
    linked_trade_id: Optional[str] = None
    executed_amount: Optional[float] = Field(default=None, ge=0)
    executed_at: Optional[str] = None
    memo: Optional[str] = None


# backend/atrsite/api/schemas.py -- 매매계획(트리거 감시) Phase 1(2026-08-07 추가).

class TradePlanInstrumentIn(BaseModel):
    instrument_id: str = Field(min_length=1)
    baseline_quantity: float = Field(gt=0)
    display_note: Optional[str] = None


class TradePlanTierIn(BaseModel):
    tier_order: int = Field(ge=1)
    pullback_pct: float = Field(gt=0)
    sell_pct: float = Field(gt=0, le=100)


class TradePlanCreate(BaseModel):
    plan_type: Literal["TRAIL"] = "TRAIL"  # Phase 1은 TRAIL만 허용
    label: str = Field(min_length=1)
    trigger_price: float = Field(gt=0)
    trigger_direction: Literal["ABOVE", "BELOW"]
    confirm_mode: Literal["CLOSE", "INTRADAY"] = "CLOSE"
    price_reference_instrument_id: str = Field(min_length=1)
    instruments: list[TradePlanInstrumentIn] = Field(min_length=1)
    tiers: list[TradePlanTierIn] = Field(default_factory=list)
    purpose: Optional[str] = None
    invalidation_condition: Optional[str] = None
    review_date: Optional[str] = None
    reason: Optional[str] = None


class TradePlanUpdate(BaseModel):
    change_reason: str = Field(min_length=1)
    label: Optional[str] = None
    trigger_price: Optional[float] = Field(default=None, gt=0)
    confirm_mode: Optional[Literal["CLOSE", "INTRADAY"]] = None
    purpose: Optional[str] = None
    invalidation_condition: Optional[str] = None
    review_date: Optional[str] = None
    reason: Optional[str] = None


