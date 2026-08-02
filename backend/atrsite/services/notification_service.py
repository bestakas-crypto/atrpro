"""알림 Outbox 오케스트레이션 -- 스펙 12절.

portfolio_service.recompute_signal()이 신호 상태 전환을 감지하면
enqueue_signal_notification()을 호출해 signal_events와 같은 트랜잭션 안에서
notification_outbox에도 함께 적재한다 (스펙 12.1 "한 DB 트랜잭션에서 함께
기록"). worker.py는 매 폴링 주기마다 process_outbox()를 호출해 대기 중인
알림을 실제로 발송한다.

주의: "장중 장애 알림 자체가 반복되지 않도록 동일 오류 유형에도 쿨다운을
둔다"(스펙 12.2)는 API 실패 등 시스템 경보용 알림 채널을 염두에 둔 규칙이다.
이 골격 단계에서는 그런 시스템 경보를 아직 텔레그램으로 보내지 않으므로
(worker.py는 logger.warning으로만 남김) 그 쿨다운은 아직 구현하지 않았다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..adapters import telegram_client
from ..repositories import notifications as notifications_repo
from .signal_engine import SignalStatus

# 스펙 12.2 -- 1차 30초, 2차 2분, 3차 10분 후 재시도. 4차 실패는 확정(더 이상 재시도 안 함).
RETRY_BACKOFF_SECONDS = [30, 120, 600]

# 이 상태로 "전환"될 때만 알림을 만든다 -- NEUTRAL/NO_POSITION으로 돌아가는 것까지
# 알리면 스펙 12.3 예시의 취지(기준 도달 알림)에서 벗어난 스팸이 된다.
NOTIFIABLE_STATUSES = frozenset({
    SignalStatus.BUY_TRIGGERED,
    SignalStatus.TAKE_PROFIT_TRIGGERED,
    SignalStatus.STOP_TRIGGERED,
    SignalStatus.SELL_BOTH_TRIGGERED,
})


def _fmt_money(n: Optional[float]) -> str:
    if n is None:
        return "-"
    return f"{round(n):,}"


def _fmt_qty(n: Optional[float]) -> str:
    if n is None:
        return "-"
    return f"{n:,.4f}".rstrip("0").rstrip(".")


def build_message(
    *, instrument: dict[str, Any], position: Optional[dict[str, Any]],
    quote: Optional[dict[str, Any]], atr_row: Optional[dict[str, Any]],
    signal_row: dict[str, Any],
) -> str:
    """스펙 12.3 텔레그램 메시지 형식.

    12.3 본문에 예시가 나온 건 매수 알림뿐이라, 익절/손절/동시도달은 같은
    어조("추천 수량" 표현 금지, 실제 주문은 MTS에서 직접 확인)를 유지하며
    유사한 구조로 확장했다.
    """
    status = SignalStatus(signal_row["status"])
    name = instrument["name"]
    price = quote["price"] if quote else None
    atr = atr_row["atr"] if atr_row else None
    atr_date = atr_row["trade_date"] if atr_row else "-"
    quantity = position["quantity"] if position else 0.0

    lines: list[str] = []

    def pct_line(threshold: Optional[float]) -> None:
        if price is not None and threshold not in (None, 0):
            pct = (price - threshold) / threshold * 100
            lines.append(f"기준 대비: {pct:+.2f}%")

    if status == SignalStatus.BUY_TRIGGERED:
        threshold = signal_row["next_buy_price"]
        lines.append("[ATR 매수 기준 도달]")
        lines.append("")
        lines.append(name)
        lines.append(f"현재가: {_fmt_money(price)}")
        lines.append(f"매수 기준가: {_fmt_money(threshold)}")
        pct_line(threshold)
        lines.append(f"적용 ATR: {_fmt_money(atr)}")
        lines.append(f"ATR 기준일: {atr_date}")
        lines.append("")
        tranche = instrument.get("tranche_amount")
        if tranche and threshold:
            qty = int(tranche // threshold)
            lines.append(f"등록된 트랜치 금액: {_fmt_money(tranche)}")
            lines.append(f"계산 가능 수량: {qty}주")
        else:
            lines.append("등록된 트랜치 금액: 설정 안 됨")

    elif status == SignalStatus.TAKE_PROFIT_TRIGGERED:
        threshold = signal_row["take_profit_price"]
        lines.append("[ATR 익절 기준 도달]")
        lines.append("")
        lines.append(name)
        lines.append(f"현재가: {_fmt_money(price)}")
        lines.append(f"익절 기준가: {_fmt_money(threshold)}")
        pct_line(threshold)
        lines.append(f"적용 ATR: {_fmt_money(atr)}")
        lines.append(f"ATR 기준일: {atr_date}")
        lines.append("")
        lines.append(f"보유수량: {_fmt_qty(quantity)}주")

    elif status == SignalStatus.STOP_TRIGGERED:
        threshold = signal_row["trailing_stop_price"]
        lines.append("[ATR 손절 기준 도달]")
        lines.append("")
        lines.append(name)
        lines.append(f"현재가: {_fmt_money(price)}")
        lines.append(f"손절 기준가: {_fmt_money(threshold)}")
        pct_line(threshold)
        lines.append(f"적용 ATR: {_fmt_money(atr)}")
        lines.append(f"ATR 기준일: {atr_date}")
        lines.append("")
        lines.append(f"보유수량: {_fmt_qty(quantity)}주")

    else:  # SELL_BOTH_TRIGGERED
        lines.append("[ATR 매도 기준 도달 (손절 + 익절 동시)]")
        lines.append("")
        lines.append(name)
        lines.append(f"현재가: {_fmt_money(price)}")
        lines.append(f"익절 기준가: {_fmt_money(signal_row['take_profit_price'])}")
        lines.append(f"손절 기준가: {_fmt_money(signal_row['trailing_stop_price'])}")
        lines.append(f"적용 ATR: {_fmt_money(atr)}")
        lines.append(f"ATR 기준일: {atr_date}")
        lines.append("")
        lines.append(f"보유수량: {_fmt_qty(quantity)}주")

    lines.append("")
    lines.append("실제 주문은 MTS에서 직접 확인")
    return "\n".join(lines)


def enqueue_signal_notification(
    conn: sqlite3.Connection, *, event_id: int, instrument: dict[str, Any],
    position: Optional[dict[str, Any]], quote: Optional[dict[str, Any]],
    atr_row: Optional[dict[str, Any]], signal_row: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """신호가 알림 대상 상태로 전환됐을 때만(NOTIFIABLE_STATUSES) Outbox에 적재한다."""
    status = SignalStatus(signal_row["status"])
    if status not in NOTIFIABLE_STATUSES:
        return None
    text = build_message(instrument=instrument, position=position, quote=quote, atr_row=atr_row, signal_row=signal_row)
    return notifications_repo.enqueue(conn, signal_event_id=event_id, channel="telegram", payload=text)


def process_outbox(conn: sqlite3.Connection, *, now: Optional[datetime] = None) -> dict[str, int]:
    """PENDING/RETRY(백오프 경과분만) 항목을 발송 시도하고 상태를 갱신한다 (스펙 12.1/12.2)."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")

    result = {"sent": 0, "retried": 0, "failed": 0}
    for row in notifications_repo.list_pending(conn, now=now_iso):
        notifications_repo.mark_status(conn, row["id"], status="SENDING")
        ok = telegram_client.send_message(row["payload"])

        if ok:
            notifications_repo.mark_status(conn, row["id"], status="SENT")
            result["sent"] += 1
            continue

        attempt = row["attempt_count"] + 1
        if attempt > len(RETRY_BACKOFF_SECONDS):
            notifications_repo.mark_status(conn, row["id"], status="FAILED", increment_attempt=True)
            result["failed"] += 1
        else:
            delay = RETRY_BACKOFF_SECONDS[attempt - 1]
            next_attempt_at = (now + timedelta(seconds=delay)).isoformat(timespec="seconds")
            notifications_repo.mark_status(
                conn, row["id"], status="RETRY", next_attempt_at=next_attempt_at, increment_attempt=True,
            )
            result["retried"] += 1

    return result
