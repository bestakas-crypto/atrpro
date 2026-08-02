"""worker.py 파이프라인 테스트 -- 3단계 목표: 더미 시세->ATR->신호가 텔레그램
연결 없이도 끊김 없이 돌아가는지 확인한다 (프롬프트 3단계 지시).
"""
from datetime import datetime

from atrsite import worker
from atrsite.repositories import instruments as instruments_repo
from atrsite.repositories import market_data as market_data_repo
from atrsite.repositories import signals as signals_repo


def _make_instrument_with_kis_code(conn, **overrides):
    defaults = dict(name="테스트", currency="KRW", buy_multiple=1.0, sell_multiple=1.5, stop_multiple=2.0)
    defaults.update(overrides)
    inst = instruments_repo.create_instrument(conn, **defaults)
    instruments_repo.update_settings(conn, inst["id"], kis_code="005930", kis_market="KRX")
    return instruments_repo.get_instrument(conn, inst["id"])


class _NoCloseConnWrapper:
    """run_once()가 자체적으로 conn.close()를 호출하므로, 테스트 픽스처가 관리하는
    db_conn을 그대로 넘기면 이후 assert에서 못 쓰게 된다. sqlite3.Connection은
    C 확장 객체라 메서드를 직접 monkeypatch할 수 없어서(read-only 속성) 얇은
    프록시로 감싸 close()만 무시한다."""

    def __init__(self, real_conn):
        self._real = real_conn

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


def test_poll_quotes_updates_quote_and_signal(db_conn):
    inst = _make_instrument_with_kis_code(db_conn)
    worker.poll_quotes(db_conn)

    quote = market_data_repo.get_quote(db_conn, inst["id"])
    assert quote is not None
    assert quote["source"] == "kis"
    assert quote["data_status"] == "FRESH"

    signal = signals_repo.get_signal_state(db_conn, inst["id"])
    assert signal is not None  # 신호 판정까지 파이프라인이 끊기지 않고 돌아감


def test_poll_quotes_skips_instrument_without_kis_code(db_conn):
    inst = instruments_repo.create_instrument(db_conn, name="코드없음")
    worker.poll_quotes(db_conn)
    assert market_data_repo.get_quote(db_conn, inst["id"]) is None


def test_collect_daily_bars_updates_atr_and_ratchet(db_conn):
    inst = _make_instrument_with_kis_code(db_conn)
    # 보유 포지션이 있어야 손절선 레칫이 의미가 있으므로 매수 체결을 하나 넣는다.
    from atrsite.services import portfolio_service
    portfolio_service.record_trade(
        db_conn, inst["id"], trade_type="buy", price=50000, quantity=10, executed_at="2026-07-01T09:00:00"
    )

    worker.collect_daily_bars_and_update_atr(db_conn)

    atr_row = market_data_repo.get_latest_atr(db_conn, inst["id"])
    assert atr_row is not None
    assert atr_row["source"] == "kis"
    assert atr_row["atr"] > 0

    updated = instruments_repo.get_instrument(db_conn, inst["id"])
    assert updated["trailing_stop_price"] is not None


def test_run_once_regular_phase_polls_quotes_not_bars(db_conn, monkeypatch):
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    inst = _make_instrument_with_kis_code(db_conn)

    phase = worker.run_once(now=datetime(2026, 8, 4, 10, 0))  # 화요일 정규장
    assert phase.value == "REGULAR"
    assert market_data_repo.get_quote(db_conn, inst["id"]) is not None


def test_run_once_post_market_phase_collects_bars(db_conn, monkeypatch):
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    inst = _make_instrument_with_kis_code(db_conn)

    phase = worker.run_once(now=datetime(2026, 8, 4, 16, 0))  # 화요일 마감 후
    assert phase.value == "POST_MARKET"
    assert market_data_repo.get_latest_atr(db_conn, inst["id"]) is not None
