"""backend/atrsite/services/snapshot_service.py 테스트 -- v1.3 자산 스냅샷 계산.

build_dashboard()와 동일한 통화환산/누락처리 공식을 재사용하므로, 여러 테스트가
build_dashboard()의 totals와 직접 비교해서 "대시보드와 스냅샷이 정확히 일치"
(스펙 21절 완료조건)를 검증한다.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from atrsite.repositories import deposits as deposits_repo
from atrsite.repositories import fx as fx_repo
from atrsite.repositories import instruments as instruments_repo
from atrsite.services import portfolio_service, snapshot_service


def _make_instrument(conn, **overrides):
    defaults = dict(name="테스트종목", currency="KRW", buy_multiple=1.0, sell_multiple=1.5, stop_multiple=2.0)
    defaults.update(overrides)
    return instruments_repo.create_instrument(conn, **defaults)


def test_matches_build_dashboard_total_asset(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_quote(db_conn, inst["id"], price=1200, source="manual", data_status="MANUAL_OVERRIDE")
    deposits_repo.create_deposit(db_conn, account_name="테스트계좌", amount=500000, currency="KRW")

    dashboard = portfolio_service.build_dashboard(db_conn)
    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))

    assert snap["total_asset_value"] == pytest.approx(dashboard["totals"]["asset_total"])
    assert snap["investment_market_value"] == pytest.approx(dashboard["totals"]["eval_value"])
    assert snap["cash_deposit_value"] == pytest.approx(dashboard["totals"]["deposit_total"])
    assert snap["unrealized_profit"] == pytest.approx(dashboard["totals"]["pnl_amount"])
    assert snap["profit_rate"] == pytest.approx(dashboard["totals"]["pnl_pct"])


def test_investment_and_cash_split(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_quote(db_conn, inst["id"], price=1000, source="manual")
    deposits_repo.create_deposit(db_conn, account_name="예금", amount=300000, currency="KRW")

    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["investment_market_value"] == pytest.approx(10000)
    assert snap["cash_deposit_value"] == pytest.approx(300000)
    assert snap["total_asset_value"] == pytest.approx(310000)


def test_realized_profit_aggregated_from_trade_replay(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="sell", price=150, quantity=4, executed_at="2026-01-02T09:00:00")
    # 실현손익 = (150-100)*4 = 200

    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["realized_profit"] == pytest.approx(200)


def test_realized_profit_survives_full_exit(db_conn):
    """전량 매도해서 보유수량이 0이 된 종목도 realized_profit 집계에는 남아야
    하고, 평가 항목(investment_market_value)에는 포함되지 않아야 한다."""
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=100, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="sell", price=130, quantity=10, executed_at="2026-01-02T09:00:00")

    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["realized_profit"] == pytest.approx(300)
    assert snap["investment_market_value"] == pytest.approx(0)
    assert snap["included_instrument_count"] == 0
    instrument_items = [i for i in snap["items"] if i["item_type"] == "INSTRUMENT"]
    assert instrument_items == []


def test_currency_breakdown_in_original_units(db_conn):
    fx_repo.upsert_rate(db_conn, "USD", 1400.0)
    inst_krw = _make_instrument(db_conn, name="국내종목", currency="KRW")
    inst_usd = _make_instrument(db_conn, name="해외종목", currency="USD")
    portfolio_service.record_trade(db_conn, inst_krw["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_quote(db_conn, inst_krw["id"], price=1000, source="manual")
    portfolio_service.record_trade(db_conn, inst_usd["id"], trade_type="buy", price=100, quantity=5, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_quote(db_conn, inst_usd["id"], price=100, source="manual")
    deposits_repo.create_deposit(db_conn, account_name="달러예금", amount=1000, currency="USD")

    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["krw_asset_value"] == pytest.approx(10000)
    assert snap["usd_asset_value"] == pytest.approx(500 + 1000)  # 종목평가 500 + 예금 1000
    assert snap["usd_krw_rate"] == pytest.approx(1400.0)


def test_missing_price_marks_partial(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    # 시세 커밋 안 함 -- MISSING_PRICE

    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["data_quality_status"] == "PARTIAL"
    assert snap["failed_quote_count"] == 1
    assert "시세 누락" in snap["quality_notes"]


def test_missing_fx_marks_partial(db_conn):
    inst = _make_instrument(db_conn, currency="JPY")  # fx_rates에 JPY 없음
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_quote(db_conn, inst["id"], price=1000, source="manual")

    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["data_quality_status"] == "PARTIAL"
    assert snap["failed_quote_count"] == 1


def test_no_instruments_or_deposits_is_failed(db_conn):
    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["data_quality_status"] == "FAILED"


def test_stale_kis_price_marks_stale_status(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    old_quoted_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    portfolio_service.commit_quote(db_conn, inst["id"], price=1000, source="kis", data_status="FRESH", quoted_at=old_quoted_at)

    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["data_quality_status"] == "STALE_PRICE"
    items = [i for i in snap["items"] if i["item_type"] == "INSTRUMENT"]
    assert items[0]["data_status"] == "STALE"


def test_manual_quote_never_marked_stale_even_if_old(db_conn):
    """market_schedule.compute_data_status 자체 원칙: 수동 입력 시세는 신선도
    재계산 대상이 아니다(사용자가 방금 입력한 값을 오래된 데이터로 재판정 금지)."""
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    old_quoted_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    portfolio_service.commit_quote(db_conn, inst["id"], price=1000, source="manual", data_status="MANUAL_OVERRIDE", quoted_at=old_quoted_at)

    snap = snapshot_service.build_snapshot(db_conn, snapshot_date=date(2026, 8, 5))
    assert snap["data_quality_status"] == "COMPLETE"


def test_manual_trigger_relabels_complete_as_manual(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    portfolio_service.commit_quote(db_conn, inst["id"], price=1000, source="manual")

    snap = snapshot_service.build_snapshot(
        db_conn, snapshot_date=date(2026, 8, 5), triggered_by="manual",
    )
    assert snap["data_quality_status"] == "MANUAL"


def test_manual_trigger_keeps_partial_label_when_incomplete(db_conn):
    inst = _make_instrument(db_conn)
    portfolio_service.record_trade(db_conn, inst["id"], trade_type="buy", price=1000, quantity=10, executed_at="2026-01-01T09:00:00")
    # 시세 없음 -> PARTIAL이어야 하고, manual 트리거라고 MANUAL로 덮어쓰면 안 됨.

    snap = snapshot_service.build_snapshot(
        db_conn, snapshot_date=date(2026, 8, 5), triggered_by="manual",
    )
    assert snap["data_quality_status"] == "PARTIAL"
