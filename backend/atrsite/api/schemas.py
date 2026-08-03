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


class InstrumentSettingsUpdate(BaseModel):
    name: Optional[str] = None
    currency: Optional[str] = None
    buy_multiple: Optional[float] = Field(default=None, ge=0)
    sell_multiple: Optional[float] = Field(default=None, ge=0)
    stop_multiple: Optional[float] = Field(default=None, ge=0)
    tranche_amount: Optional[float] = Field(default=None, ge=0)
    kis_code: Optional[str] = None
    kis_market: Optional[str] = None


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


class FxRateUpdate(BaseModel):
    rates: dict[str, float] = Field(default_factory=dict)
    display_currency: Optional[str] = None


class CompanyResolveUS(BaseModel):
    """스펙 4.2 "이 종목 분석" 확인 -- /company/search 결과 중 하나를 그대로
    에코해서 실제 companies 행으로 만든다."""
    cik: int
    ticker: str = Field(min_length=1)
    name: str = Field(min_length=1)


class CompanyAnalysisRun(BaseModel):
    company_id: str = Field(min_length=1)
