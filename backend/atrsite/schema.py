"""SQLite DDL -- spec 10.1 core tables + 10.2 constraints.

All statements use CREATE TABLE IF NOT EXISTS so init_db() is idempotent and
safe to call on every process startup (web and worker both call it).
"""
from __future__ import annotations

SCHEMA_VERSION = 1

DDL_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS instruments (
        id                     TEXT PRIMARY KEY,
        name                   TEXT NOT NULL,
        currency               TEXT NOT NULL DEFAULT 'KRW',
        kis_code               TEXT,
        kis_market              TEXT,
        auto_update_high       INTEGER NOT NULL DEFAULT 1,
        post_entry_high_price  REAL,
        trailing_stop_price    REAL,
        is_active              INTEGER NOT NULL DEFAULT 1,
        created_at             TEXT NOT NULL,
        updated_at             TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_settings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        instrument_id   TEXT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
        buy_multiple    REAL NOT NULL,
        sell_multiple   REAL NOT NULL,
        stop_multiple   REAL NOT NULL,
        tranche_amount  REAL,
        version         INTEGER NOT NULL,
        created_at      TEXT NOT NULL,
        UNIQUE(instrument_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id                TEXT PRIMARY KEY,
        instrument_id     TEXT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
        trade_type        TEXT NOT NULL CHECK(trade_type IN ('buy', 'sell')),
        price             REAL NOT NULL CHECK(price > 0),
        quantity          REAL NOT NULL CHECK(quantity > 0),
        executed_at       TEXT NOT NULL,
        sequence_no       INTEGER NOT NULL DEFAULT 0,
        fee               REAL,
        tax               REAL,
        memo              TEXT,
        atr_at_execution  REAL,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_state (
        instrument_id    TEXT PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
        quantity         REAL NOT NULL DEFAULT 0,
        avg_price        REAL NOT NULL DEFAULT 0,
        cost_basis       REAL NOT NULL DEFAULT 0,
        last_buy_price   REAL,
        last_sell_price  REAL,
        last_trade_price REAL,
        updated_at       TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_bars (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        instrument_id  TEXT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
        trade_date     TEXT NOT NULL,
        high           REAL NOT NULL,
        low            REAL NOT NULL,
        close          REAL NOT NULL,
        UNIQUE(instrument_id, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS atr_values (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        instrument_id  TEXT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
        trade_date     TEXT NOT NULL,
        period         INTEGER NOT NULL,
        atr            REAL NOT NULL,
        source         TEXT NOT NULL DEFAULT 'manual',
        UNIQUE(instrument_id, trade_date, period)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quote_latest (
        instrument_id        TEXT PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
        price                REAL NOT NULL,
        quoted_at            TEXT NOT NULL,
        source               TEXT NOT NULL DEFAULT 'manual',
        data_status          TEXT NOT NULL DEFAULT 'MANUAL_OVERRIDE',
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        change_pct           REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_state (
        instrument_id        TEXT PRIMARY KEY REFERENCES instruments(id) ON DELETE CASCADE,
        status                TEXT NOT NULL,
        data_status           TEXT NOT NULL,
        next_buy_price        REAL,
        take_profit_price     REAL,
        trailing_stop_price   REAL,
        reason                TEXT,
        computed_at           TEXT NOT NULL,
        -- 가장 최근 signal_events.id (상태 전환마다 갱신). 사용자가 "확인"을
        -- 누르면 acknowledged_event_id를 이 값으로 맞춰서 배너를 끈다. 이후
        -- 같은 상태가 계속돼도(새 이벤트가 안 생기므로) 다시 안 뜨고, 상태가
        -- 실제로 또 바뀌면(새 이벤트) 다시 미확인 상태가 되어 배너가 뜬다.
        latest_event_id       INTEGER,
        acknowledged_event_id INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        instrument_id     TEXT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
        previous_status   TEXT,
        new_status        TEXT NOT NULL,
        created_at        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_outbox (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_event_id   INTEGER NOT NULL REFERENCES signal_events(id) ON DELETE CASCADE,
        channel           TEXT NOT NULL,
        status            TEXT NOT NULL DEFAULT 'PENDING',
        payload           TEXT NOT NULL,
        attempt_count     INTEGER NOT NULL DEFAULT 0,
        next_attempt_at   TEXT,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL,
        UNIQUE(signal_event_id, channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deposits (
        id            TEXT PRIMARY KEY,
        account_name  TEXT NOT NULL,
        amount        REAL NOT NULL,
        currency      TEXT NOT NULL DEFAULT 'KRW',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fx_rates (
        currency    TEXT PRIMARY KEY,
        rate_to_krw REAL NOT NULL,
        source      TEXT NOT NULL DEFAULT 'manual',
        updated_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type     TEXT NOT NULL,
        started_at   TEXT NOT NULL,
        finished_at  TEXT,
        status       TEXT NOT NULL DEFAULT 'RUNNING',
        detail       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type  TEXT NOT NULL,
        entity_id    TEXT NOT NULL,
        action       TEXT NOT NULL,
        before_json  TEXT,
        after_json   TEXT,
        created_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trades_instrument ON trades(instrument_id, executed_at, sequence_no)",
    "CREATE INDEX IF NOT EXISTS idx_daily_bars_instrument ON daily_bars(instrument_id, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_atr_values_instrument ON atr_values(instrument_id, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_signal_events_instrument ON signal_events(instrument_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notification_outbox_status ON notification_outbox(status, next_attempt_at)",
]
