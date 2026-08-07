"""매매계획(트리거 감시) 오케스트레이션 -- Phase 1.

trade_plan_engine.py(순수함수)와 repositories/trade_plans.py(DB)를 연결한다.
schedule_notification_service.py와 동일한 재시도/Outbox 패턴을 그대로
재사용한다.

이 모듈은 기존 ATR 매수/손절/익절 신호(signal_engine.py, portfolio_service.
recompute_signal)와 완전히 독립적으로 동작한다 -- 서로의 상태를 읽거나
바꾸지 않는다.

호출 순서 전제: worker.py의 run_once()가 poll_quotes()로 quote_latest를
먼저 갱신한 "같은 사이클 안"에서 evaluate_trade_plans()를 부른다 -- 이
함수 자체는 KIS 현재가를 직접 조회하지 않고 이미 커밋된 quote_latest를
읽기만 한다(불필요한 중복 호출 방지). 예외는 confirm_mode=CLOSE 확정
종가 조회 1건뿐이고, 그것도 tier 후보가 실제로 있을 때만 호출한다.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from ..adapters import kis_client
from ..adapters.kis_client import KisApiError
from ..repositories import instruments as instruments_repo
from ..repositories import market_data as market_data_repo
from ..repositories import position as position_repo
from ..repositories import trade_plan_notifications as outbox_repo
from ..repositories import trade_plans as trade_plans_repo
from . import trade_plan_engine as engine
from .market_schedule import compute_data_status
from .signal_engine import DataStatus

logger = logging.getLogger("atrsite.trade_plan_service")

RETRY_BACKOFF_SECONDS = [30, 120, 600]  # 스펙 12.2와 동일(신호/예약알림과 통일)

_BLOCKED_STATUSES = {DataStatus.STALE, DataStatus.API_ERROR, DataStatus.INSUFFICIENT_DATA}


def _fmt_price(value: float, currency: str) -> str:
    if currency == "USD":
        return f"{value:,.2f}달러"
    if currency == "KRW":
        return f"{round(value):,}원"
    return f"{value:,.2f} {currency}"


def _to_tier_states(plan: dict[str, Any]) -> list[engine.TierState]:
    return [
        engine.TierState(t["tier_order"], t["pullback_pct"], t["sell_pct"], t["fired_at"])
        for t in sorted(plan["tiers"], key=lambda t: t["tier_order"])
    ]


def _to_plan_state(plan: dict[str, Any]) -> engine.PlanState:
    return engine.PlanState(
        lifecycle_status=plan["lifecycle_status"],
        trigger_price=plan["trigger_price"],
        trigger_direction=plan["trigger_direction"],
        trigger_activated_at=plan["trigger_activated_at"],
        peak_price_since_trigger=plan["peak_price_since_trigger"],
        confirm_mode=plan["confirm_mode"],
        approach_notified_at=plan["approach_notified_at"],
    )


def _any_tier_candidate_breached(plan: dict[str, Any], current_price: float) -> bool:
    """confirm_mode=CLOSE 확정 종가 조회가 필요한 상황인지(=적어도 하나의
    미발동 tier 선을 장중가가 건드렸는지) 미리 값싸게 판정한다 -- 매 평가마다
    KIS 일봉을 조회하지 않기 위함."""
    peak = plan["peak_price_since_trigger"]
    if peak is None:
        return False
    projected_peak = max(peak, current_price)
    for t in plan["tiers"]:
        if t["fired_at"] is not None:
            continue
        line = projected_peak * (1 - t["pullback_pct"] / 100)
        if current_price <= line:
            return True
    return False


def _fetch_confirmed_close(instrument: dict[str, Any], *, today: date) -> Optional[float]:
    code = instrument["kis_code"]
    if not code:
        return None
    client = kis_client.get_client()
    start = (today - timedelta(days=5)).isoformat()
    end = today.isoformat()
    try:
        bars = client.get_daily_bars(
            code, start, end, market=instrument["kis_market"], is_etf=instrument["is_etf"],
        )
    except KisApiError as exc:
        logger.warning("확정 종가 조회 실패 instrument=%s: %s", instrument["name"], exc)
        return None
    today_str = today.isoformat()
    for bar in bars:
        if bar.trade_date == today_str:
            return bar.close
    return None  # 거래소가 아직 오늘 봉을 확정하지 않음


def _build_observation(conn: sqlite3.Connection, plan: dict[str, Any], *, now: datetime) -> engine.PlanObservation:
    ref_instrument_id = plan["price_reference_instrument_id"]
    instrument = instruments_repo.get_instrument(conn, ref_instrument_id)
    quote = market_data_repo.get_quote(conn, ref_instrument_id)
    atr_row = market_data_repo.get_latest_atr(conn, ref_instrument_id)
    atr = atr_row["atr"] if atr_row else None

    if quote is None or quote["price"] is None or quote["price"] <= 0:
        return engine.PlanObservation(intraday_price=None, is_data_valid=False, atr=atr)

    if quote["source"] == "manual":
        data_status = DataStatus(quote["data_status"])
    else:
        data_status = compute_data_status(
            quoted_at=quote["quoted_at"], now=now, consecutive_failures=quote["consecutive_failures"] or 0,
        )
    is_valid = data_status not in _BLOCKED_STATUSES

    confirmed_close = None
    if (
        is_valid
        and plan["confirm_mode"] == "CLOSE"
        and plan["lifecycle_status"] in ("ACTIVE", "PARTIALLY_FIRED")
        and _any_tier_candidate_breached(plan, quote["price"])
    ):
        confirmed_close = _fetch_confirmed_close(instrument, today=now.date())

    return engine.PlanObservation(
        intraday_price=quote["price"], is_data_valid=is_valid, confirmed_close=confirmed_close, atr=atr,
    )


def _quantities_fired_before(plan: dict[str, Any], before_tier_order: int) -> dict[str, float]:
    """DB에 이미 fired_at이 찍혀 있는(=이전 사이클에 발동한) tier들의
    권고수량을 tier_order 순서대로 다시 계산해서 누적한다. before_tier_order
    미만인 tier만 본다."""
    baseline = {i["instrument_id"]: i["baseline_quantity"] for i in plan["instruments"]}
    tier_states = _to_tier_states(plan)
    already = {iid: 0.0 for iid in baseline}
    for t in sorted(plan["tiers"], key=lambda x: x["tier_order"]):
        if t["tier_order"] >= before_tier_order or t["fired_at"] is None:
            continue
        t_is_final = engine.is_cumulative_final_tier(tier_states, t["tier_order"])
        result = engine.compute_tier_sell_quantities(
            baseline, t["sell_pct"], is_final_tier=t_is_final, already_recommended=already,
        )
        for iid, qty in result.items():
            already[iid] += qty
    return already


def _compute_fired_quantities(plan: dict[str, Any], fired_tier_order: int) -> dict[str, int]:
    """단일 tier 발동 시 계좌별 매도 권고수량(최대잔여법). 같은 사이클에
    여러 tier가 동시 발동하는 경우에는 이 함수를 개별 호출해서 합산하면
    안 된다(같은 baseline을 중복으로 100%씩 계산하게 됨) --
    그 경우는 _enqueue_combined_tier_fired가 already_recommended를
    체이닝해서 별도로 처리한다."""
    baseline = {i["instrument_id"]: i["baseline_quantity"] for i in plan["instruments"]}
    tier_states = _to_tier_states(plan)
    this_tier = next(t for t in plan["tiers"] if t["tier_order"] == fired_tier_order)
    is_final = engine.is_cumulative_final_tier(tier_states, fired_tier_order)
    already_recommended = _quantities_fired_before(plan, fired_tier_order) if is_final else None
    return engine.compute_tier_sell_quantities(
        baseline, this_tier["sell_pct"], is_final_tier=is_final, already_recommended=already_recommended,
    )


def _quantity_shortfall_notes(conn: sqlite3.Connection, plan: dict[str, Any], quantities: dict[str, int]) -> list[str]:
    """실제 보유수량이 권고수량보다 부족하면 경고 문구를 만든다. 체결을
    추정하지 않고 사실만 알린다."""
    notes = []
    names = {i["instrument_id"]: i["instrument_name"] for i in plan["instruments"]}
    for instrument_id, recommended in quantities.items():
        position = position_repo.get_position(conn, instrument_id)
        actual = position["quantity"] if position else 0
        if actual < recommended:
            notes.append(
                f"{names.get(instrument_id, instrument_id)}: 권고 {recommended}주이나 "
                f"현재 확인 가능한 보유수량은 {actual}주입니다 -- 실제 체결 여부를 직접 확인하세요."
            )
    return notes


def _enqueue_notifications(
    conn: sqlite3.Connection, plan: dict[str, Any], evaluation: engine.PlanEvaluation, *, now: datetime,
) -> None:
    trading_date = now.date().isoformat()
    currency = None
    ref_instrument = next(
        (i for i in plan["instruments"] if i["instrument_id"] == plan["price_reference_instrument_id"]), None,
    )
    if ref_instrument is not None:
        currency = ref_instrument["currency"]

    fired_events = [e for e in evaluation.events if isinstance(e, engine.TierEvent) and e.kind == "FIRED"]

    for event in evaluation.events:
        if event == "DATA_STALE":
            payload = f"[데이터 이상] {plan['label']} 시세가 오래됐거나 조회되지 않아 계획을 변경하지 않았어요."
            outbox_repo.enqueue(
                conn, plan_id=plan["id"], event_type="DATA_STALE",
                idempotency_key=f"DATA_STALE:{trading_date}", payload=payload,
            )
        elif event == "TRIGGER_APPROACH":
            price_str = _fmt_price(plan["trigger_price"], currency or "")
            payload = f"[트리거 접근] {plan['label']}가 {price_str} 트리거에 접근했어요."
            outbox_repo.enqueue(
                conn, plan_id=plan["id"], event_type="TRIGGER_APPROACH",
                idempotency_key="TRIGGER_APPROACH", payload=payload,
            )
        elif event == "TRIGGER_REACHED":
            price_str = _fmt_price(plan["trigger_price"], currency or "")
            payload = (
                f"[트리거 도달] {plan['label']}가 {price_str}에 도달해 지금부터 최고가 추적을 "
                f"시작해요. 아직 매도 신호는 아니에요."
            )
            outbox_repo.enqueue(
                conn, plan_id=plan["id"], event_type="TRIGGER_REACHED",
                idempotency_key="TRIGGER_REACHED", payload=payload,
            )
        elif isinstance(event, engine.TierEvent) and event.kind == "PREVIEW":
            payload = f"[장중 예비] {plan['label']}가 {event.tier_order}차선 아래로 내려왔지만 종가 확인 전이에요."
            outbox_repo.enqueue(
                conn, plan_id=plan["id"], event_type="TIER_PREVIEW",
                idempotency_key=f"TIER_PREVIEW:{event.tier_order}:{trading_date}", payload=payload,
            )
        # FIRED는 아래에서 한꺼번에 처리한다(같은 사이클에 여러 tier가 동시
        # 발동해도 알림 한 건으로 합친다 -- 갭 하락 시 여러 tier를 개별
        # 텔레그램 메시지로 쪼개지 않기 위함).

    if fired_events:
        _enqueue_combined_tier_fired(conn, plan, fired_events)


def _enqueue_combined_tier_fired(conn: sqlite3.Connection, plan: dict[str, Any], fired_events: list) -> None:
    """같은 사이클에 여러 tier가 동시 발동(갭 하락)한 경우를 처리한다.
    각 tier의 권고수량을 독립적으로 계산해서 단순 합산하면 안 된다 --
    누적 100%(final) tier는 "baseline - 이미 판 만큼"으로 계산되는데, 같은
    사이클에서 먼저 처리된 tier의 몫이 아직 DB에 fired_at으로 반영되기
    전이라 already_recommended에 안 잡히면 그 몫까지 다시 전량으로
    계산해서 이중으로 잡힐 수 있다. 그래서 이전 사이클에 이미 발동한
    tier로 시작해, 이번 사이클에 발동하는 tier들을 tier_order 순서대로
    already_recommended에 누적하면서 하나씩 계산한다."""
    names = {i["instrument_id"]: i["instrument_name"] for i in plan["instruments"]}
    baseline = {i["instrument_id"]: i["baseline_quantity"] for i in plan["instruments"]}
    total_baseline = sum(i["baseline_quantity"] for i in plan["instruments"])
    tier_states = _to_tier_states(plan)
    ordered_fired = sorted(fired_events, key=lambda e: e.tier_order)

    already_recommended = _quantities_fired_before(plan, ordered_fired[0].tier_order)
    all_quantities: dict[str, int] = {iid: 0 for iid in names}
    tier_summaries = []
    for event in ordered_fired:
        this_tier = next(t for t in plan["tiers"] if t["tier_order"] == event.tier_order)
        is_final = engine.is_cumulative_final_tier(tier_states, event.tier_order)
        quantities = engine.compute_tier_sell_quantities(
            baseline, this_tier["sell_pct"], is_final_tier=is_final, already_recommended=already_recommended,
        )
        for iid, qty in quantities.items():
            already_recommended[iid] = already_recommended.get(iid, 0) + qty
            all_quantities[iid] = all_quantities.get(iid, 0) + qty
        qty_desc = ", ".join(f"{names.get(iid, iid)} {qty}주" for iid, qty in quantities.items())
        tier_summaries.append(f"{event.tier_order}차(최고가 대비 -{event.pullback_pct}%): {qty_desc}")

    total_qty_desc = ", ".join(f"{names.get(iid, iid)} {qty}주" for iid, qty in all_quantities.items())
    if len(fired_events) == 1:
        lines = [
            f"[종가 확정] {plan['label']}가 최고가 대비 {fired_events[0].pullback_pct}% 하락을 종가로 "
            f"확인했어요. 기존 기준수량 {round(total_baseline)}주 중 {total_qty_desc} 매도를 권고해요.",
        ]
    else:
        lines = [
            f"[종가 확정 -- {len(fired_events)}단계 동시 이탈] {plan['label']}가 갭 하락으로 여러 단계 "
            f"매도선을 한 번에 통과했어요. 기존 기준수량 {round(total_baseline)}주 중 총 {total_qty_desc} "
            f"매도를 권고해요.",
        ]
        lines.extend(f"  - {s}" for s in tier_summaries)

    lines.extend(_quantity_shortfall_notes(conn, plan, all_quantities))
    idem_key = "TIER_FIRED:" + "+".join(str(e.tier_order) for e in sorted(fired_events, key=lambda e: e.tier_order))
    outbox_repo.enqueue(
        conn, plan_id=plan["id"], event_type="TIER_FIRED", idempotency_key=idem_key, payload="\n".join(lines),
    )


def evaluate_plan(conn: sqlite3.Connection, plan_id: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """계획 하나를 평가하고 저장·알림 적재까지 끝낸다."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    plan = trade_plans_repo.get_plan(conn, plan_id)
    if plan is None:
        raise ValueError(f"존재하지 않는 계획: {plan_id}")

    obs = _build_observation(conn, plan, now=now)
    plan_state = _to_plan_state(plan)
    tier_states = _to_tier_states(plan)
    evaluation = engine.evaluate_plan(plan_state, tier_states, obs, now_iso=now_iso)

    tier_updates = [
        {
            "tier_order": u.tier_order, "fired_at": u.fired_at,
            "fired_peak_price": u.fired_peak_price, "fired_reference_price": u.fired_reference_price,
        }
        for u in evaluation.tier_updates
    ]
    trade_plans_repo.apply_evaluation(
        conn, plan_id,
        new_lifecycle_status=evaluation.new_lifecycle_status,
        new_trigger_activated_at=evaluation.new_trigger_activated_at,
        new_peak_price=evaluation.new_peak_price,
        new_approach_notified_at=evaluation.new_approach_notified_at,
        tier_updates=tier_updates,
    )

    if evaluation.events:
        _enqueue_notifications(conn, plan, evaluation, now=now)

    return {"plan_id": plan_id, "events": len(evaluation.events)}


def evaluate_trade_plans(conn: sqlite3.Connection, *, now: Optional[datetime] = None) -> dict[str, int]:
    """ARMED/ACTIVE/PARTIALLY_FIRED 상태의 모든 계획을 한 번씩 평가한다.
    worker.py가 poll_quotes() 이후 같은 사이클에서 호출한다. 계획 하나가
    실패해도(예외) 나머지는 계속 평가한다."""
    now = now or datetime.now(timezone.utc)
    result = {"evaluated": 0, "events": 0, "errors": 0}
    for plan in trade_plans_repo.list_plans_for_polling(conn):
        try:
            outcome = evaluate_plan(conn, plan["id"], now=now)
            result["evaluated"] += 1
            result["events"] += outcome["events"]
        except Exception:
            logger.exception("매매계획 평가 실패 plan_id=%s", plan["id"])
            result["errors"] += 1
    return result


def process_outbox(conn: sqlite3.Connection, *, now: Optional[datetime] = None) -> dict[str, int]:
    """schedule_notification_service.process_outbox()와 동일한 재시도 패턴."""
    from ..adapters import telegram_client

    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")

    result = {"sent": 0, "retried": 0, "failed": 0}
    for row in outbox_repo.list_pending(conn, now=now_iso):
        outbox_repo.mark_status(conn, row["id"], status="SENDING")
        ok = telegram_client.send_message(row["payload"])

        if ok:
            outbox_repo.mark_status(conn, row["id"], status="SENT")
            result["sent"] += 1
            continue

        attempt = row["attempt_count"] + 1
        if attempt > len(RETRY_BACKOFF_SECONDS):
            outbox_repo.mark_status(conn, row["id"], status="FAILED", increment_attempt=True)
            result["failed"] += 1
        else:
            delay = RETRY_BACKOFF_SECONDS[attempt - 1]
            next_attempt_at = (now + timedelta(seconds=delay)).isoformat(timespec="seconds")
            outbox_repo.mark_status(
                conn, row["id"], status="RETRY", next_attempt_at=next_attempt_at, increment_attempt=True,
            )
            result["retried"] += 1

    return result
