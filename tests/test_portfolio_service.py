"""portfolio_service 통합 테스트 -- DB + position_engine + signal_engine이
실제로 맞물려 돌아가는지 확인한다 (1단계 순수함수 테스트와 달리 SQLite를 사용).
"""
import pytest

from atrsite.repositories import deposits as deposits_repo
from atrsite.repositories import fx as fx_repo
from atrsite.repositories import instruments as instruments_repo
from atrsite.repositories import market_data as market_data_repo
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


# ---------------------------------------------------------------------------
# 리뷰에서 지적된 누락 테스트들 (2026-08-02 추가)
# ---------------------------------------------------------------------------

def test_editing_earlier_trade_that_creates_oversell_is_rejected_and_original_kept(db_conn):
    """과거 거래를 수정해서 중간 시점 초과매도가 생기면 전체가 거부돼야 하고,
    원래 거래 내역은 그대로 남아 있어야 한다 (부분 반영 금지)."""
    inst = _make_instrument(db_conn)
    buy = portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="sell", price=110, quantity=8, executed_at="2026-01-02T09:00:00")
    # 첫 매수를 10주 -> 5주로 줄이면, 이미 8주를 판 두 번째 거래가 초과매도가 된다.
    with pytest.raises(OversellError):
        portfolio_service.edit_trade(db_conn, inst["id"], buy["id"], quantity=5)

    # 거부됐으니 매수 거래 수량은 원래 값(10) 그대로여야 한다.
    unchanged = trades_repo.get_trade(db_conn, buy["id"])
    assert unchanged["quantity"] == pytest.approx(10)
    position = position_repo.get_position(db_conn, inst["id"])
    assert position["quantity"] == pytest.approx(2)  # 10 - 8, 원래 상태 그대로


def test_reentry_after_full_exit_starts_a_fresh_cycle(db_conn):
    """전량 매도 후 새로 매수하면, 이전 사이클의 최고가/손절선 흔적 없이 새
    진입가를 기준으로 완전히 새 사이클이 시작돼야 한다 (스펙 8.4)."""
    inst = _make_instrument(db_conn, stop_multiple=2.0)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=120)  # 최고가 120, 손절선 100

    portfolio_service.record_trade(db_conn, inst["id"], trade_type="sell", price=125, quantity=10, executed_at="2026-01-05T09:00:00")
    instrument = instruments_repo.get_instrument(db_conn, inst["id"])
    assert instrument["post_entry_high_price"] is None
    assert instrument["trailing_stop_price"] is None

    # 새로 진입 -- 이전 사이클의 고가(120)/손절선(100) 흔적이 전혀 없어야 한다.
    # ATR은 포지션 사이클에 종속된 값이 아니라 종목 자체의 변동성이므로, 아직
    # 새 ATR을 안 받았어도 "마지막으로 알려진 ATR"(10)로 즉시 손절선을 계산한다
    # (재진입 순간 보호선이 하나도 없는 것보다 낫다) -- 50 - 10*2 = 30.
    # 이전 사이클 손절선(100)의 흔적이 섞이지 않고 완전히 새 레칫(30)에서
    # 시작한다는 게 핵심이지, "ATR도 초기화"라는 뜻은 아니다.
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=50, quantity=10, executed_at="2026-01-06T09:00:00")
    instrument = instruments_repo.get_instrument(db_conn, inst["id"])
    assert instrument["post_entry_high_price"] == pytest.approx(50)
    assert instrument["trailing_stop_price"] == pytest.approx(30)  # 50 - (이전 ATR)10*2, 과거 100과 무관한 새 레칫

    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=5, trade_date="2026-01-06")
    instrument = instruments_repo.get_instrument(db_conn, inst["id"])
    assert instrument["trailing_stop_price"] == pytest.approx(40)  # 50 - 5*2, 과거 100의 흔적 없음


def test_trades_repo_assigns_sequence_no_in_insertion_order_for_same_executed_at(db_conn):
    """같은 executed_at(초 단위까지 동일)에 여러 거래가 들어와도, 저장소가 매기는
    sequence_no가 삽입 순서를 보존해서 재생 순서가 흔들리지 않아야 한다 (스펙 4.5)."""
    inst = _make_instrument(db_conn)
    same_ts = "2026-01-01T09:00:00"
    t1 = trades_repo.create_trade(db_conn, instrument_id=inst["id"], trade_type="buy", price=100, quantity=10, executed_at=same_ts)
    t2 = trades_repo.create_trade(db_conn, instrument_id=inst["id"], trade_type="sell", price=120, quantity=4, executed_at=same_ts)
    t3 = trades_repo.create_trade(db_conn, instrument_id=inst["id"], trade_type="buy", price=90, quantity=4, executed_at=same_ts)

    assert t1["sequence_no"] < t2["sequence_no"] < t3["sequence_no"]

    ordered = trades_repo.list_trades_for_instrument(db_conn, inst["id"])
    assert [t["id"] for t in ordered] == [t1["id"], t2["id"], t3["id"]]

    portfolio_service.recompute_position(db_conn, inst["id"])
    position = position_repo.get_position(db_conn, inst["id"])
    # buy10@100(avg100) -> sell4@120(avg100유지,qty6,cost600) -> buy4@90(cost600+360=960,qty10,avg96)
    assert position["quantity"] == pytest.approx(10)
    assert position["avg_price"] == pytest.approx(96)


def test_kis_sourced_quote_becomes_stale_after_elapsed_time_but_manual_does_not(db_conn):
    """KIS 소스 시세는 시간이 지나면 자동으로 STALE로 전환돼 신규 신호를 막아야
    하고, 수동 입력 시세는 아무리 오래돼도 MANUAL_OVERRIDE를 유지해야 한다."""
    from datetime import datetime, timedelta, timezone as tz
    from atrsite.repositories import market_data as market_data_repo

    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")

    old_ts = (datetime.now(tz.utc) - timedelta(minutes=20)).isoformat()
    market_data_repo.upsert_quote(db_conn, inst["id"], price=88, source="kis", data_status="FRESH", quoted_at=old_ts)
    signal = portfolio_service.recompute_signal(db_conn, inst["id"])
    assert signal["data_status"] == "STALE"
    assert signal["status"] not in ("BUY_TRIGGERED", "TAKE_PROFIT_TRIGGERED", "STOP_TRIGGERED", "SELL_BOTH_TRIGGERED")

    # 같은 만큼 오래된 수동 입력은 그대로 MANUAL_OVERRIDE 유지 (신호는 정상 판정)
    market_data_repo.upsert_quote(db_conn, inst["id"], price=88, source="manual", data_status="MANUAL_OVERRIDE", quoted_at=old_ts)
    signal2 = portfolio_service.recompute_signal(db_conn, inst["id"])
    assert signal2["data_status"] == "MANUAL_OVERRIDE"
    assert signal2["status"] == "BUY_TRIGGERED"  # 88 <= 90(다음 매수가) -> 정상 판정됨


def test_consecutive_kis_failures_force_api_error_status(db_conn):
    from atrsite.repositories import market_data as market_data_repo

    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_quote(db_conn, inst["id"], price=95, source="kis", data_status="FRESH")

    for _ in range(3):
        market_data_repo.record_quote_failure(db_conn, inst["id"])
    signal = portfolio_service.recompute_signal(db_conn, inst["id"])
    assert signal["data_status"] == "API_ERROR"


# ---------------------------------------------------------------------------
# 신호 확인(acknowledge) -- 손절 등 트리거 신호는 가격 반등으로 자동으로
# 조용히 사라지면 안 되고, 사람이 직접 확인해야 사라진다는 사용자 결정
# (2026-08-02)에 따른 기능.
# ---------------------------------------------------------------------------

def test_new_trigger_signal_starts_unacknowledged(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    signal = portfolio_service.commit_quote(db_conn, inst["id"], price=79)  # 손절선 80 이하 -> STOP_TRIGGERED
    snapshot = portfolio_service.instrument_snapshot(db_conn, inst["id"])
    assert snapshot["signal"]["status"] == "STOP_TRIGGERED"
    assert snapshot["signal"]["latest_event_id"] is not None
    assert snapshot["signal"]["acknowledged_event_id"] is None  # 아직 미확인


def test_acknowledge_matches_latest_event_and_price_flicker_does_not_reopen_it(db_conn):
    """확인을 누르면 acknowledged_event_id가 latest_event_id와 같아진다.
    이후 같은 상태가 계속 반복 계산돼도(새 이벤트가 안 생기므로) 미확인으로
    되돌아가면 안 된다 -- 매 폴링마다 배너가 다시 뜨는 걸 방지."""
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=79)  # STOP_TRIGGERED

    acked = portfolio_service.acknowledge_signal(db_conn, inst["id"])
    assert acked["acknowledged_event_id"] == acked["latest_event_id"]

    # 가격이 살짝 흔들려도(여전히 손절선 이하) 같은 상태가 반복 계산될 뿐이므로
    # latest_event_id가 안 바뀌고, 따라서 확인 상태도 그대로 유지돼야 한다.
    portfolio_service.commit_quote(db_conn, inst["id"], price=78)
    still_acked = portfolio_service.instrument_snapshot(db_conn, inst["id"])["signal"]
    assert still_acked["status"] == "STOP_TRIGGERED"
    assert still_acked["acknowledged_event_id"] == still_acked["latest_event_id"]


def test_new_episode_after_acknowledge_becomes_unacknowledged_again(db_conn):
    """확인한 뒤에 상태가 실제로 바뀌었다가(예: 관망) 다시 손절 조건을 충족하면
    -- 새 이벤트이므로 -- 다시 미확인 상태로 배너가 떠야 한다."""
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=79)  # STOP_TRIGGERED
    portfolio_service.acknowledge_signal(db_conn, inst["id"])

    portfolio_service.commit_quote(db_conn, inst["id"], price=95)  # 손절선(80)/익절가(115) 다 벗어남 -> NEUTRAL
    neutral_signal = portfolio_service.instrument_snapshot(db_conn, inst["id"])["signal"]
    assert neutral_signal["status"] == "NEUTRAL"

    portfolio_service.commit_quote(db_conn, inst["id"], price=79)  # 다시 STOP_TRIGGERED -- 새 사이클
    reopened = portfolio_service.instrument_snapshot(db_conn, inst["id"])["signal"]
    assert reopened["status"] == "STOP_TRIGGERED"
    assert reopened["acknowledged_event_id"] != reopened["latest_event_id"]  # 다시 미확인


def test_acknowledge_does_not_change_computed_status_or_thresholds(db_conn):
    """확인은 배너 표시 여부만 바꿀 뿐, 계산된 손절선/신호 상태 자체는 그대로여야
    한다 -- 손절선 표시가 사라지거나 잘못된 값이 되면 안 됨."""
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(db_conn, inst["id"], atr=10, trade_date="2026-01-01")
    portfolio_service.commit_quote(db_conn, inst["id"], price=79)

    before = portfolio_service.instrument_snapshot(db_conn, inst["id"])
    portfolio_service.acknowledge_signal(db_conn, inst["id"])
    after = portfolio_service.instrument_snapshot(db_conn, inst["id"])

    assert after["signal"]["status"] == before["signal"]["status"] == "STOP_TRIGGERED"
    assert after["instrument"]["trailing_stop_price"] == before["instrument"]["trailing_stop_price"]


# ---------------------------------------------------------------------------
# 온디맨드 시세 갱신(refresh_quote_now) + day_high 반영 (2026-08-02 추가)
# ---------------------------------------------------------------------------

def test_commit_quote_uses_day_high_for_auto_update_when_higher_than_price(db_conn):
    """장중 순간적으로 더 높이 찍고 내려온 경우, 현재가보다 당일고가를
    최고가 갱신 기준으로 써야 한다 (스펙 11.3)."""
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    updated = portfolio_service.commit_quote(db_conn, inst["id"], price=105, day_high=110)
    assert updated["post_entry_high_price"] == pytest.approx(110)


def test_commit_quote_without_day_high_falls_back_to_price(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    updated = portfolio_service.commit_quote(db_conn, inst["id"], price=105)  # 수동 입력, day_high 없음
    assert updated["post_entry_high_price"] == pytest.approx(105)


@pytest.fixture()
def force_kis_dummy_mode():
    from atrsite.config import settings
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    yield
    object.__setattr__(settings, "kis_app_key", original_key)
    object.__setattr__(settings, "kis_app_secret", original_secret)


def test_refresh_quote_now_requires_kis_code(db_conn, force_kis_dummy_mode):
    inst = _make_instrument(db_conn)  # kis_code 미설정
    with pytest.raises(portfolio_service.RefreshQuoteError):
        portfolio_service.refresh_quote_now(db_conn, inst["id"])


def test_refresh_quote_now_commits_dummy_quote_when_kis_code_set(db_conn, force_kis_dummy_mode):
    inst = _make_instrument(db_conn)
    instruments_repo.update_settings(db_conn, inst["id"], kis_code="005930", kis_market="KRX")

    updated = portfolio_service.refresh_quote_now(db_conn, inst["id"])
    assert updated is not None

    snapshot = portfolio_service.instrument_snapshot(db_conn, inst["id"])
    assert snapshot["quote"]["source"] == "kis"
    assert snapshot["quote"]["data_status"] == "FRESH"


def test_refresh_all_quotes_now_updates_only_instruments_with_kis_code(db_conn, force_kis_dummy_mode):
    with_code = _make_instrument(db_conn, name="코드있음")
    instruments_repo.update_settings(db_conn, with_code["id"], kis_code="005930", kis_market="KRX")
    without_code = _make_instrument(db_conn, name="코드없음")

    result = portfolio_service.refresh_all_quotes_now(db_conn)
    assert result["updated"] == ["코드있음"]
    assert result["skipped"] == ["코드없음"]
    assert result["failed"] == []

    assert market_data_repo.get_quote(db_conn, with_code["id"])["source"] == "kis"
    assert market_data_repo.get_quote(db_conn, without_code["id"]) is None


# 2026-08-06 -- "주가 갱신" 버튼을 누르면 환율도 같이 자동 갱신(사용자 요청).
# fx_rate_client는 항상 monkeypatch로 대체해서 실제 frankfurter.dev를 호출하지
# 않는다(다른 외부 어댑터들과 동일한 테스트 원칙).

def test_refresh_all_quotes_now_refreshes_fx_for_currencies_in_use(db_conn, force_kis_dummy_mode, monkeypatch):
    _make_instrument(db_conn, name="달러종목", currency="USD")
    deposits_repo.create_deposit(db_conn, account_name="엔화예금", amount=100000, currency="JPY")

    calls = []

    def fake_fetch(currency, **kwargs):
        calls.append(currency)
        return {"USD": 1423.72, "JPY": 9.0343}.get(currency)

    monkeypatch.setattr(portfolio_service.fx_rate_client, "fetch_rate_to_krw", fake_fetch)

    result = portfolio_service.refresh_all_quotes_now(db_conn)
    assert sorted(calls) == ["JPY", "USD"]
    assert sorted(result["fx_updated"]) == ["JPY", "USD"]
    assert result["fx_failed"] == []

    rates = fx_repo.list_rates(db_conn)
    assert rates["USD"]["rate_to_krw"] == pytest.approx(1423.72)
    assert rates["USD"]["source"] == "auto"
    assert rates["JPY"]["rate_to_krw"] == pytest.approx(9.0343)


def test_refresh_all_quotes_now_skips_krw_only_portfolio(db_conn, force_kis_dummy_mode, monkeypatch):
    _make_instrument(db_conn, name="원화종목", currency="KRW")

    def fake_fetch(currency, **kwargs):
        raise AssertionError("KRW만 있으면 환율 조회를 시도하면 안 됨")

    monkeypatch.setattr(portfolio_service.fx_rate_client, "fetch_rate_to_krw", fake_fetch)

    result = portfolio_service.refresh_all_quotes_now(db_conn)
    assert result["fx_updated"] == []
    assert result["fx_failed"] == []


def test_refresh_all_quotes_now_keeps_old_rate_when_fx_fetch_fails(db_conn, force_kis_dummy_mode, monkeypatch):
    _make_instrument(db_conn, name="달러종목", currency="USD")
    fx_repo.upsert_rate(db_conn, "USD", 1300.0, source="manual")

    monkeypatch.setattr(portfolio_service.fx_rate_client, "fetch_rate_to_krw", lambda currency, **kw: None)

    result = portfolio_service.refresh_all_quotes_now(db_conn)
    assert result["fx_updated"] == []
    assert result["fx_failed"] == ["USD"]

    rates = fx_repo.list_rates(db_conn)
    assert rates["USD"]["rate_to_krw"] == pytest.approx(1300.0)  # 실패해도 기존 값 그대로
    assert rates["USD"]["source"] == "manual"  # source도 안 바뀜


# ---------------------------------------------------------------------------
# 2026-08-06 실전 버그 회귀 테스트 -- STALE/API_ERROR 중에도 기존 트리거 상태가
# 사라지면 안 된다(GPT 코드리뷰로 발견, 실전 재현됨). 데이터 정상 복귀까지 포함.
# ---------------------------------------------------------------------------

def _setup_stop_triggered(conn):
    """trailing_stop_price=90(익절가는 100+10*1.5=115라 90~115 구간이 "관망")으로
    세팅하고, 현재가 70(KIS 소스, FRESH)으로 STOP_TRIGGERED를 실제 발생시킨다.

    매수 체결(record_trade) 자체도 내부적으로 recompute_signal()을 한 번
    호출해서 "포지션 없음 -> 관망(NEUTRAL)" 이벤트가 하나 먼저 생긴다 -- 이건
    이번 버그 수정과 무관한 기존 동작이라, 이 시점의 실제 이벤트 개수를
    baseline으로 반환해서 테스트가 매직넘버 대신 이 값과 비교하게 한다."""
    from atrsite.repositories import signals as signals_repo

    inst = _make_instrument(conn)
    portfolio_service.record_trade(conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_manual_atr(conn, inst["id"], atr=10, trade_date="2026-01-01")
    instruments_repo.update_manual_fields(conn, inst["id"], trailing_stop_price=90)

    portfolio_service.commit_quote(conn, inst["id"], price=70, source="kis", data_status="FRESH")

    signal = signals_repo.get_signal_state(conn, inst["id"])
    assert signal["status"] == "STOP_TRIGGERED"
    baseline_event_count = len(signals_repo.list_signal_events(conn, inst["id"]))
    return inst, signal, baseline_event_count


def test_stale_data_preserves_stop_triggered_status_and_acknowledged_state(db_conn):
    from atrsite.repositories import notifications as notifications_repo
    from atrsite.repositories import signals as signals_repo
    from datetime import datetime, timedelta, timezone as tz

    inst, signal, baseline_events = _setup_stop_triggered(db_conn)
    original_event_id = signal["latest_event_id"]
    assert original_event_id is not None
    assert len(notifications_repo.list_pending(db_conn)) == 1  # STOP_TRIGGERED만 알림 대상

    portfolio_service.acknowledge_signal(db_conn, inst["id"])
    acked = signals_repo.get_signal_state(db_conn, inst["id"])
    assert acked["acknowledged_event_id"] == original_event_id

    # 시세가 20분 전 값으로 굳어짐(KIS 장애/지연 시뮬레이션) -> STALE 전환.
    old_ts = (datetime.now(tz.utc) - timedelta(minutes=20)).isoformat()
    portfolio_service.commit_quote(
        db_conn, inst["id"], price=70, source="kis", data_status="FRESH", quoted_at=old_ts,
    )
    stale_signal = signals_repo.get_signal_state(db_conn, inst["id"])

    # data_status만 바뀌고 나머지는 전부 그대로 보존돼야 한다.
    assert stale_signal["data_status"] == "STALE"
    assert stale_signal["status"] == "STOP_TRIGGERED"  # <- 이게 이번 수정의 핵심
    assert stale_signal["latest_event_id"] == original_event_id
    assert stale_signal["acknowledged_event_id"] == original_event_id  # 확인 상태도 유지
    assert stale_signal["trailing_stop_price"] == pytest.approx(90)

    # 새 이벤트도 중복 알림도 생기면 안 된다.
    assert len(signals_repo.list_signal_events(db_conn, inst["id"])) == baseline_events
    assert len(notifications_repo.list_pending(db_conn)) == 1


def test_data_recovery_with_unchanged_condition_stays_acknowledged(db_conn):
    """장애 중 상태가 보존된 뒤, 데이터가 정상 복귀했는데 조건(가격<=손절선)이
    그대로면 -- 같은 상태로 재판정되고 확인 상태도 계속 유지돼야 한다(새
    이벤트/알림 없음)."""
    from atrsite.repositories import notifications as notifications_repo
    from atrsite.repositories import signals as signals_repo
    from datetime import datetime, timedelta, timezone as tz

    inst, signal, baseline_events = _setup_stop_triggered(db_conn)
    original_event_id = signal["latest_event_id"]
    portfolio_service.acknowledge_signal(db_conn, inst["id"])

    old_ts = (datetime.now(tz.utc) - timedelta(minutes=20)).isoformat()
    portfolio_service.commit_quote(db_conn, inst["id"], price=70, source="kis", data_status="FRESH", quoted_at=old_ts)

    # 데이터 정상 복귀 -- 가격은 그대로 70(여전히 손절선 90 아래).
    portfolio_service.commit_quote(db_conn, inst["id"], price=70, source="kis", data_status="FRESH")
    recovered_signal = signals_repo.get_signal_state(db_conn, inst["id"])

    assert recovered_signal["data_status"] == "FRESH"
    assert recovered_signal["status"] == "STOP_TRIGGERED"
    assert recovered_signal["latest_event_id"] == original_event_id  # 새 이벤트 아님
    assert recovered_signal["acknowledged_event_id"] == original_event_id  # 계속 확인된 상태
    assert len(signals_repo.list_signal_events(db_conn, inst["id"])) == baseline_events
    assert len(notifications_repo.list_pending(db_conn)) == 1


def test_data_recovery_with_changed_condition_clears_signal(db_conn):
    """장애 중엔 상태가 보존되지만, 데이터가 정상 복귀했을 때 실제로 가격이
    손절선(90) 위 + 익절가(115) 아래인 "관망" 구간으로 돌아와 있으면 정상적으로
    풀려야 한다 -- 장애 보존 로직이 신호를 "영구 고정"시키는 게 아님을 확인."""
    from atrsite.repositories import signals as signals_repo
    from datetime import datetime, timedelta, timezone as tz

    inst, signal, _baseline_events = _setup_stop_triggered(db_conn)

    old_ts = (datetime.now(tz.utc) - timedelta(minutes=20)).isoformat()
    portfolio_service.commit_quote(db_conn, inst["id"], price=70, source="kis", data_status="FRESH", quoted_at=old_ts)

    # 데이터 정상 복귀 + 가격이 90~115 관망 구간(100)으로 회복 -> 손절 해제돼야 함.
    portfolio_service.commit_quote(db_conn, inst["id"], price=100, source="kis", data_status="FRESH")
    recovered_signal = signals_repo.get_signal_state(db_conn, inst["id"])

    assert recovered_signal["data_status"] == "FRESH"
    assert recovered_signal["status"] == "NEUTRAL"


def test_api_error_status_also_preserves_existing_signal(db_conn):
    """연속 3회 조회 실패(API_ERROR)도 STALE과 동일하게 기존 신호를 보존해야 한다."""
    from atrsite.repositories import signals as signals_repo

    inst, signal, _baseline_events = _setup_stop_triggered(db_conn)
    original_event_id = signal["latest_event_id"]

    for _ in range(3):
        market_data_repo.record_quote_failure(db_conn, inst["id"])
    portfolio_service.recompute_signal(db_conn, inst["id"])

    api_error_signal = signals_repo.get_signal_state(db_conn, inst["id"])
    assert api_error_signal["data_status"] == "API_ERROR"
    assert api_error_signal["status"] == "STOP_TRIGGERED"
    assert api_error_signal["latest_event_id"] == original_event_id


def test_blocked_data_on_first_ever_computation_creates_safe_initial_state(db_conn):
    """이 종목에 signal_state 행이 아예 없던 첫 계산이 하필 데이터 장애 상태로
    시작해도(예: 등록 직후 시세가 아직 없음) 크래시 없이 안전한 초기값(관망/
    미보유)을 만들어야 한다 -- "보존할 기존 상태가 없는" 경계 케이스."""
    from atrsite.repositories import signals as signals_repo

    inst = _make_instrument(db_conn)
    # 시세를 한 번도 커밋하지 않음 -> quote is None -> INSUFFICIENT_DATA, 그리고
    # signal_state 행 자체가 아직 없는 상태에서 recompute_signal을 직접 호출.
    result = portfolio_service.recompute_signal(db_conn, inst["id"])
    assert result["data_status"] == "INSUFFICIENT_DATA"
    assert result["status"] == "NO_POSITION"

    stored = signals_repo.get_signal_state(db_conn, inst["id"])
    assert stored is not None
    assert stored["status"] == "NO_POSITION"
