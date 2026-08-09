from atrsite.repositories import fx as fx_repo
from atrsite.repositories import instruments as instruments_repo
from atrsite.repositories import trades as trades_repo
from atrsite.services import realized_pnl_service


def _instrument(conn, name="QQQ", currency="USD"):
    return instruments_repo.create_instrument(conn, name=name, currency=currency)


def _trade(conn, instrument_id, trade_type, price, quantity, executed_at, fee=0.0, tax=0.0):
    return trades_repo.create_trade(
        conn,
        instrument_id=instrument_id,
        trade_type=trade_type,
        price=price,
        quantity=quantity,
        executed_at=executed_at,
        fee=fee,
        tax=tax,
    )


def test_realized_pnl_replays_full_history_but_filters_sells_by_period(db_conn):
    fx_repo.upsert_rate(db_conn, "USD", 1300)
    inst = _instrument(db_conn)
    _trade(db_conn, inst["id"], "buy", 100, 10, "2026-07-01T09:00:00+09:00")
    _trade(db_conn, inst["id"], "buy", 200, 10, "2026-07-20T09:00:00+09:00")
    _trade(db_conn, inst["id"], "sell", 220, 5, "2026-08-03T09:00:00+09:00")
    _trade(db_conn, inst["id"], "sell", 180, 5, "2026-09-01T09:00:00+09:00")

    data = realized_pnl_service.realized_pnl_for_period(db_conn, "2026-08-01", "2026-08-31", "KRW")

    assert data["total_realized_pnl"] == 455000
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["realized_pnl_native"] == 350
    assert item["realized_pnl_converted"] == 455000
    assert item["sell_count"] == 1
    assert item["sells"][0]["realized_pnl_native"] == 350


def test_realized_pnl_includes_start_and_end_date_boundaries(db_conn):
    inst = _instrument(db_conn, currency="KRW")
    _trade(db_conn, inst["id"], "buy", 1000, 10, "2026-08-01T00:00:00+09:00")
    _trade(db_conn, inst["id"], "sell", 1200, 2, "2026-08-01T00:00:00+09:00")
    _trade(db_conn, inst["id"], "sell", 1300, 2, "2026-08-09T23:59:59+09:00")

    data = realized_pnl_service.realized_pnl_for_period(db_conn, "2026-08-01", "2026-08-09", "KRW")

    assert data["total_realized_pnl"] == 1000
    assert data["items"][0]["sell_count"] == 2


def test_realized_pnl_handles_empty_period(db_conn):
    inst = _instrument(db_conn, currency="KRW")
    _trade(db_conn, inst["id"], "buy", 1000, 10, "2026-08-01T09:00:00")
    _trade(db_conn, inst["id"], "sell", 1200, 2, "2026-08-10T09:00:00")

    data = realized_pnl_service.realized_pnl_for_period(db_conn, "2026-08-01", "2026-08-09", "KRW")

    assert data["total_realized_pnl"] == 0
    assert data["items"] == []


def test_realized_pnl_rejects_invalid_date_range(db_conn):
    try:
        realized_pnl_service.realized_pnl_for_period(db_conn, "2026-08-09", "2026-08-01", "KRW")
    except ValueError as exc:
        assert "start_date" in str(exc)
    else:
        raise AssertionError("expected ValueError")
