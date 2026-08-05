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
        is_etf                 INTEGER NOT NULL DEFAULT 0,
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
    """
    CREATE TABLE IF NOT EXISTS analysis_results (
        id            TEXT PRIMARY KEY,
        created_at    TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        result_text   TEXT NOT NULL,
        provider      TEXT NOT NULL,
        model         TEXT NOT NULL
    )
    """,
    # ---- 종목탐구(company-explorer) -- 2026-08-03 추가 --------------------
    # 기존 analysis_results(매크로 브리핑)와 이름이 겹치지 않게 전부 company_
    # 접두사. 별도 DB로 분리하지 않고 같은 atrsite.db에 둠(크로스 DB 조인
    # 불필요, 백업 스크립트가 이미 단일 파일 기준 -- 과설계 방지).
    """
    CREATE TABLE IF NOT EXISTS companies (
        id                TEXT PRIMARY KEY,
        country           TEXT NOT NULL CHECK(country IN ('KR', 'US')),
        security_type     TEXT NOT NULL DEFAULT 'STOCK' CHECK(security_type IN ('STOCK', 'ETF')),
        name              TEXT NOT NULL,
        name_en           TEXT,
        currency          TEXT NOT NULL,
        exchange          TEXT,
        primary_ticker    TEXT NOT NULL,
        sec_cik           TEXT,
        dart_corp_code    TEXT,
        industry_template TEXT,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_identifiers (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        id_type    TEXT NOT NULL CHECK(id_type IN ('TICKER', 'KRX_CODE', 'CIK', 'NAME_ALIAS')),
        id_value   TEXT NOT NULL,
        UNIQUE(id_type, id_value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_filings (
        id                      TEXT PRIMARY KEY,
        company_id              TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        source                  TEXT NOT NULL CHECK(source IN ('SEC', 'DART')),
        filing_type             TEXT NOT NULL,
        filed_at                TEXT,
        period_end              TEXT,
        accession_or_receipt_no TEXT,
        url                     TEXT,
        fetched_at              TEXT NOT NULL,
        UNIQUE(source, accession_or_receipt_no)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_financial_periods (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        fiscal_year   INTEGER NOT NULL,
        fiscal_period TEXT NOT NULL CHECK(fiscal_period IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')),
        period_type   TEXT NOT NULL CHECK(period_type IN ('QUARTER', 'ANNUAL')),
        period_end    TEXT NOT NULL,
        filed_at      TEXT,
        source        TEXT NOT NULL,
        fetched_at    TEXT NOT NULL,
        -- fiscal_year/fiscal_period가 아니라 period_end로 유일성을 잡는다 --
        -- SEC 원본 데이터의 fy/fp 태그는 비교 컬럼(전년 동기 등)에 실제
        -- 기간과 다른 값이 붙는 경우가 실제로 있어(2026-08-03 라이브
        -- 검증 중 발견: 서로 다른 두 분기가 둘 다 "2026 Q1"로 태깅됨)
        -- 유일성 기준으로 못 씀. period_end(실제 날짜)는 항상 신뢰 가능.
        UNIQUE(company_id, period_end, period_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_financial_metrics (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        period_id   INTEGER NOT NULL REFERENCES company_financial_periods(id) ON DELETE CASCADE,
        metric_key  TEXT NOT NULL,
        value       REAL,
        unit        TEXT,
        verdict     TEXT CHECK(verdict IS NULL OR verdict IN
                      ('IMPROVING', 'STABLE', 'DETERIORATING', 'CAUTION', 'INSUFFICIENT_DATA')),
        basis_note  TEXT,
        UNIQUE(period_id, metric_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_analysis_requests (
        id             TEXT PRIMARY KEY,
        company_id     TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        status         TEXT NOT NULL DEFAULT 'PENDING',
        error_message  TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_analysis_results (
        id            TEXT PRIMARY KEY,
        request_id    TEXT NOT NULL REFERENCES company_analysis_requests(id) ON DELETE CASCADE,
        company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        created_at    TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        result_text   TEXT NOT NULL,
        provider      TEXT NOT NULL,
        model         TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_company_identifiers_company ON company_identifiers(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_filings_company ON company_filings(company_id, filed_at)",
    "CREATE INDEX IF NOT EXISTS idx_company_periods_company ON company_financial_periods(company_id, period_end)",
    "CREATE INDEX IF NOT EXISTS idx_company_metrics_period ON company_financial_metrics(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_analysis_results_company ON company_analysis_results(company_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_trades_instrument ON trades(instrument_id, executed_at, sequence_no)",
    "CREATE INDEX IF NOT EXISTS idx_daily_bars_instrument ON daily_bars(instrument_id, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_atr_values_instrument ON atr_values(instrument_id, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_signal_events_instrument ON signal_events(instrument_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notification_outbox_status ON notification_outbox(status, next_attempt_at)",
    """
    -- backend/atrsite/schema.py -- v1.1 현금 출금기록(개인용 장부, 2026-08-05 추가).
    -- 스펙 10절 "중요한 비연동 원칙": 이 테이블은 예금(deposits)/포지션/거래이력과
    -- 완전히 독립적인 기록일 뿐이다. deposits.amount를 자동으로 변경하지 않는다.
    CREATE TABLE IF NOT EXISTS cash_withdrawals (
        id                     TEXT PRIMARY KEY,
        -- 실제 출금 일시. Asia/Seoul 기준 naive 문자열(YYYY-MM-DDTHH:MM:SS)로
        -- 저장한다 -- created_at/updated_at(UTC ISO, utcnow_iso())과 다른 이유는
        -- 스펙 3.1: 오늘/이번주/이번달/YTD 합계를 서버·브라우저 모두 Asia/Seoul
        -- 기준으로 동일하게 계산해야 해서(서버 시스템 시간대 자체가 이미
        -- Asia/Seoul로 맞춰져 있음 -- market_schedule.py와 동일한 전제).
        withdrawn_at           TEXT NOT NULL,
        -- deposits가 하드 삭제되면 NULL로 남는다(스펙: 계좌를 삭제해도 출금기록은
        -- 지우지 않음) -- account_name_snapshot으로 과거 계좌를 계속 식별한다.
        deposit_account_id     TEXT REFERENCES deposits(id) ON DELETE SET NULL,
        account_name_snapshot  TEXT NOT NULL,
        purpose                TEXT NOT NULL,
        amount                 REAL NOT NULL,
        currency               TEXT NOT NULL DEFAULT 'KRW',
        memo                   TEXT,
        -- created_at/updated_at 비교(초 단위 정밀도)로는 생성 직후 같은 초 안에
        -- 수정된 경우 "수정됨"을 못 잡아내서(2026-08-05 테스트로 실제 발견된
        -- 버그) 명시적 플래그로 스펙 9.5 "수정됨" 표시를 처리한다.
        edited                 INTEGER NOT NULL DEFAULT 0,
        created_at             TEXT NOT NULL,
        updated_at             TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cash_withdrawals_withdrawn_at ON cash_withdrawals(withdrawn_at)",
    "CREATE INDEX IF NOT EXISTS idx_cash_withdrawals_deposit_account_id ON cash_withdrawals(deposit_account_id)",
    "CREATE INDEX IF NOT EXISTS idx_cash_withdrawals_currency ON cash_withdrawals(currency)",
    "CREATE INDEX IF NOT EXISTS idx_cash_withdrawals_purpose ON cash_withdrawals(purpose)",
]
