"""backend/atrsite/services/snapshot_service.py -- v1.3 일별 총자산/손익 스냅샷 계산.

기존 portfolio_service.build_dashboard()와 정확히 같은 공식(총자산 = 종목평가액 +
예금합계, 평가손익 = 평가액－매입원가, 통화환산은 fx_rates 매개)을 그대로 따른다 --
_convert()도 그대로 재사용해서 대시보드와 스냅샷의 총자산이 절대 어긋나지 않게 한다.

이 모듈이 새로 하는 것:
  1. 실현손익 집계 -- position_engine.replay_trades()가 매도마다 계산하는
     realized_pnl은 지금까지 어디에도 누적 저장되지 않고 그때그때 버려졌다(trades
     테이블에 realized_pnl 컬럼 자체가 없음). 종목별 전체 거래를 재생해서 매도
     스텝의 realized_pnl을 모두 더하면 "지금까지의 누적 실현손익"을 낼 수 있다 --
     신규 데이터는 필요 없고 순수 집계만 추가하면 된다.
     한계(사용자에게 보고됨): 외화 거래의 실현손익은 거래 시점 환율이 저장돼 있지
     않아 "현재" 환율로 환산한다 -- 시점별 정확한 원화 환산이 아니다. 이 근사가
     적용됐는지는 quality_notes에 남기지 않는다(모든 외화 거래에 항상 적용되는
     구조적 한계라 개별 스냅샷 경고보다는 history.txt/코드 주석으로 문서화한다).
  2. 통화별 자산/손익 분해 -- KRW 환산이 아니라 "그 통화 원래 단위" 그대로 저장한다
     (usd_krw_rate 등과 짝을 이뤄 필요하면 화면에서 직접 환산).
  3. data_quality_status 판정 -- market_schedule.compute_data_status()로 시세
     신선도를 재계산하고, 가격/환율 누락 여부와 합쳐 COMPLETE/PARTIAL/STALE_PRICE/
     FAILED/MANUAL로 분류(스펙 8절, 심각도 FAILED > PARTIAL > STALE_PRICE > COMPLETE).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Optional

from ..repositories import deposits as deposits_repo
from ..repositories import fx as fx_repo
from ..repositories import instruments as instruments_repo
from ..repositories import market_data as market_data_repo
from ..repositories import position as position_repo
from ..repositories import trades as trades_repo
from .market_schedule import compute_data_status
from .portfolio_service import _convert
from .position_engine import Trade as EngineTrade, replay_trades
from .signal_engine import DataStatus

CALCULATION_VERSION = 1

QUALITY_COMPLETE = "COMPLETE"
QUALITY_PARTIAL = "PARTIAL"
QUALITY_FAILED = "FAILED"
QUALITY_STALE_PRICE = "STALE_PRICE"
QUALITY_MANUAL = "MANUAL"

_QUANTITY_EPSILON = 1e-9
# 이 상태들이면 시세는 "오래됐거나 신뢰도 낮음"으로 취급해 STALE_PRICE 후보로 본다.
_STALE_LIKE_STATUSES = frozenset({DataStatus.STALE, DataStatus.API_ERROR})


def _instrument_realized_profit(
    conn: sqlite3.Connection, instrument: dict[str, Any], rates: dict[str, Any], base_currency: str,
) -> Optional[float]:
    """이 종목의 전체 매도 이력에서 실현손익을 누적하고 base_currency로 환산한다.
    거래가 없으면 0.0(실현손익 없음), 환율 정보가 없어 환산 불가능하면 None을
    반환한다(조용히 0으로 취급하지 않음 -- 호출자가 realized_unknown으로 구분)."""
    trades = trades_repo.list_trades_for_instrument(conn, instrument["id"])
    if not trades:
        return 0.0
    sorted_trades = sorted(trades, key=lambda t: (t["executed_at"], t["sequence_no"]))
    engine_trades = [
        EngineTrade(
            trade_type=t["trade_type"], price=t["price"], quantity=t["quantity"],
            fee=t["fee"] or 0.0, tax=t["tax"] or 0.0, trade_id=t["id"],
        )
        for t in sorted_trades
    ]
    _, steps = replay_trades(engine_trades)
    realized_total = sum(step.realized_pnl for step in steps if step.realized_pnl is not None)
    if abs(realized_total) < 1e-9:
        return 0.0
    return _convert(realized_total, instrument["currency"], base_currency, rates)


def build_snapshot(
    conn: sqlite3.Connection, *, snapshot_date: date, now: Optional[datetime] = None,
    triggered_by: str = "auto",
) -> dict[str, Any]:
    """스냅샷 1건을 계산한다(순수 계산 -- DB 저장은 repositories/snapshots.py 담당).

    반환 dict의 "items" 키에 portfolio_snapshot_items로 저장할 상세 행 목록이 담긴다.
    """
    now = now or datetime.now(timezone.utc)
    base_currency = fx_repo.get_display_currency(conn)
    rates = fx_repo.list_rates(conn)

    items: list[dict[str, Any]] = []
    total_cost = 0.0
    total_eval = 0.0
    eval_known_count = 0
    missing_eval_price_count = 0
    missing_eval_fx_count = 0
    missing_cost_count = 0
    has_stale_price = False
    currency_asset_totals: dict[str, float] = {}
    currency_profit_totals: dict[str, float] = {}
    realized_total = 0.0
    realized_unknown = False

    # active_only=False -- 비활성화된 종목도 과거 실현손익 집계 대상이라 전부 훑는다.
    for instrument in instruments_repo.list_instruments(conn, active_only=False):
        position = position_repo.get_position(conn, instrument["id"])
        quantity = position["quantity"] if position else 0.0
        currency = instrument["currency"]

        realized = _instrument_realized_profit(conn, instrument, rates, base_currency)
        if realized is None:
            realized_unknown = True
        else:
            realized_total += realized

        if quantity <= _QUANTITY_EPSILON:
            # 보유수량 0(전량매도 등)인 종목은 "평가에 포함된 종목"이 아니다 -- 위에서
            # 실현손익은 이미 집계했으니 평가 항목으로는 만들지 않고 건너뛴다.
            continue

        cost_basis = position["cost_basis"] if position else 0.0
        quote = market_data_repo.get_quote(conn, instrument["id"])

        item: dict[str, Any] = {
            "item_type": "INSTRUMENT",
            "instrument_id": instrument["id"],
            "instrument_name_snapshot": instrument["name"],
            "instrument_code_snapshot": instrument.get("kis_code"),
            "market_code": instrument.get("kis_market"),
            "currency": currency,
            "quantity": quantity,
            "cost_basis": cost_basis,
        }

        converted_cost = _convert(cost_basis, currency, base_currency, rates)
        if converted_cost is None:
            missing_cost_count += 1
        else:
            total_cost += converted_cost

        if quote is None:
            missing_eval_price_count += 1
            item.update({
                "unit_price": None, "price_date": None, "original_value": 0.0,
                "exchange_rate": None, "krw_value": None, "unrealized_profit": None,
                "data_status": "MISSING_PRICE", "error_message": "시세 없음",
            })
            items.append(item)
            continue

        price = quote["price"]
        original_value = price * quantity
        # 수동 입력 시세는 신선도 재계산 대상이 아니다(market_schedule.compute_data_status
        # 자체 문서화 원칙 -- 사용자가 방금 입력한 값을 "오래된 데이터"로 재판정하지 않음).
        if quote["source"] == "manual":
            freshness = DataStatus(quote["data_status"])
        else:
            freshness = compute_data_status(
                quoted_at=quote["quoted_at"], now=now, consecutive_failures=quote["consecutive_failures"],
            )

        currency_asset_totals.setdefault(currency, 0.0)
        currency_asset_totals[currency] += original_value
        if cost_basis:
            currency_profit_totals.setdefault(currency, 0.0)
            currency_profit_totals[currency] += original_value - cost_basis

        converted_eval = _convert(original_value, currency, base_currency, rates)
        if converted_eval is None:
            missing_eval_fx_count += 1
            item.update({
                "unit_price": price, "price_date": quote["quoted_at"][:10],
                "original_value": original_value, "exchange_rate": None, "krw_value": None,
                "unrealized_profit": None, "data_status": "MISSING_FX",
                "error_message": f"{currency} 환율 없음",
            })
        else:
            total_eval += converted_eval
            eval_known_count += 1
            data_status = "OK"
            if freshness in _STALE_LIKE_STATUSES:
                data_status = "STALE"
                has_stale_price = True
            unrealized = converted_eval - converted_cost if converted_cost is not None else None
            rate = None if currency == base_currency else rates.get(currency, {}).get("rate_to_krw")
            item.update({
                "unit_price": price, "price_date": quote["quoted_at"][:10],
                "original_value": original_value, "exchange_rate": rate, "krw_value": converted_eval,
                "unrealized_profit": unrealized, "data_status": data_status, "error_message": None,
            })
        items.append(item)

    total_deposit = 0.0
    missing_deposit_fx_count = 0
    deposit_rows = deposits_repo.list_deposits(conn)
    for deposit in deposit_rows:
        currency = deposit["currency"]
        currency_asset_totals.setdefault(currency, 0.0)
        currency_asset_totals[currency] += deposit["amount"]
        converted = _convert(deposit["amount"], currency, base_currency, rates)
        item = {
            "item_type": "DEPOSIT", "account_id": deposit["id"], "account_name_snapshot": deposit["account_name"],
            "currency": currency, "quantity": None, "unit_price": None, "price_date": None,
            "original_value": deposit["amount"], "cost_basis": None, "unrealized_profit": None,
        }
        if converted is None:
            missing_deposit_fx_count += 1
            item.update({"exchange_rate": None, "krw_value": None, "data_status": "MISSING_FX",
                          "error_message": f"{currency} 환율 없음"})
        else:
            total_deposit += converted
            item.update({
                "exchange_rate": None if currency == base_currency else rates.get(currency, {}).get("rate_to_krw"),
                "krw_value": converted, "data_status": "OK", "error_message": None,
            })
        items.append(item)

    deposit_known_count = len(deposit_rows) - missing_deposit_fx_count
    deposit_total_valid = len(deposit_rows) == 0 or deposit_known_count > 0
    eval_contribution = total_eval if eval_known_count > 0 else 0.0
    deposit_contribution = total_deposit if deposit_total_valid else 0.0
    total_asset = eval_contribution + deposit_contribution

    unrealized_profit: Optional[float] = None
    profit_rate: Optional[float] = None
    if eval_known_count > 0 and total_cost > 0:
        unrealized_profit = total_eval - total_cost
        profit_rate = (unrealized_profit / total_cost) * 100

    total_profit: Optional[float] = None
    if unrealized_profit is not None and not realized_unknown:
        total_profit = unrealized_profit + realized_total

    included_instrument_count = eval_known_count + missing_eval_price_count + missing_eval_fx_count
    successful_quote_count = eval_known_count
    failed_quote_count = missing_eval_price_count + missing_eval_fx_count

    notes: list[str] = []
    if included_instrument_count == 0 and len(deposit_rows) == 0:
        status = QUALITY_FAILED
        notes.append("등록된 종목/예금이 없어 총자산을 계산할 수 없습니다")
    elif missing_eval_price_count or missing_eval_fx_count or missing_deposit_fx_count or missing_cost_count:
        status = QUALITY_PARTIAL
        if missing_eval_price_count:
            notes.append(f"시세 누락 {missing_eval_price_count}종목")
        if missing_eval_fx_count:
            notes.append(f"평가액 환율 누락 {missing_eval_fx_count}종목")
        if missing_deposit_fx_count:
            notes.append(f"예금 환율 누락 {missing_deposit_fx_count}건")
        if missing_cost_count:
            notes.append(f"매입원가 환율 누락 {missing_cost_count}종목")
    elif has_stale_price:
        status = QUALITY_STALE_PRICE
        notes.append("일부 시세가 오래됨(STALE/API_ERROR) -- 최근 확정가로 계산")
    else:
        status = QUALITY_COMPLETE

    if realized_unknown:
        notes.append("일부 종목 실현손익 환산 실패(환율 없음) -- 총손익 계산 보류")

    if triggered_by == "manual" and status == QUALITY_COMPLETE:
        status = QUALITY_MANUAL
        notes.append("수동 재계산")

    return {
        "snapshot_date": snapshot_date.isoformat(),
        "snapshot_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Seoul",
        "base_currency": base_currency,
        "total_asset_value": total_asset,
        "investment_market_value": eval_contribution,
        "cash_deposit_value": deposit_contribution,
        "total_cost_basis": total_cost,
        "unrealized_profit": unrealized_profit,
        "realized_profit": None if realized_unknown else realized_total,
        "total_profit": total_profit,
        "profit_rate": profit_rate,
        "krw_asset_value": currency_asset_totals.get("KRW"),
        "usd_asset_value": currency_asset_totals.get("USD"),
        "jpy_asset_value": currency_asset_totals.get("JPY"),
        "krw_profit": currency_profit_totals.get("KRW"),
        "usd_profit": currency_profit_totals.get("USD"),
        "jpy_profit": currency_profit_totals.get("JPY"),
        "usd_krw_rate": rates.get("USD", {}).get("rate_to_krw"),
        "jpy_krw_rate": rates.get("JPY", {}).get("rate_to_krw"),
        "included_instrument_count": included_instrument_count,
        "successful_quote_count": successful_quote_count,
        "failed_quote_count": failed_quote_count,
        "data_quality_status": status,
        "quality_notes": "; ".join(notes) if notes else None,
        "calculation_version": CALCULATION_VERSION,
        "items": items,
    }


# 재시도해도 개선될 여지가 없는(이미 충분히 완성된) 상태 -- worker.py가 이 상태를
# 보면 그날 재시도를 더 하지 않는다.
COMPLETE_LIKE_STATUSES = frozenset({QUALITY_COMPLETE, QUALITY_STALE_PRICE, QUALITY_MANUAL})

# 스펙 18절 -- "2일 이상 스냅샷 누락" 관리자 경고 기준.
MISSING_GAP_WARNING_DAYS = 2


def find_missing_gap_warning(conn: sqlite3.Connection, *, today: date) -> Optional[str]:
    """가장 최근의 정상 완료(COMPLETE류) 스냅샷 날짜와 오늘 사이 간격이
    MISSING_GAP_WARNING_DAYS 이상이면 경고 문구를 반환한다(없으면 None).
    이 함수는 import cycle을 피하려고 repositories.snapshots를 지역 임포트한다
    (snapshots.py가 이 모듈의 COMPLETE_LIKE_STATUSES를 참조하는 반대 방향
    의존성이 이미 있어서 최상단에서 순환 임포트하지 않는다)."""
    from ..repositories import snapshots as snapshots_repo

    rows = snapshots_repo.list_snapshots(conn)
    complete_dates = [
        date.fromisoformat(r["snapshot_date"]) for r in rows if r["data_quality_status"] in COMPLETE_LIKE_STATUSES
    ]
    if not complete_dates:
        return None  # 아직 정상 스냅샷이 하나도 없음 -- "누락"이 아니라 "시작 전"
    gap_days = (today - max(complete_dates)).days
    if gap_days >= MISSING_GAP_WARNING_DAYS:
        return f"최근 정상 스냅샷이 {gap_days}일째 없습니다(마지막: {max(complete_dates).isoformat()})"
    return None
