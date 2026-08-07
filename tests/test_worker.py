"""worker.py 파이프라인 테스트 -- 3단계 목표: 더미 시세->ATR->신호가 텔레그램
연결 없이도 끊김 없이 돌아가는지 확인한다 (프롬프트 3단계 지시).

이 개발 환경의 .env에는 이제 실제 KIS 자격증명이 들어 있으므로(4단계 이후
실전 계좌 연동), 아래 autouse 픽스처로 매 테스트마다 강제로 더미 모드를
켠다 -- 안 그러면 이 테스트들이 실제 KIS 서버에 접속을 시도하게 된다.
"""
import pytest

from datetime import date, datetime

from atrsite import worker
from atrsite.config import settings
from atrsite.repositories import instruments as instruments_repo
from atrsite.repositories import market_data as market_data_repo
from atrsite.repositories import signals as signals_repo


@pytest.fixture(autouse=True)
def force_kis_dummy_mode():
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    yield
    object.__setattr__(settings, "kis_app_key", original_key)
    object.__setattr__(settings, "kis_app_secret", original_secret)


@pytest.fixture(autouse=True)
def reset_daily_bars_collection_state():
    """run_once()의 "하루 한 번만 일봉 수집" 가드는 모듈 전역 상태라, 테스트마다
    깨끗한 상태에서 시작하도록 초기화한다(안 그러면 같은 날짜를 쓰는 다른
    테스트끼리 서로 영향을 줄 수 있음)."""
    worker._last_daily_bars_collection_date = None
    yield
    worker._last_daily_bars_collection_date = None


@pytest.fixture(autouse=True)
def reset_snapshot_retry_state():
    """v1.3 자산 스냅샷 재시도 카운터도 모듈 전역 상태 -- 테스트 간 격리."""
    worker._snapshot_last_check_date = None
    worker._snapshot_attempts_today = 0
    yield
    worker._snapshot_last_check_date = None
    worker._snapshot_attempts_today = 0


@pytest.fixture(autouse=True)
def force_telegram_dummy_mode_for_worker():
    from atrsite.config import settings
    original_token, original_chat = settings.telegram_bot_token, settings.telegram_chat_id
    object.__setattr__(settings, "telegram_bot_token", "")
    object.__setattr__(settings, "telegram_chat_id", "")
    yield
    object.__setattr__(settings, "telegram_bot_token", original_token)
    object.__setattr__(settings, "telegram_chat_id", original_chat)


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


# ---------------------------------------------------------------------------
# 2026-08-07 매매계획 감시 기능 조사 중 발견: worker의 유일한 폴링 게이트가
# KRX 09:00~15:30 뿐이라 QQQ 등 미국 상장 종목이 한국 야간(=실제 미국
# 정규장)에 전혀 자동 폴링되지 않았다. market_group 분리로 고친 결과 검증.
# ---------------------------------------------------------------------------

def _make_us_instrument(conn, name="QQQ", kis_code="QQQ", kis_market="NAS"):
    inst = instruments_repo.create_instrument(conn, name=name, currency="USD")
    instruments_repo.update_settings(conn, inst["id"], kis_code=kis_code, kis_market=kis_market)
    return instruments_repo.get_instrument(conn, inst["id"])


def test_poll_quotes_krx_group_skips_us_instrument(db_conn):
    krx_inst = _make_instrument_with_kis_code(db_conn)
    us_inst = _make_us_instrument(db_conn)
    worker.poll_quotes(db_conn, market_group="KRX")
    assert market_data_repo.get_quote(db_conn, krx_inst["id"]) is not None
    assert market_data_repo.get_quote(db_conn, us_inst["id"]) is None


def test_poll_quotes_us_group_skips_krx_instrument(db_conn):
    krx_inst = _make_instrument_with_kis_code(db_conn)
    us_inst = _make_us_instrument(db_conn)
    worker.poll_quotes(db_conn, market_group="US")
    assert market_data_repo.get_quote(db_conn, us_inst["id"]) is not None
    assert market_data_repo.get_quote(db_conn, krx_inst["id"]) is None


def test_run_once_polls_qqq_during_us_regular_hours_even_when_krx_closed(db_conn, monkeypatch):
    """한국시간 야간(=미국 정규장) -- KRX는 HOLIDAY/PRE_MARKET이어도 QQQ는
    자동으로 폴링돼야 한다. 2026-08-05(수) 22:45 KST는 여름(EDT) 미국
    정규장 중(개장 22:30 KST)이면서 이미 KRX 정규장(09:00~15:30)은 지난
    한밤중이라 KRX 쪽은 POST_MARKET/HOLIDAY 구간이다."""
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    us_inst = _make_us_instrument(db_conn)

    worker.run_once(now=datetime(2026, 8, 5, 22, 45))
    assert market_data_repo.get_quote(db_conn, us_inst["id"]) is not None


def test_sleep_interval_uses_tighter_of_two_markets():
    from atrsite.services.market_schedule import MarketPhase as MP
    # KRX 휴장(30분)이어도 미국이 정규장(5분)이면 5분을 써야 한다.
    assert worker._sleep_seconds_for(MP.HOLIDAY, now=datetime(2026, 8, 5, 22, 45), us_phase=MP.REGULAR) == 300
    # 둘 다 유휴 상태면 60초.
    assert worker._sleep_seconds_for(MP.PRE_MARKET, now=datetime(2026, 8, 5, 3, 0), us_phase=MP.PRE_MARKET) == 60


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


def test_run_once_only_collects_bars_once_per_day(db_conn, monkeypatch):
    """POST_MARKET은 장마감부터 자정까지 몇 시간이나 이어지고 그 사이 매분
    run_once()가 호출되는데, 같은 날짜 안에서는 일봉/ATR을 한 번만 수집해야
    한다(안 그러면 자정까지 KIS를 매분 반복 호출하게 됨)."""
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    _make_instrument_with_kis_code(db_conn)

    call_count = {"n": 0}
    original = worker.collect_daily_bars_and_update_atr

    def counting(conn, *, market_group="KRX"):
        call_count["n"] += 1
        return original(conn, market_group=market_group)

    monkeypatch.setattr(worker, "collect_daily_bars_and_update_atr", counting)

    worker.run_once(now=datetime(2026, 8, 4, 16, 0))   # 화요일 마감 직후
    worker.run_once(now=datetime(2026, 8, 4, 20, 0))   # 같은 날 저녁 -- 재수집 안 해야 함
    worker.run_once(now=datetime(2026, 8, 4, 23, 55))  # 같은 날 자정 직전 -- 마찬가지
    assert call_count["n"] == 1

    worker.run_once(now=datetime(2026, 8, 5, 16, 0))   # 다음 거래일 마감 후 -- 다시 수집
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# v1.3 자산 스냅샷 -- 06:30/06:40/07:00 재시도 + upsert(중복방지)
# ---------------------------------------------------------------------------

def _seed_holding_for_snapshot(conn):
    inst = instruments_repo.create_instrument(conn, name="스냅샷테스트종목", currency="KRW")
    from atrsite.services import portfolio_service
    portfolio_service.record_trade(conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-08-01T09:00:00")
    portfolio_service.commit_quote(conn, inst["id"], price=1200, source="manual")
    return inst


def test_snapshot_not_attempted_before_0630(db_conn, monkeypatch):
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    _seed_holding_for_snapshot(db_conn)

    worker.run_once(now=datetime(2026, 8, 5, 6, 0))
    from atrsite.repositories import snapshots as snapshots_repo
    assert snapshots_repo.get_snapshot(db_conn, "2026-08-05") is None


def test_snapshot_created_at_0630(db_conn, monkeypatch):
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    _seed_holding_for_snapshot(db_conn)

    worker.run_once(now=datetime(2026, 8, 5, 6, 30))
    from atrsite.repositories import snapshots as snapshots_repo
    snap = snapshots_repo.get_snapshot(db_conn, "2026-08-05")
    assert snap is not None
    assert snap["data_quality_status"] == "COMPLETE"


def test_snapshot_not_duplicated_on_repeated_run_once(db_conn, monkeypatch):
    """같은 날짜에 run_once()가 여러 번 불려도(worker 중복실행/재시작 시나리오)
    스냅샷 행이 하나만 존재해야 한다(스펙 9절 idempotency)."""
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    _seed_holding_for_snapshot(db_conn)

    worker.run_once(now=datetime(2026, 8, 5, 6, 30))
    worker.run_once(now=datetime(2026, 8, 5, 6, 31))
    worker.run_once(now=datetime(2026, 8, 5, 6, 35))

    from atrsite.repositories import snapshots as snapshots_repo
    rows = snapshots_repo.list_snapshots(db_conn, start_date="2026-08-05", end_date="2026-08-05")
    assert len(rows) == 1


def test_snapshot_retries_on_partial_then_stops_after_success(db_conn, monkeypatch):
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    inst = instruments_repo.create_instrument(db_conn, name="지연입력종목", currency="KRW")
    from atrsite.services import portfolio_service
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-08-01T09:00:00")
    # 아직 시세 없음 -> 06:30 시도는 PARTIAL

    worker.run_once(now=datetime(2026, 8, 5, 6, 30))
    from atrsite.repositories import snapshots as snapshots_repo
    snap = snapshots_repo.get_snapshot(db_conn, "2026-08-05")
    assert snap["data_quality_status"] == "PARTIAL"

    # 06:40 재시도 전에 시세가 들어옴 -> 이번엔 COMPLETE로 갱신(같은 행 upsert)
    portfolio_service.commit_quote(db_conn, inst["id"], price=1200, source="manual")
    worker.run_once(now=datetime(2026, 8, 5, 6, 40))
    snap = snapshots_repo.get_snapshot(db_conn, "2026-08-05")
    assert snap["data_quality_status"] == "COMPLETE"

    rows = snapshots_repo.list_snapshots(db_conn, start_date="2026-08-05", end_date="2026-08-05")
    assert len(rows) == 1  # 갱신이지 새 행이 아님


def test_snapshot_gives_up_after_third_retry(db_conn, monkeypatch):
    monkeypatch.setattr(worker.db, "connect", lambda *a, **kw: _NoCloseConnWrapper(db_conn))
    instruments_repo.create_instrument(db_conn, name="영구실패종목", currency="JPY")  # JPY 환율 없음 -> 항상 PARTIAL
    from atrsite.services import portfolio_service
    inst = instruments_repo.list_instruments(db_conn)[0]
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-08-01T09:00:00")
    portfolio_service.commit_quote(db_conn, inst["id"], price=1200, source="manual")

    worker.run_once(now=datetime(2026, 8, 5, 6, 30))
    worker.run_once(now=datetime(2026, 8, 5, 6, 40))
    worker.run_once(now=datetime(2026, 8, 5, 7, 0))
    assert worker._snapshot_attempts_today == 3

    # 4번째 호출(07:10)은 이미 소진돼서 재시도하지 않음 -- attempts 그대로.
    worker.run_once(now=datetime(2026, 8, 5, 7, 10))
    assert worker._snapshot_attempts_today == 3


def test_sleep_seconds_shortened_during_snapshot_window():
    from atrsite.services.market_schedule import MarketPhase
    seconds = worker._sleep_seconds_for(MarketPhase.HOLIDAY, now=datetime(2026, 8, 5, 6, 45))
    assert seconds == worker.IDLE_POLL_INTERVAL_SECONDS  # 휴장일 30분 간격이 아니라 60초


def test_sleep_seconds_normal_outside_snapshot_window():
    from atrsite.services.market_schedule import MarketPhase
    seconds = worker._sleep_seconds_for(MarketPhase.HOLIDAY, now=datetime(2026, 8, 5, 10, 0))
    assert seconds == worker.HOLIDAY_POLL_INTERVAL_SECONDS
