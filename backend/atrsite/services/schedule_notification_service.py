"""backend/atrsite/services/schedule_notification_service.py -- v1.2 예약알림 오케스트레이션.

worker.py가 매일 1회 enqueue_due_notifications()로 "오늘 알려야 할 회차"를
schedule_notification_outbox에 적재하고, 매 폴링 주기 process_outbox()로 실제
발송한다. notification_service.py(신호 알림)와 동일한 재시도 백오프/상태 머신
및 telegram_client 발송 함수를 그대로 재사용한다(스펙: 기존 Outbox 패턴 재사용).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from ..adapters import telegram_client
from ..repositories import schedule_notifications as outbox_repo
from ..repositories import schedules as schedules_repo

# 스펙 12.2와 동일한 백오프(신호 알림 재시도 정책 재사용): 1차 30초, 2차 2분, 3차 10분.
RETRY_BACKOFF_SECONDS = [30, 120, 600]

SCHEDULE_TYPE_LABELS = {
    "BUY": "매수", "SELL": "매도", "DEPOSIT": "입금", "WITHDRAWAL": "출금",
    "REBALANCE": "리밸런싱", "RULE_REVIEW": "규칙 점검", "MATURITY": "만기",
    "DIVIDEND_CHECK": "배당 확인", "INTEREST_CHECK": "이자 확인", "EXCHANGE": "환전",
    "DISCLOSURE_CHECK": "공시 확인", "TAX_CHECK": "세금 확인", "GENERAL": "일반",
}


def _fmt_money(n: Optional[float]) -> str:
    if n is None:
        return "-"
    return f"{round(n):,}"


def build_message(row: dict[str, Any]) -> str:
    label = SCHEDULE_TYPE_LABELS.get(row["schedule_type"], row["schedule_type"])
    lines = [f"[투자 일정 알림 -- {label}]", "", row["schedule_title"]]
    lines.append(f"예정일: {row['adjusted_scheduled_date']}")
    if row["original_scheduled_date"] != row["adjusted_scheduled_date"]:
        lines.append(f"(원래 예정일: {row['original_scheduled_date']} -- 휴장일 보정됨)")
    if row["needs_holiday_confirmation"]:
        lines.append("주의: 이 시장의 검증된 휴장일 데이터가 없습니다 -- 실제 개장 여부를 직접 확인하세요.")
    if row["planned_amount"] is not None:
        lines.append(f"계획 금액: {_fmt_money(row['planned_amount'])} {row['currency']}")
    lines.append("")
    lines.append("실제 주문/이체/확인은 각 채널에서 직접 진행하세요 (자동 실행 아님).")
    return "\n".join(lines)


def enqueue_due_notifications(conn: sqlite3.Connection, *, today: Optional[date] = None) -> int:
    """오늘 알림 대상 회차를 outbox에 적재한다. 실제 신규 적재 건수를 반환한다
    (이미 적재된 건은 UNIQUE 제약으로 조용히 무시됨 -- 멱등성)."""
    today = today or datetime.now().date()
    due = schedules_repo.find_occurrences_due_for_notification(conn, today=today)
    count = 0
    for row in due:
        payload = build_message(row)
        result = outbox_repo.enqueue(
            conn, occurrence_id=row["id"], notification_type="REMINDER",
            scheduled_date=row["adjusted_scheduled_date"], payload=payload,
        )
        if result is not None:
            count += 1
    return count


def process_outbox(conn: sqlite3.Connection, *, now: Optional[datetime] = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")

    result = {"sent": 0, "retried": 0, "failed": 0}
    for row in outbox_repo.list_pending(conn, now=now_iso):
        outbox_repo.mark_status(conn, row["id"], status="SENDING")
        ok = telegram_client.send_message(row["payload"])

        if ok:
            outbox_repo.mark_status(conn, row["id"], status="SENT")
            # 발송 성공 -> 해당 회차를 NOTIFIED로 전환(배지/오늘위젯/확인 UI 대상이 됨).
            # 이미 사용자가 다른 경로로 상태를 바꿔놨을 수 있으니(예: 먼저 완료 처리)
            # SCHEDULED인 경우에만 덮어쓴다.
            occ = schedules_repo.get_occurrence(conn, row["occurrence_id"])
            if occ is not None and occ["status"] == "SCHEDULED":
                schedules_repo.update_occurrence(conn, row["occurrence_id"], status="NOTIFIED")
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
