"""portfolio_service 통합 테스트 -- DB + position_engine + signal_engine이
실제로 맞물려 돌아가는지 확인한다 (1단계 순수함수 테스트와 달리 SQLite를 사용).
"""
import pytest

from atrsite.repositories import deposits as deposits_repo
from atrsite.repositories import fx as fx_repo
from atrsite.repositories import instruments as instruments_repo
from atrsite.repositories import position as position_repo
from atrsite.repositories import trades as trades_repo
from atrsite.services import portfolio_service
from atrsite.services.position_engine import OversellError
from atrsite.services.signal_engine import SignalStatus


def _make_instrument(conn, **overrides):
    defaults = dict(name="테스트종목", currency="KRW", buy_multiple=1.0, sell_multiple=1.5, stop_multiple=2.0)
    defaults.update(overrides)
    return instruments_repo.create_instrument(conn, **defaults)


def test_spec_mandatory_average_price_example(db_conn):
    """스펙 필수 예시: 10주x100원 매수 -> 5주x120원 매도 -> 5주x200원 매수 -> 평균단가 150원."""
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="sell", price=120, quantity=5, executed_at="2026-01-02T09:00:00")
    trade = portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=200, quantity=5, executed_at="2026-01-03T09:00:00")

    position = position_repo.get_position(db_conn, inst["id"])
    assert position["quantity"] == pytest.approx(10)
    assert position["avg_price"] == pytest.approx(150)
    assert trade["price"] == 200


def test_oversell_is_rejected_and_nothing_persisted(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    with pytest.raises(OversellError):
        portfolio_service.record_trade(db_conn, inst["id"], trade_type="sell", price=100, quantity=11, executed_at="2026-01-02T09:00:00")
    trades = trades_repo.list_trades_for_instrument(db_conn, inst["id"])
    assert len(trades) == 1  # 초과매도 시도는 저장되지 않아야 함


def test_new_position_sets_post_entry_high_and_full_exit_resets_it(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    instrument = instruments_repo.get_instrument(db_conn, inst["id"])
    assert instrument["post_entry_high_price"] == pytest.approx(100)

    portfolio_service.record_trade(db_conn, inst["id"], trade_type="sell", price=110, quantity=10, executed_at="2026-01-02T09:00:00")
    instrument = instruments_repo.get_instrument(db_conn, inst["id"])
    assert instrument["post_entry_high_price"] is None
    assert instrument["trailing_stop_price"] is None


def test_ratchet_survives_across_quote_and_atr_commits(db_conn):
    """최고가 100/ATR10/배수2 -> 손절선 80, 이후 최고가 110 -> 손절선 90 유지."""
    inst = _make_instrument(db_conn, stop_multiple=2.0)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    instrument = instruments_repo.get_instrument(db_conn, inst["id"])
    assert instrument["trailing_stop_price"] == pytest.approx(80)

    portfolio_service.commit_quote(db_conn, inst["id"], price=110)
    instrument = instruments_repo.get_instrument(db_conn, inst["id"])
    assert instrument["post_entry_high_price"] == pytest.approx(110)
    assert instrument["trailing_stop_price"] == pytest.approx(90)


def test_signal_state_transitions_and_events_are_deduplicated(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")

    portfolio_service.commit_quote(db_conn, inst["id"], price=140)  # 익절가 100+10*1.5=115 도달
    snap1 = portfolio_service.instrument_snapshot(db_conn, inst["id"])
    assert snap1["signal"]["status"] == SignalStatus.TAKE_PROFIT_TRIGGERED.value

    portfolio_service.commit_quote(db_conn, inst["id"], price=141)  # 여전히 익절 상태 -> 새 이벤트 없음
    from atrsite.repositories import signals as signals_repo
    events = signals_repo.list_signal_events(db_conn, inst["id"])
    take_profit_events = [e for e in events if e["new_status"] == SignalStatus.TAKE_PROFIT_TRIGGERED.value]
    assert len(take_profit_events) == 1


def test_edit_trade_reconciles_position_and_signal(db_conn):
    inst = _make_instrument(db_conn)
    trade = portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.edit_trade(db_conn, inst["id"], trade["id"], quantity=5)
    position = position_repo.get_position(db_conn, inst["id"])
    assert position["quantity"] == pytest.approx(5)
    assert position["avg_price"] == pytest.approx(100)


def test_delete_last_trade_resets_to_flat(db_conn):
    inst = _make_instrument(db_conn)
    trade = portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.delete_trade(db_conn, inst["id"], trade["id"])
    position = position_repo.get_position(db_conn, inst["id"])
    assert position["quantity"] == pytest.approx(0)
    instrument = instruments_repo.get_instrument(db_conn, inst["id"])
    assert instrument["post_entry_high_price"] is None


def test_strategy_settings_versioning(db_conn):
    inst = _make_instrument(db_conn, buy_multiple=1.0)
    updated = instruments_repo.update_settings(db_conn, inst["id"], buy_multiple=2.0)
    assert updated["buy_multiple"] == pytest.approx(2.0)
    row = db_conn.execute(
        "SELECT COUNT(*) AS c FROM strategy_settings WHERE instrument_id = ?", (inst["id"],)
    ).fetchone()
    assert row["c"] == 2  # 최초 버전 + 변경 버전


def test_deposits_and_fx_roundtrip(db_conn):
    dep = deposits_repo.create_deposit(db_conn, account_name="증권계좌", amount=1000000, currency="USD")
    assert dep["currency"] == "USD"
    fx_repo.upsert_rate(db_conn, "USD", 1350.5)
    rates = fx_repo.list_rates(db_conn)
    assert rates["USD"]["rate_to_krw"] == pytest.approx(1350.5)
    assert rates["KRW"]["rate_to_krw"] == 1.0

    fx_repo.set_display_currency(db_conn, "USD")
    assert fx_repo.get_display_currency(db_conn) == "USD"
