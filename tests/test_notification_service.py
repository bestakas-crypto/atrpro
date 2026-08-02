"""notification_service + telegram_client 테스트 -- 스펙 12절.

이 개발 환경의 .env에는 이제 실제 텔레그램 봇 토큰/채팅ID가 들어 있으므로
(4단계 이후 실제 알림 연동), 아래 autouse 픽스처로 매 테스트마다 강제로
더미 모드를 켠다 -- 안 그러면 pytest를 돌릴 때마다 사용자의 실제 텔레그램으로
가짜 매매 신호 메시지가 발송된다. 재시도/백오프 경로는 send_message 자체를
monkeypatch해서 검증한다.
"""
from datetime import datetime, timedelta, timezone

import pytest

from atrsite.adapters import telegram_client
from atrsite.config import settings
from atrsite.repositories import notifications as notifications_repo
from atrsite.services import notification_service, portfolio_service
from atrsite.services.signal_engine import SignalStatus


@pytest.fixture(autouse=True)
def force_telegram_dummy_mode():
    original_token, original_chat = settings.telegram_bot_token, settings.telegram_chat_id
    object.__setattr__(settings, "telegram_bot_token", "")
    object.__setattr__(settings, "telegram_chat_id", "")
    yield
    object.__setattr__(settings, "telegram_bot_token", original_token)
    object.__setattr__(settings, "telegram_chat_id", original_chat)


def _make_instrument_with_position(conn, **overrides):
    from atrsite.repositories import instruments as instruments_repo
    defaults = dict(name="테스트종목", buy_multiple=1.0, sell_multiple=1.5, stop_multiple=2.0)
    defaults.update(overrides)
    inst = instruments_repo.create_instrument(conn, **defaults)
    portfolio_service.record_trade(
        conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00"
    )
    return inst


def test_telegram_dummy_mode_when_credentials_missing():
    assert telegram_client.is_dummy_mode() is True
    assert telegram_client.send_message("테스트") is True  # 더미 모드는 항상 성공 취급


def test_telegram_send_failure_returns_false_without_raising(monkeypatch):
    from atrsite.config import settings
    object.__setattr__(settings, "telegram_bot_token", "dummy-token")
    object.__setattr__(settings, "telegram_chat_id", "dummy-chat")
    try:
        def _boom(*a, **kw):
            raise telegram_client.httpx.ConnectError("network down")
        monkeypatch.setattr(telegram_client.httpx, "post", _boom)
        assert telegram_client.send_message("테스트") is False
    finally:
        object.__setattr__(settings, "telegram_bot_token", "")
        object.__setattr__(settings, "telegram_chat_id", "")


def test_build_message_buy_has_no_recommendation_wording(db_conn):
    inst = _make_instrument_with_position(db_conn)
    portfolio_service.commit_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=88)  # 다음매수가 90 이하

    signal = portfolio_service.instrument_snapshot(db_conn, inst["id"])["signal"]
    assert signal["status"] == SignalStatus.BUY_TRIGGERED.value

    text = notification_service.build_message(
        instrument=instruments_repo_get(db_conn, inst["id"]),
        position=portfolio_service.instrument_snapshot(db_conn, inst["id"])["position"],
        quote=portfolio_service.instrument_snapshot(db_conn, inst["id"])["quote"],
        atr_row=portfolio_service.instrument_snapshot(db_conn, inst["id"])["atr"],
        signal_row=signal,
    )
    assert "[ATR 매수 기준 도달]" in text
    assert "추천 수량" not in text
    assert "실제 주문은 MTS에서 직접 확인" in text
    assert "매수 기준가: 90" in text


def test_build_message_buy_shows_tranche_based_quantity_when_configured(db_conn):
    from atrsite.repositories import instruments as instruments_repo
    inst = _make_instrument_with_position(db_conn)
    instruments_repo.update_settings(db_conn, inst["id"], tranche_amount=700000)
    portfolio_service.commit_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=88)  # 다음매수가 90

    snapshot = portfolio_service.instrument_snapshot(db_conn, inst["id"])
    text = notification_service.build_message(
        instrument=snapshot["instrument"], position=snapshot["position"],
        quote=snapshot["quote"], atr_row=snapshot["atr"], signal_row=snapshot["signal"],
    )
    assert "등록된 트랜치 금액: 700,000" in text
    assert "계산 가능 수량: 7777주" in text  # floor(700000 / 90) = 7777
    assert "추천 수량" not in text


def instruments_repo_get(conn, instrument_id):
    from atrsite.repositories import instruments as instruments_repo
    return instruments_repo.get_instrument(conn, instrument_id)


def test_recompute_signal_enqueues_outbox_on_transition_and_dedupes(db_conn):
    inst = _make_instrument_with_position(db_conn)
    portfolio_service.commit_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")

    portfolio_service.commit_quote(db_conn, inst["id"], price=140)  # 익절가 115 도달 -> TAKE_PROFIT
    portfolio_service.commit_quote(db_conn, inst["id"], price=141)  # 여전히 익절 상태 -> 중복 없어야 함

    pending = notifications_repo.list_pending(db_conn)
    assert len(pending) == 1
    assert "[ATR 익절 기준 도달]" in pending[0]["payload"]


def test_neutral_transition_does_not_enqueue_outbox(db_conn):
    inst = _make_instrument_with_position(db_conn)
    portfolio_service.commit_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=100)  # 관망 구간(NEUTRAL)
    assert notifications_repo.list_pending(db_conn) == []


def test_process_outbox_marks_sent_in_dummy_mode(db_conn):
    inst = _make_instrument_with_position(db_conn)
    portfolio_service.commit_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=88)  # BUY_TRIGGERED

    result = notification_service.process_outbox(db_conn)
    assert result == {"sent": 1, "retried": 0, "failed": 0}

    row = notifications_repo.list_pending(db_conn)
    assert row == []  # SENT는 더 이상 pending이 아님


def test_process_outbox_retries_then_fails_after_four_attempts(db_conn, monkeypatch):
    inst = _make_instrument_with_position(db_conn)
    portfolio_service.commit_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=88)  # BUY_TRIGGERED -> outbox 1건

    monkeypatch.setattr(notification_service.telegram_client, "send_message", lambda text: False)

    now = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)

    # 1차 실패 -> 30초 후 재시도 예약
    r1 = notification_service.process_outbox(db_conn, now=now)
    assert r1 == {"sent": 0, "retried": 1, "failed": 0}
    row = notifications_repo.list_pending(db_conn, now=(now + timedelta(seconds=5)).isoformat())
    assert row == []  # 아직 백오프 시간이 안 지남 -> 재시도 대상 아님

    # 2차 실패 -> 2분 후
    now2 = now + timedelta(seconds=31)
    r2 = notification_service.process_outbox(db_conn, now=now2)
    assert r2 == {"sent": 0, "retried": 1, "failed": 0}

    # 3차 실패 -> 10분 후
    now3 = now2 + timedelta(seconds=121)
    r3 = notification_service.process_outbox(db_conn, now=now3)
    assert r3 == {"sent": 0, "retried": 1, "failed": 0}

    # 4차 실패 -> 실패 확정 (더 이상 재시도 안 함)
    now4 = now3 + timedelta(seconds=601)
    r4 = notification_service.process_outbox(db_conn, now=now4)
    assert r4 == {"sent": 0, "retried": 0, "failed": 1}

    # 그 이후로는 시간이 아무리 지나도 다시 집히지 않아야 함(FAILED는 terminal)
    r5 = notification_service.process_outbox(db_conn, now=now4 + timedelta(days=1))
    assert r5 == {"sent": 0, "retried": 0, "failed": 0}
