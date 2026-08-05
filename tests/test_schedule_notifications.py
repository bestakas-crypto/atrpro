"""schedule_notification_service + repositories/schedule_notifications.py 테스트 -- v1.2.

test_notification_service.py와 동일한 이유로 매 테스트마다 더미 모드를 강제한다 --
.env에 실제 텔레그램 토큰이 들어있어도 테스트 중 실제 발송이 나가면 안 된다.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from atrsite.config import settings
from atrsite.repositories import schedule_notifications as outbox_repo
from atrsite.repositories import schedules as schedules_repo
from atrsite.services import schedule_notification_service as svc


@pytest.fixture(autouse=True)
def force_telegram_dummy_mode():
    original_token, original_chat = settings.telegram_bot_token, settings.telegram_chat_id
    object.__setattr__(settings, "telegram_bot_token", "")
    object.__setattr__(settings, "telegram_chat_id", "")
    yield
    object.__setattr__(settings, "telegram_bot_token", original_token)
    object.__setattr__(settings, "telegram_chat_id", original_chat)


def _make_once_schedule(conn, *, start_date: str, notify_days_before: int = 0, market: str = "NONE"):
    return schedules_repo.create_schedule(
        conn, plan_id=None, schedule_type="GENERAL", title="테스트 일정",
        market=market, recurrence_type="ONCE", start_date=start_date,
        holiday_policy="NO_ADJUSTMENT", notify_days_before=notify_days_before,
        total_amount=None, currency="KRW",
    )


def test_enqueue_on_exact_scheduled_date(db_conn):
    sched = _make_once_schedule(db_conn, start_date="2026-08-10")
    db_conn.commit()
    count = svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 10))
    assert count == 1
    pending = outbox_repo.list_pending(db_conn)
    assert len(pending) == 1
    assert pending[0]["notification_type"] == "REMINDER"


def test_no_enqueue_before_scheduled_date(db_conn):
    _make_once_schedule(db_conn, start_date="2026-08-10")
    db_conn.commit()
    count = svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 9))
    assert count == 0


def test_notify_days_before_fires_early(db_conn):
    _make_once_schedule(db_conn, start_date="2026-08-10", notify_days_before=3)
    db_conn.commit()
    assert svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 7)) == 1
    assert svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 10)) == 0  # 이미 3일전에 적재됨


def test_enqueue_is_idempotent_same_day_called_twice(db_conn):
    _make_once_schedule(db_conn, start_date="2026-08-10")
    db_conn.commit()
    first = svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 10))
    second = svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 10))
    assert first == 1
    assert second == 0  # UNIQUE 제약으로 중복 적재 안 됨
    assert len(outbox_repo.list_pending(db_conn)) == 1


def test_process_outbox_sends_and_marks_occurrence_notified(db_conn):
    sched = _make_once_schedule(db_conn, start_date="2026-08-10")
    db_conn.commit()
    svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 10))
    db_conn.commit()

    result = svc.process_outbox(db_conn, now=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc))
    assert result["sent"] == 1

    occ_id = sched["occurrences"][0]["id"]
    occ = schedules_repo.get_occurrence(db_conn, occ_id)
    assert occ["status"] == "NOTIFIED"


def test_process_outbox_retries_on_failure(db_conn, monkeypatch):
    _make_once_schedule(db_conn, start_date="2026-08-10")
    db_conn.commit()
    svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 10))
    db_conn.commit()

    monkeypatch.setattr(svc.telegram_client, "send_message", lambda text: False)
    now = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    result = svc.process_outbox(db_conn, now=now)
    assert result["retried"] == 1

    pending = outbox_repo.list_pending(db_conn, now=now.isoformat(timespec="seconds"))
    assert len(pending) == 0  # 아직 백오프(30초) 안 지남 -- 재조회 대상 아님
    later = (now + timedelta(seconds=31)).isoformat(timespec="seconds")
    assert len(outbox_repo.list_pending(db_conn, now=later)) == 1


def test_process_outbox_fails_after_max_retries(db_conn, monkeypatch):
    _make_once_schedule(db_conn, start_date="2026-08-10")
    db_conn.commit()
    svc.enqueue_due_notifications(db_conn, today=date(2026, 8, 10))
    db_conn.commit()

    monkeypatch.setattr(svc.telegram_client, "send_message", lambda text: False)
    now = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    for i in range(len(svc.RETRY_BACKOFF_SECONDS)):
        now = now + timedelta(seconds=svc.RETRY_BACKOFF_SECONDS[i] + 1)
        svc.process_outbox(db_conn, now=now)
    now = now + timedelta(seconds=svc.RETRY_BACKOFF_SECONDS[-1] + 1)
    result = svc.process_outbox(db_conn, now=now)
    assert result["failed"] == 1


def test_build_message_flags_holiday_confirmation_needed(db_conn):
    row = {
        "schedule_title": "미국 배당 확인", "schedule_type": "DIVIDEND_CHECK",
        "adjusted_scheduled_date": "2026-08-10", "original_scheduled_date": "2026-08-10",
        "needs_holiday_confirmation": True, "planned_amount": None, "currency": "USD",
    }
    text = svc.build_message(row)
    assert "검증된 휴장일 데이터가 없습니다" in text
    assert "배당 확인" in text


def test_build_message_shows_holiday_adjustment_note(db_conn):
    row = {
        "schedule_title": "주간 적립", "schedule_type": "BUY",
        "adjusted_scheduled_date": "2026-01-02", "original_scheduled_date": "2026-01-01",
        "needs_holiday_confirmation": False, "planned_amount": 100000, "currency": "KRW",
    }
    text = svc.build_message(row)
    assert "원래 예정일: 2026-01-01" in text
    assert "100,000 KRW" in text
