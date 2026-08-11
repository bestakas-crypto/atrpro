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
        updated_at             TEXT NOT NULL,
        -- v1.4(2026-08-12) analyze.kunoh.top 연동을 위한 순입금 계산 준비:
        -- 이 출금이 "외부로 나가는 소비"(EXTERNAL, 자산 총액에서 실제로 빠짐)
        -- 인지 "내부 계좌 이동"(INTERNAL_TRANSFER, 예: 다른 예금계좌로 이체 --
        -- 총자산에는 영향 없음)인지 구분한다. 기존 행은 전부 소비 목적으로
        -- 쓰였다는 스펙 원문 전제("생활비"류 용도)에 따라 기본값 EXTERNAL.
        -- 신규 DB는 여기서, 이미 운영 중인 DB는 db.py의 _add_column_if_missing로
        -- 채운다(둘 다 기본값 EXTERNAL이라 결과는 동일).
        flow_type              TEXT NOT NULL DEFAULT 'EXTERNAL'
                                CHECK(flow_type IN ('EXTERNAL', 'INTERNAL_TRANSFER'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cash_withdrawals_withdrawn_at ON cash_withdrawals(withdrawn_at)",
    "CREATE INDEX IF NOT EXISTS idx_cash_withdrawals_deposit_account_id ON cash_withdrawals(deposit_account_id)",
    "CREATE INDEX IF NOT EXISTS idx_cash_withdrawals_currency ON cash_withdrawals(currency)",
    "CREATE INDEX IF NOT EXISTS idx_cash_withdrawals_purpose ON cash_withdrawals(purpose)",

    """
    -- backend/atrsite/schema.py -- v1.4 현금 입금기록(analyze.kunoh.top 1단계,
    -- 2026-08-12 추가). cash_withdrawals(v1.1)의 정반대 짝 -- 예금(deposits)
    -- 잔액이 늘어난 "사건"을 기록한다. deposits.amount는 지금까지 현재 잔액만
    -- 들고 있고 변경 이력이 없어서(순수 스냅샷), "이번 달에 급여 300만원이
    -- 새로 들어왔다" 같은 걸 시스템이 구분할 방법이 없었다 -- 이게 정확한
    -- TWR(시간가중수익률)을 지금 당장 계산할 수 없는 근본 원인이었다
    -- (analyze.kunoh.top 기획 검증 중 확인). 이 테이블이 그 원장 역할을 한다.
    -- withdrawals와 마찬가지로 deposits/포지션/거래이력을 자동으로 갱신하지
    -- 않는 완전히 독립적인 기록이다 -- 사용자가 deposits.amount를 직접 늘릴
    -- 때, 동시에 여기에도 이벤트를 남겨야 뒤에서 순입금을 계산할 수 있다.
    CREATE TABLE IF NOT EXISTS cash_inflows (
        id                     TEXT PRIMARY KEY,
        -- withdrawn_at과 동일한 이유로 Asia/Seoul naive 문자열로 저장.
        deposited_at           TEXT NOT NULL,
        deposit_account_id     TEXT REFERENCES deposits(id) ON DELETE SET NULL,
        account_name_snapshot  TEXT NOT NULL,
        -- withdrawals의 purpose(용도)에 대응하는 "출처" -- 예: 급여, 상여금, 이체.
        source                 TEXT NOT NULL,
        amount                 REAL NOT NULL,
        currency               TEXT NOT NULL DEFAULT 'KRW',
        memo                   TEXT,
        -- 자산 총액에 실제로 새 돈이 들어온 것(EXTERNAL, 예: 급여)인지, 다른
        -- 계좌/브로커리지에서 옮겨온 내부 이동(INTERNAL_TRANSFER, 총자산 불변)
        -- 인지 구분 -- 이게 있어야 순입금(EXTERNAL만)을 정확히 걸러 합산한다.
        flow_type              TEXT NOT NULL DEFAULT 'EXTERNAL'
                                CHECK(flow_type IN ('EXTERNAL', 'INTERNAL_TRANSFER')),
        edited                 INTEGER NOT NULL DEFAULT 0,
        created_at             TEXT NOT NULL,
        updated_at             TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cash_inflows_deposited_at ON cash_inflows(deposited_at)",
    "CREATE INDEX IF NOT EXISTS idx_cash_inflows_deposit_account_id ON cash_inflows(deposit_account_id)",
    "CREATE INDEX IF NOT EXISTS idx_cash_inflows_currency ON cash_inflows(currency)",
    "CREATE INDEX IF NOT EXISTS idx_cash_inflows_source ON cash_inflows(source)",

    # ---- v1.2 -- 통합 투자 스케줄 및 예약알림 --------------------------------
    # "계좌" 개념은 이 프로젝트에 별도 테이블이 없다 -- deposits가 사실상 유일한
    # 계좌 엔티티(현금/예금 계좌)라서 cash_withdrawals와 동일하게
    # deposit_account_id(FK, ON DELETE SET NULL) + account_name_snapshot 패턴을
    # 그대로 재사용한다. instruments도 마찬가지로 삭제돼도 과거 일정 기록이
    # 깨지지 않도록 instrument_name_snapshot을 둔다.
    """
    CREATE TABLE IF NOT EXISTS investment_plans (
        id                     TEXT PRIMARY KEY,
        name                   TEXT NOT NULL,
        deposit_account_id     TEXT REFERENCES deposits(id) ON DELETE SET NULL,
        account_name_snapshot  TEXT,
        total_amount           REAL,
        currency               TEXT NOT NULL DEFAULT 'KRW',
        status                 TEXT NOT NULL DEFAULT 'ACTIVE',
        memo                   TEXT,
        created_at             TEXT NOT NULL,
        updated_at             TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_plan_items (
        id                         TEXT PRIMARY KEY,
        plan_id                    TEXT NOT NULL REFERENCES investment_plans(id) ON DELETE CASCADE,
        instrument_id              TEXT REFERENCES instruments(id) ON DELETE SET NULL,
        instrument_name_snapshot   TEXT NOT NULL,
        ratio_percent              REAL NOT NULL,
        created_at                 TEXT NOT NULL,
        updated_at                 TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_schedules (
        id                      TEXT PRIMARY KEY,
        -- plan_id는 nullable -- RULE_REVIEW/MATURITY/GENERAL 같은 일정은 투자
        -- 계획(plan) 없이도 단독으로 등록될 수 있다(스펙: 계좌 유형 불문 전체
        -- 일정 관리).
        plan_id                 TEXT REFERENCES investment_plans(id) ON DELETE CASCADE,
        schedule_type           TEXT NOT NULL,
        title                   TEXT NOT NULL,
        market                  TEXT NOT NULL DEFAULT 'NONE',
        recurrence_type         TEXT NOT NULL,
        recurrence_interval     INTEGER NOT NULL DEFAULT 1,
        start_date              TEXT NOT NULL,
        end_date                TEXT,
        occurrence_count        INTEGER,
        holiday_policy          TEXT NOT NULL DEFAULT 'NEXT_BUSINESS_DAY',
        -- 회차일 며칠 전에 텔레그램으로 미리 알릴지(0=당일만). 스펙: "예약알림".
        notify_days_before      INTEGER NOT NULL DEFAULT 0,
        total_amount            REAL,
        currency                TEXT NOT NULL DEFAULT 'KRW',
        deposit_account_id      TEXT REFERENCES deposits(id) ON DELETE SET NULL,
        account_name_snapshot   TEXT,
        status                  TEXT NOT NULL DEFAULT 'ACTIVE',
        memo                    TEXT,
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_schedule_items (
        id                         TEXT PRIMARY KEY,
        schedule_id                TEXT NOT NULL REFERENCES investment_schedules(id) ON DELETE CASCADE,
        instrument_id               TEXT REFERENCES instruments(id) ON DELETE SET NULL,
        instrument_name_snapshot    TEXT NOT NULL,
        ratio_percent                REAL NOT NULL,
        created_at                   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedule_occurrences (
        id                            TEXT PRIMARY KEY,
        schedule_id                   TEXT NOT NULL REFERENCES investment_schedules(id) ON DELETE CASCADE,
        occurrence_index               INTEGER NOT NULL,
        -- 휴장일 보정 전/후 날짜를 모두 남긴다(스펙: original/adjusted 분리).
        original_scheduled_date        TEXT NOT NULL,
        adjusted_scheduled_date        TEXT NOT NULL,
        -- KRX 외 시장은 검증된 휴장일 데이터가 없어 주말 보정만 하고 이 플래그를
        -- 세운다 -- 절대 날짜를 추측해서 확정하지 않는다(스펙 7).
        needs_holiday_confirmation     INTEGER NOT NULL DEFAULT 0,
        planned_amount                  REAL,
        currency                        TEXT NOT NULL DEFAULT 'KRW',
        status                          TEXT NOT NULL DEFAULT 'SCHEDULED',
        acknowledged                    INTEGER NOT NULL DEFAULT 0,
        created_at                      TEXT NOT NULL,
        updated_at                      TEXT NOT NULL,
        UNIQUE(schedule_id, occurrence_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedule_occurrence_items (
        id                          TEXT PRIMARY KEY,
        occurrence_id               TEXT NOT NULL REFERENCES schedule_occurrences(id) ON DELETE CASCADE,
        instrument_id                TEXT REFERENCES instruments(id) ON DELETE SET NULL,
        instrument_name_snapshot     TEXT NOT NULL,
        planned_amount                REAL NOT NULL,
        created_at                     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedule_executions (
        -- 계획(planned) 대비 실행(actual)을 분리하는 명시적 실행 기록 --
        -- 이 테이블 자체는 절대로 trades/cash_withdrawals/deposits를 대신 만들지
        -- 않는다(스펙: 자동 매매/이체 금지). 사용자가 실제로 발생시킨 거래/출금을
        -- 나중에 연결하거나(linked_*_id), 연결 없이 "완료 처리"만 기록한다.
        id                    TEXT PRIMARY KEY,
        occurrence_id          TEXT NOT NULL REFERENCES schedule_occurrences(id) ON DELETE CASCADE,
        execution_type          TEXT NOT NULL,
        linked_withdrawal_id      TEXT REFERENCES cash_withdrawals(id) ON DELETE SET NULL,
        linked_trade_id           TEXT REFERENCES trades(id) ON DELETE SET NULL,
        executed_amount            REAL,
        executed_at                 TEXT NOT NULL,
        memo                         TEXT,
        created_at                    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedule_notification_outbox (
        -- notification_outbox(신호 알림 전용, signal_event_id NOT NULL FK)와는
        -- 별개 테이블이다 -- 스키마가 signal_events 전용으로 이미 고정돼 있어
        -- 재사용하면 FK 제약을 깨야 한다. 대신 상태 머신(PENDING/SENDING/SENT/
        -- RETRY/FAILED)과 재시도 로직, telegram_client 발송 함수는 그대로
        -- 재사용한다(notification_service.py의 패턴을 스케줄용으로 복제).
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        occurrence_id        TEXT NOT NULL REFERENCES schedule_occurrences(id) ON DELETE CASCADE,
        notification_type     TEXT NOT NULL,
        scheduled_date          TEXT NOT NULL,
        channel                  TEXT NOT NULL DEFAULT 'telegram',
        status                    TEXT NOT NULL DEFAULT 'PENDING',
        payload                    TEXT NOT NULL,
        attempt_count               INTEGER NOT NULL DEFAULT 0,
        next_attempt_at              TEXT,
        created_at                    TEXT NOT NULL,
        updated_at                     TEXT NOT NULL,
        -- 스펙: schedule_id+notification_type+scheduled_date 조합으로 중복 발송
        -- 방지(occurrence_id가 schedule_id+회차를 이미 유일하게 식별하므로
        -- occurrence_id로 대체 -- 동일 의미, 조인 없이 바로 유니크 제약 가능).
        UNIQUE(occurrence_id, notification_type, scheduled_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_investment_plan_items_plan_id ON investment_plan_items(plan_id)",
    "CREATE INDEX IF NOT EXISTS idx_investment_schedules_plan_id ON investment_schedules(plan_id)",
    "CREATE INDEX IF NOT EXISTS idx_investment_schedules_status ON investment_schedules(status)",
    "CREATE INDEX IF NOT EXISTS idx_investment_schedule_items_schedule_id ON investment_schedule_items(schedule_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_occurrences_schedule_id ON schedule_occurrences(schedule_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_occurrences_adjusted_date ON schedule_occurrences(adjusted_scheduled_date)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_occurrences_status ON schedule_occurrences(status)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_occurrence_items_occurrence_id ON schedule_occurrence_items(occurrence_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_executions_occurrence_id ON schedule_executions(occurrence_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_notification_outbox_status ON schedule_notification_outbox(status, next_attempt_at)",

    # ---- v1.3 -- 일별 총자산/손익 스냅샷 -------------------------------------
    # 금액은 이 프로젝트 전체 관례대로 REAL(SQLite에 Decimal 타입이 없고 deposits/
    # trades 등 기존 모든 금액 컬럼이 REAL -- v1.1/v1.2에서도 이미 사용자에게 보고한
    # 편차). snapshot_date는 UNIQUE -- 같은 KST 날짜에 행이 2개 생기지 않는다
    # (worker.py가 이 제약 + upsert 정책으로 중복실행을 막는다).
    """
    CREATE TABLE IF NOT EXISTS portfolio_daily_snapshots (
        id                          TEXT PRIMARY KEY,
        snapshot_date               TEXT NOT NULL UNIQUE,
        snapshot_at                 TEXT NOT NULL,
        timezone                    TEXT NOT NULL DEFAULT 'Asia/Seoul',
        base_currency                TEXT NOT NULL DEFAULT 'KRW',
        -- 총자산/평가액/예금/원가/손익 -- 계산 불가능하면(FAILED) NULL로 남긴다.
        -- 가짜 0을 넣지 않는다(스펙 8절 "부분 실패 시 이전 값을 그대로 복사하지 않는다"
        -- 와 같은 취지 -- 모르면 모른다고 NULL로 남긴다).
        total_asset_value            REAL,
        investment_market_value       REAL,
        cash_deposit_value             REAL,
        total_cost_basis                 REAL,
        unrealized_profit                 REAL,
        realized_profit                    REAL,
        total_profit                        REAL,
        profit_rate                          REAL,
        -- 통화별 자산/손익은 "그 통화 원래 단위" 그대로(원화 환산 아님) --
        -- usd_krw_rate/jpy_krw_rate와 짝을 이뤄, 화면에서 필요하면 직접 환산한다.
        krw_asset_value                       REAL,
        usd_asset_value                        REAL,
        jpy_asset_value                         REAL,
        krw_profit                               REAL,
        usd_profit                                REAL,
        jpy_profit                                 REAL,
        usd_krw_rate                                REAL,
        jpy_krw_rate                                 REAL,
        included_instrument_count                     INTEGER NOT NULL DEFAULT 0,
        successful_quote_count                         INTEGER NOT NULL DEFAULT 0,
        failed_quote_count                              INTEGER NOT NULL DEFAULT 0,
        -- COMPLETE/PARTIAL/FAILED/STALE_PRICE/MANUAL (스펙 8절).
        data_quality_status                              TEXT NOT NULL,
        -- 사람이 읽는 설명(어떤 종목/환율이 빠졌는지, 실패 사유 등).
        quality_notes                                     TEXT,
        -- 계산 공식이 바뀐 시점을 구분하기 위한 버전 번호(스펙 17절).
        calculation_version                                INTEGER NOT NULL DEFAULT 1,
        created_at                                          TEXT NOT NULL,
        updated_at                                           TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_snapshot_items (
        id                        TEXT PRIMARY KEY,
        snapshot_id                TEXT NOT NULL REFERENCES portfolio_daily_snapshots(id) ON DELETE CASCADE,
        -- INSTRUMENT(투자종목) / DEPOSIT(예금·현금성 계좌) -- 이 프로젝트엔 계좌
        -- 종류 구분이 deposits 하나뿐이라 스펙의 CASH/DEPOSIT을 굳이 나누지 않는다.
        item_type                   TEXT NOT NULL,
        account_id                   TEXT,
        account_name_snapshot         TEXT,
        instrument_id                  TEXT,
        instrument_name_snapshot        TEXT,
        instrument_code_snapshot         TEXT,
        market_code                       TEXT,
        currency                           TEXT NOT NULL,
        quantity                            REAL,
        unit_price                           REAL,
        price_date                            TEXT,
        original_value                         REAL NOT NULL,
        exchange_rate                           REAL,
        krw_value                                REAL,
        cost_basis                                REAL,
        unrealized_profit                          REAL,
        -- OK / MISSING_PRICE / MISSING_FX / STALE.
        data_status                                 TEXT NOT NULL,
        error_message                                TEXT,
        created_at                                    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_portfolio_daily_snapshots_status ON portfolio_daily_snapshots(data_quality_status)",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_snapshot_items_snapshot_id ON portfolio_snapshot_items(snapshot_id)",

    # ---- 매매계획(사용자 트리거 감시) Phase 1 -- TRAIL만 지원 ---------------
    # 설계 근거: PLAN_trade_trigger_v1.md, history.txt "[매매계획 감시 기능
    # Phase 1]" 항목. 이 기능은 매매 타당성을 프로그램이 판단하지 않는다 --
    # 사용자가 LLM과 상의해 이미 확정한 트리거/단계를 그대로 저장하고
    # 감시·알림만 한다(기존 ATR 손절/익절 신호와는 완전히 독립적인 별도
    # 신호 트랙).
    """
    CREATE TABLE IF NOT EXISTS trade_plans (
        id                         TEXT PRIMARY KEY,
        plan_type                  TEXT NOT NULL,   -- Phase 1: 'TRAIL'만 허용
        label                       TEXT NOT NULL,
        lifecycle_status             TEXT NOT NULL DEFAULT 'ARMED',
                                     -- ARMED/ACTIVE/PARTIALLY_FIRED/COMPLETED/CANCELLED
        trigger_price                 REAL,
        trigger_direction              TEXT,   -- ABOVE / BELOW
        trigger_activated_at            TEXT,
        peak_price_since_trigger         REAL, -- instruments.post_entry_high_price와
                                                 -- 별개 필드(의미가 다름 -- 4.2 참고)
        confirm_mode                      TEXT NOT NULL DEFAULT 'CLOSE', -- CLOSE/INTRADAY
        price_reference_instrument_id      TEXT REFERENCES instruments(id),
                                             -- 여러 계좌가 연결된 계획에서 가격을
                                             -- 한 번만 조회할 대표 종목(명시 지정,
                                             -- 우연히 아무 행이나 고르지 않음)
        approach_notified_at                 TEXT, -- TRIGGER_APPROACH 중복방지(계획당 1회)
        purpose                               TEXT,
        invalidation_condition                 TEXT,
        review_date                             TEXT,
        reason                                   TEXT,
        version                                  INTEGER NOT NULL DEFAULT 1,
        created_at                                TEXT NOT NULL,
        updated_at                                 TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_plan_instruments (
        -- 계획 하나에 종목(계좌 등록분) 여러 개 연결 -- 예: KODEX 200이 두
        -- 계좌에 나뉘어 등록된 경우 같은 계획에 둘 다 연결하고 baseline은
        -- 계좌별로 따로 저장한다(합산해서 하나로 만들지 않음, 계좌별 실행
        -- 메모가 각각 필요하기 때문).
        plan_id             TEXT NOT NULL REFERENCES trade_plans(id) ON DELETE CASCADE,
        instrument_id         TEXT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
        baseline_quantity       REAL NOT NULL, -- 계획 확정 시점 스냅샷(고정),
                                                 -- 이후 적립매수로 실보유수량이
                                                 -- 늘어도 이 값은 안 바뀐다
        display_note              TEXT,
        PRIMARY KEY (plan_id, instrument_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_plan_tiers (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id          TEXT NOT NULL REFERENCES trade_plans(id) ON DELETE CASCADE,
        tier_order         INTEGER NOT NULL,
        pullback_pct          REAL NOT NULL,  -- 최고가 대비 하락률(양수)
        sell_pct                REAL NOT NULL, -- 이 단계에서 추가로 매도할 비율
                                                 -- (baseline 대비, 누적 아님)
        fired_at                  TEXT,
        fired_peak_price             REAL,   -- 발동 당시 최고가(사후 검증용 스냅샷)
        fired_reference_price          REAL, -- 발동을 확정한 실제 가격(종가/장중)
        UNIQUE (plan_id, tier_order)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_plan_history (
        -- instruments.py의 buy_multiple/sell_multiple 버저닝 패턴을 그대로
        -- 재사용 -- 수정 전 상태 전체(연결 종목/baseline/tiers 포함)를 JSON
        -- 스냅샷으로 남겨서 복원 가능하게 한다.
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id             TEXT NOT NULL REFERENCES trade_plans(id) ON DELETE CASCADE,
        version               INTEGER NOT NULL,
        snapshot_json           TEXT NOT NULL,
        change_reason              TEXT,
        changed_at                   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_plan_notification_outbox (
        -- notification_outbox(signal_event_id NOT NULL FK, 신호 전용)와
        -- schedule_notification_outbox 둘 다 재사용 불가(다른 FK 대상) --
        -- schedule_notification_outbox와 동일한 상태머신/재시도 패턴만
        -- 복제한 별도 테이블. idempotency_key로 같은 이벤트의 중복 발송을
        -- 막는다(예: "{plan_id}:TIER_FIRED:{tier_order}:{trade_date}").
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id               TEXT NOT NULL REFERENCES trade_plans(id) ON DELETE CASCADE,
        event_type              TEXT NOT NULL, -- TRIGGER_APPROACH/TRIGGER_REACHED/
                                                 -- TIER_PREVIEW/TIER_FIRED/PLAN_REVIEW/DATA_STALE
        idempotency_key            TEXT NOT NULL,
        channel                      TEXT NOT NULL DEFAULT 'telegram',
        status                        TEXT NOT NULL DEFAULT 'PENDING',
        payload                        TEXT NOT NULL,
        attempt_count                    INTEGER NOT NULL DEFAULT 0,
        next_attempt_at                    TEXT,
        created_at                          TEXT NOT NULL,
        updated_at                            TEXT NOT NULL,
        UNIQUE(plan_id, idempotency_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trade_plans_lifecycle_status ON trade_plans(lifecycle_status)",
    "CREATE INDEX IF NOT EXISTS idx_trade_plan_instruments_instrument_id ON trade_plan_instruments(instrument_id)",
    "CREATE INDEX IF NOT EXISTS idx_trade_plan_tiers_plan_id ON trade_plan_tiers(plan_id)",
    "CREATE INDEX IF NOT EXISTS idx_trade_plan_history_plan_id ON trade_plan_history(plan_id)",
    "CREATE INDEX IF NOT EXISTS idx_trade_plan_notification_outbox_status ON trade_plan_notification_outbox(status, next_attempt_at)",
]
