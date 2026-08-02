"""거래 저장소 + position_engine + signal_engine을 하나로 묶는 오케스트레이션 계층.

이 모듈이 하는 일 (그리고 하지 않는 일)
----------------------------------------
* 거래가 생성/수정/삭제될 때마다 해당 종목의 전체 거래를 다시 재생해서
  position_state를 갱신한다 (스펙 4.7 "거래 수정 시 추적 손절 상태 재검증").
* 최신 시세/ATR/포지션을 모아 signal_engine.determine_signal()을 호출하고
  결과를 signal_state에 저장한다. 상태가 바뀌면 signal_events에 이력을 남긴다.
* 추적 손절선의 레칫(ratchet)을 관리한다 — 절대 스스로 주문을 넣거나 수량을
  정하지 않는다(스펙 2절 절대 원칙).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from ..repositories import deposits as deposits_repo
from ..repositories import fx as fx_repo
from ..repositories import instruments as instruments_repo
from ..repositories import market_data as market_data_repo
from ..repositories import position as position_repo
from ..repositories import signals as signals_repo
from ..repositories import trades as trades_repo
from . import notification_service
from .market_schedule import compute_data_status
from .position_engine import PositionState, Trade as EngineTrade, apply_trade, replay_trades
from .signal_engine import (
    DataStatus,
    SignalInput,
    SignalStatus,
    apply_trailing_stop_ratchet,
    compute_next_buy_price,
    compute_take_profit_price,
    compute_trailing_stop_candidate,
    determine_signal,
    reset_trailing_stop_cycle,
    should_emit_event,
)


def _replay_position(conn: sqlite3.Connection, instrument_id: str) -> PositionState:
    rows = trades_repo.list_trades_for_instrument(conn, instrument_id)
    engine_trades = [
        EngineTrade(
            trade_type=r["trade_type"],
            price=r["price"],
            quantity=r["quantity"],
            fee=r["fee"] or 0.0,
            tax=r["tax"] or 0.0,
            trade_id=r["id"],
        )
        for r in rows
    ]
    final_state, _steps = replay_trades(engine_trades)
    return final_state


def recompute_position(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any]:
    """전체 거래를 재생해 position_state를 덮어쓰고 최신 값을 반환한다."""
    state = _replay_position(conn, instrument_id)
    return position_repo.upsert_position(
        conn,
        instrument_id,
        quantity=state.quantity,
        avg_price=state.avg_price,
        cost_basis=state.cost_basis,
        last_buy_price=state.last_buy_price,
        last_sell_price=state.last_sell_price,
        last_trade_price=state.last_trade_price,
    )


def recompute_signal(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any]:
    """position_state + quote_latest + atr_values + instrument 손절선을 모아
    signal_engine.determine_signal()을 실행하고 signal_state/signal_events를 갱신한다.
    """
    instrument = instruments_repo.get_instrument(conn, instrument_id)
    if instrument is None:
        raise ValueError(f"instrument not found: {instrument_id}")
    position = position_repo.get_position(conn, instrument_id)
    quote = market_data_repo.get_quote(conn, instrument_id)
    atr_row = market_data_repo.get_latest_atr(conn, instrument_id)
    atr = atr_row["atr"] if atr_row else None

    quantity = position["quantity"] if position else 0.0
    avg_price = position["avg_price"] if position else 0.0
    last_buy_price = position["last_buy_price"] if position else None

    next_buy_price = compute_next_buy_price(last_buy_price, atr, instrument["buy_multiple"])
    take_profit_price = compute_take_profit_price(avg_price, atr, instrument["sell_multiple"], quantity)

    current_price = quote["price"] if quote else None

    # 신선도(스펙 17.3)는 "지금" 기준으로 매번 새로 계산한다 -- KIS 소스일 때만.
    # 수동 입력(manual)은 사용자가 방금 넣은 값이므로 저장된 상태를 그대로 신뢰한다.
    if quote is None:
        data_status = DataStatus.INSUFFICIENT_DATA
    elif quote["source"] == "manual":
        data_status = DataStatus(quote["data_status"])
    else:
        data_status = compute_data_status(
            quoted_at=quote["quoted_at"], now=datetime.now(timezone.utc),
            consecutive_failures=quote.get("consecutive_failures", 0),
        )

    previous = signals_repo.get_signal_state(conn, instrument_id)
    previous_status = SignalStatus(previous["status"]) if previous else None
    # 스펙 9.4 히스테리시스 -- 직전 신호가 이미 켜져 있었는지를 넘겨서, 기준선을
    # 살짝 반복 통과하는 가격에 매 폴링마다 신호가 깜빡이지 않게 한다.
    was_hit_buy = previous_status == SignalStatus.BUY_TRIGGERED
    was_hit_profit = previous_status in (SignalStatus.TAKE_PROFIT_TRIGGERED, SignalStatus.SELL_BOTH_TRIGGERED)
    was_hit_stop = previous_status in (SignalStatus.STOP_TRIGGERED, SignalStatus.SELL_BOTH_TRIGGERED)

    result = determine_signal(
        SignalInput(
            quantity=quantity,
            current_price=current_price,
            next_buy_price=next_buy_price,
            take_profit_price=take_profit_price,
            trailing_stop_price=instrument["trailing_stop_price"],
            data_status=data_status,
            atr=atr,
        ),
        was_hit_buy=was_hit_buy, was_hit_profit=was_hit_profit, was_hit_stop=was_hit_stop,
    )

    is_new_episode = should_emit_event(previous_status, result.status)
    event_id: Optional[int] = None
    if is_new_episode:
        # signal_state보다 먼저 이벤트를 만들어서 그 id를 latest_event_id로
        # 같이 저장한다 -- "확인" 기능이 이 id로 미확인 여부를 판정한다.
        event_id = signals_repo.record_signal_event(
            conn, instrument_id,
            previous_status=previous_status.value if previous_status else None,
            new_status=result.status.value,
        )

    new_state = signals_repo.upsert_signal_state(
        conn,
        instrument_id,
        status=result.status.value,
        data_status=data_status.value,
        next_buy_price=next_buy_price,
        take_profit_price=take_profit_price,
        trailing_stop_price=instrument["trailing_stop_price"],
        reason=result.reason,
        latest_event_id=event_id,
    )

    if is_new_episode:
        # 스펙 12.1 -- signal_events와 notification_outbox는 같은 트랜잭션에서 함께 기록한다.
        notification_service.enqueue_signal_notification(
            conn, event_id=event_id, instrument=instrument, position=position,
            quote=quote, atr_row=atr_row, signal_row=new_state,
        )

    return new_state


def acknowledge_signal(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any] | None:
    """사용자가 종목 상세에서 "확인"을 누른 것 -- 신호 배너를 끈다 (2026-08-02
    추가, 사용자 결정: 손절 등 트리거 신호는 가격이 반등한다고 자동으로
    조용히 사라지면 안 되고, 사람이 직접 확인해야 사라진다).

    상태 자체(signal_state.status)는 건드리지 않는다 -- 계속 STOP_TRIGGERED로
    계산되더라도(레칫/기준가 표시는 그대로 정확해야 하므로), acknowledged_event_id
    만 최신으로 맞춰서 프런트엔드가 배너를 숨기도록 한다. 이후 상태가 실제로
    또 바뀌면(새 signal_event) 다시 미확인 상태가 되어 배너가 뜬다.
    """
    return signals_repo.acknowledge_signal(conn, instrument_id)


def _apply_ratchet(conn: sqlite3.Connection, instrument_id: str) -> None:
    """instrument.post_entry_high_price + 최신 ATR로 후보 손절선을 계산하고
    레칫(단조 비감소)을 적용해 instrument.trailing_stop_price를 갱신한다."""
    instrument = instruments_repo.get_instrument(conn, instrument_id)
    atr_row = market_data_repo.get_latest_atr(conn, instrument_id)
    atr = atr_row["atr"] if atr_row else None
    candidate = compute_trailing_stop_candidate(
        instrument["post_entry_high_price"], atr, instrument["stop_multiple"]
    )
    new_stop = apply_trailing_stop_ratchet(instrument["trailing_stop_price"], candidate)
    instruments_repo.update_manual_fields(conn, instrument_id, trailing_stop_price=new_stop)


def record_trade(
    conn: sqlite3.Connection,
    instrument_id: str,
    *,
    trade_type: str,
    price: float,
    quantity: float,
    executed_at: str,
    fee: float | None = None,
    tax: float | None = None,
    memo: str | None = None,
) -> dict[str, Any]:
    """거래 등록 + 재계산 전체 흐름 (position_engine이 검증을 담당하므로
    OversellError/InvalidTradeError는 이 함수를 호출한 API 계층에서 잡아
    HTTP 409/422로 변환한다)."""
    instrument = instruments_repo.get_instrument(conn, instrument_id)
    if instrument is None:
        raise ValueError(f"instrument not found: {instrument_id}")

    position_before = position_repo.get_position(conn, instrument_id)
    was_flat = position_before is None or position_before["quantity"] <= 1e-9

    atr_row = market_data_repo.get_latest_atr(conn, instrument_id)
    atr_at_execution = atr_row["atr"] if atr_row else None

    # 저장 전에 미리 엔진으로 검증한다 -- OversellError는 여기서 그대로 전파되어
    # DB에 아무 것도 기록되지 않는다 (트랜잭션 롤백은 호출자의 session()이 담당).
    engine_state = _replay_position(conn, instrument_id)
    apply_trade(engine_state, EngineTrade(trade_type=trade_type, price=price, quantity=quantity,
                                           fee=fee or 0.0, tax=tax or 0.0))

    trade = trades_repo.create_trade(
        conn, instrument_id=instrument_id, trade_type=trade_type, price=price, quantity=quantity,
        executed_at=executed_at, fee=fee, tax=tax, memo=memo, atr_at_execution=atr_at_execution,
    )

    recompute_position(conn, instrument_id)

    if trade_type == "buy" and was_flat:
        # 새 사이클 시작 (스펙 8.4) -- 최고가를 진입가로 재설정하고 손절선을 초기화한다.
        instruments_repo.update_manual_fields(
            conn, instrument_id, post_entry_high_price=price, trailing_stop_price=None
        )

    new_position = position_repo.get_position(conn, instrument_id)
    if new_position["quantity"] <= 1e-9:
        # 전량 매도 완료 -> 사이클 초기화 (스펙 8.4)
        high, stop = reset_trailing_stop_cycle()
        instruments_repo.update_manual_fields(
            conn, instrument_id, post_entry_high_price=high, trailing_stop_price=stop
        )

    _apply_ratchet(conn, instrument_id)
    recompute_signal(conn, instrument_id)

    return trade


def edit_trade(conn: sqlite3.Connection, instrument_id: str, trade_id: str, **fields: Any) -> dict[str, Any] | None:
    """거래 수정 -- 스펙 4.7: 수정 후 전체 재생 + 추적 손절 상태를 재검증한다.

    DB에 쓰기 전에 먼저 메모리에서만 수정된 거래 목록을 재생해 검증한다
    (record_trade와 동일한 원칙). 이렇게 해야 이 함수가 "호출자가 실패 시
    트랜잭션을 롤백해줄 것"이라는 가정 없이도 스스로 원본 보존을 보장한다 --
    edit_trade만 단독으로 호출됐을 때도(예: 스크립트, 테스트) 검증 실패 시
    DB에 아무 흔적도 남기지 않는다.
    """
    current = trades_repo.get_trade(conn, trade_id)
    if current is None:
        return None

    rows = trades_repo.list_trades_for_instrument(conn, instrument_id)
    proposed_rows = [dict(r, **fields) if r["id"] == trade_id else dict(r) for r in rows]
    proposed_rows.sort(key=lambda r: (r["executed_at"], r["sequence_no"]))
    proposed_trades = [
        EngineTrade(trade_type=r["trade_type"], price=r["price"], quantity=r["quantity"],
                    fee=r["fee"] or 0.0, tax=r["tax"] or 0.0, trade_id=r["id"])
        for r in proposed_rows
    ]
    replay_trades(proposed_trades)  # 검증만 수행 -- 실패하면 예외가 그대로 전파되고 DB는 무손상

    updated = trades_repo.update_trade(conn, trade_id, **fields)
    _reconcile_after_trade_change(conn, instrument_id)
    return updated


def delete_trade(conn: sqlite3.Connection, instrument_id: str, trade_id: str) -> bool:
    deleted = trades_repo.delete_trade(conn, trade_id)
    if deleted:
        _reconcile_after_trade_change(conn, instrument_id)
    return deleted


def _reconcile_after_trade_change(conn: sqlite3.Connection, instrument_id: str) -> None:
    """스펙 4.7 -- 거래 수정/삭제 후 postEntryHigh/trailingStop 정합성을 다시 맞춘다.

    보유수량이 0이 되면 사이클을 초기화하고, 보유수량이 있는데 postEntryHigh가
    아예 없다면(예: 첫 매수 거래를 삭제 후 다른 매수가 남아있는 경우) 남은
    거래 중 가장 이른 매수가로 다시 앵커링한다.
    """
    recompute_position(conn, instrument_id)
    instrument = instruments_repo.get_instrument(conn, instrument_id)
    position = position_repo.get_position(conn, instrument_id)

    if position["quantity"] <= 1e-9:
        high, stop = reset_trailing_stop_cycle()
        instruments_repo.update_manual_fields(conn, instrument_id, post_entry_high_price=high, trailing_stop_price=stop)
    elif instrument["post_entry_high_price"] is None:
        rows = trades_repo.list_trades_for_instrument(conn, instrument_id)
        first_buy = next((r for r in rows if r["trade_type"] == "buy"), None)
        instruments_repo.update_manual_fields(
            conn, instrument_id,
            post_entry_high_price=first_buy["price"] if first_buy else None,
            trailing_stop_price=None,
        )

    _apply_ratchet(conn, instrument_id)
    recompute_signal(conn, instrument_id)


def commit_quote(
    conn: sqlite3.Connection, instrument_id: str, *, price: float, quoted_at: Optional[str] = None,
    source: str = "manual", data_status: str = "MANUAL_OVERRIDE",
) -> dict[str, Any]:
    """현재가 반영 -- 스펙 13.2 "수동 보정"(2단계) 및 worker.py의 KIS 자동 조회(3단계)
    양쪽에서 공유하는 진입점. source/data_status로 둘을 구분한다
    ("manual"/MANUAL_OVERRIDE vs "kis"/FRESH 등).

    자동 최고가 갱신(autoUpdateHigh)이 켜져 있고 새 현재가가 기존 최고가보다
    높으면 최고가를 갱신한 뒤 레칫을 다시 계산한다 (기존 앱 commitCurrentPrice와 동일한 의미).
    """
    market_data_repo.upsert_quote(conn, instrument_id, price=price, source=source,
                                   data_status=data_status, quoted_at=quoted_at)
    instrument = instruments_repo.get_instrument(conn, instrument_id)
    if instrument["auto_update_high"] and (
        instrument["post_entry_high_price"] is None or price > instrument["post_entry_high_price"]
    ):
        instruments_repo.update_manual_fields(conn, instrument_id, post_entry_high_price=price)
        _apply_ratchet(conn, instrument_id)
    recompute_signal(conn, instrument_id)
    return instruments_repo.get_instrument(conn, instrument_id)  # type: ignore[return-value]


def commit_manual_atr(
    conn: sqlite3.Connection, instrument_id: str, *, atr: float, trade_date: str
) -> dict[str, Any]:
    return commit_atr(conn, instrument_id, atr=atr, trade_date=trade_date, source="manual")


def commit_atr(
    conn: sqlite3.Connection, instrument_id: str, *, atr: float, trade_date: str, source: str = "manual"
) -> dict[str, Any]:
    """ATR 값 반영 -- 스펙 13.2 수동 보정(2단계)과 worker.py의 확정 일봉 기반
    자동 갱신(3단계) 공통 진입점."""
    market_data_repo.upsert_manual_atr(conn, instrument_id, atr=atr, trade_date=trade_date, source=source)
    _apply_ratchet(conn, instrument_id)
    recompute_signal(conn, instrument_id)
    return instruments_repo.get_instrument(conn, instrument_id)  # type: ignore[return-value]


def commit_post_high(conn: sqlite3.Connection, instrument_id: str, *, post_entry_high_price: float | None) -> dict[str, Any]:
    instruments_repo.update_manual_fields(conn, instrument_id, post_entry_high_price=post_entry_high_price)
    _apply_ratchet(conn, instrument_id)
    recompute_signal(conn, instrument_id)
    return instruments_repo.get_instrument(conn, instrument_id)  # type: ignore[return-value]


def _convert(amount: Optional[float], from_currency: str, to_currency: str, rates: dict[str, dict[str, Any]]) -> Optional[float]:
    if amount is None:
        return None
    if from_currency == to_currency:
        return amount
    from_rate = rates.get(from_currency, {}).get("rate_to_krw")
    to_rate = rates.get(to_currency, {}).get("rate_to_krw")
    if from_rate is None or to_rate is None:
        return None
    return amount * from_rate / to_rate


def build_dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    """종목 목록 화면 데이터 -- 기존 앱의 renderList() 총계 로직과 동일한 의미.

    통화 환산은 fx_rates(=1단위 통화의 원화 환산값)를 매개로 한다. 환율 정보가
    없는 항목은 합계에서 제외하고 그 개수를 별도로 보고한다(기존 앱과 동일 UX).
    """
    display_currency = fx_repo.get_display_currency(conn)
    rates = fx_repo.list_rates(conn)

    instrument_rows = []
    total_cost = 0.0
    missing_cost_count = 0
    total_eval = 0.0
    eval_known_count = 0
    missing_eval_price_count = 0
    missing_eval_fx_count = 0

    for instrument in instruments_repo.list_instruments(conn):
        snapshot = instrument_snapshot(conn, instrument["id"])
        position = snapshot["position"]
        quote = snapshot["quote"]
        cost_basis = position["cost_basis"] if position else 0.0
        current_price = quote["price"] if quote else None
        quantity = position["quantity"] if position else 0.0
        eval_value = current_price * quantity if current_price is not None else None

        converted_cost = _convert(cost_basis, instrument["currency"], display_currency, rates)
        if converted_cost is None:
            missing_cost_count += 1
        else:
            total_cost += converted_cost

        if eval_value is None:
            missing_eval_price_count += 1
        else:
            converted_eval = _convert(eval_value, instrument["currency"], display_currency, rates)
            if converted_eval is None:
                missing_eval_fx_count += 1
            else:
                total_eval += converted_eval
                eval_known_count += 1

        instrument_rows.append({
            "instrument": instrument,
            "position": position,
            "quote": quote,
            "signal": snapshot["signal"],
            "cost_basis": cost_basis,
            "eval_value": eval_value,
        })

    total_deposit = 0.0
    missing_deposit_fx_count = 0
    deposit_rows = deposits_repo.list_deposits(conn)
    for deposit in deposit_rows:
        converted = _convert(deposit["amount"], deposit["currency"], display_currency, rates)
        if converted is None:
            missing_deposit_fx_count += 1
        else:
            total_deposit += converted
    deposit_known_count = len(deposit_rows) - missing_deposit_fx_count
    deposit_total_valid = len(deposit_rows) == 0 or deposit_known_count > 0

    eval_contribution = total_eval if eval_known_count > 0 else 0.0
    deposit_contribution = total_deposit if deposit_total_valid else 0.0
    total_asset = eval_contribution + deposit_contribution

    pnl_amount = None
    pnl_pct = None
    if eval_known_count > 0 and total_cost > 0:
        pnl_amount = total_eval - total_cost
        pnl_pct = (pnl_amount / total_cost) * 100

    return {
        "instruments": instrument_rows,
        "deposits": deposit_rows,
        "fx": {"rates": rates, "display_currency": display_currency},
        "totals": {
            "cost_basis": total_cost,
            "missing_cost_count": missing_cost_count,
            "eval_value": total_eval if eval_known_count > 0 else None,
            "missing_eval_price_count": missing_eval_price_count,
            "missing_eval_fx_count": missing_eval_fx_count,
            "deposit_total": total_deposit if deposit_total_valid else None,
            "missing_deposit_fx_count": missing_deposit_fx_count,
            "asset_total": total_asset,
            "pnl_amount": pnl_amount,
            "pnl_pct": pnl_pct,
        },
    }


def instrument_snapshot(conn: sqlite3.Connection, instrument_id: str) -> dict[str, Any] | None:
    """대시보드/상세화면이 한 번에 쓰는 통합 스냅샷 -- instrument + position + quote + signal."""
    instrument = instruments_repo.get_instrument(conn, instrument_id)
    if instrument is None:
        return None
    return {
        "instrument": instrument,
        "position": position_repo.get_position(conn, instrument_id),
        "quote": market_data_repo.get_quote(conn, instrument_id),
        "atr": market_data_repo.get_latest_atr(conn, instrument_id),
        "signal": signals_repo.get_signal_state(conn, instrument_id),
    }
