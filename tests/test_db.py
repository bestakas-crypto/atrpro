"""db.py의 스키마 마이그레이션 테스트 -- CREATE TABLE IF NOT EXISTS는 이미
데이터가 든 기존 DB(예: VPS에 실제 배포된 DB)에 새 컬럼을 추가해주지 않는다.
2026-08-02 quote_latest.change_pct 컬럼을 추가하면서 이 갭을 처음 발견했고,
_add_column_if_missing()으로 고쳤다 -- 회귀 방지용 테스트."""
from atrsite import db as db_module


def test_init_db_adds_missing_column_to_existing_table_without_losing_data(tmp_path):
    """change_pct 컬럼이 없는 "구버전" DB를 흉내내고, init_db()가 기존
    행의 데이터를 잃지 않으면서 컬럼을 보충하는지 확인한다."""
    conn = db_module.connect(tmp_path / "old.db")
    # change_pct 없이 예전 스키마 그대로 quote_latest를 만든다 (실제 VPS DB 상황 재현).
    conn.execute("""
        CREATE TABLE quote_latest (
            instrument_id        TEXT PRIMARY KEY,
            price                REAL NOT NULL,
            quoted_at            TEXT NOT NULL,
            source               TEXT NOT NULL DEFAULT 'manual',
            data_status          TEXT NOT NULL DEFAULT 'MANUAL_OVERRIDE',
            consecutive_failures INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT INTO quote_latest (instrument_id, price, quoted_at) VALUES (?, ?, ?)",
        ("inst-1", 12345.0, "2026-08-01T00:00:00+00:00"),
    )
    conn.commit()

    db_module.init_db(conn)  # 마이그레이션 실행 -- 실패하면 안 됨

    row = conn.execute("SELECT * FROM quote_latest WHERE instrument_id = ?", ("inst-1",)).fetchone()
    assert row["price"] == 12345.0  # 기존 데이터 그대로 남아있어야 함
    assert row["change_pct"] is None  # 새 컬럼은 기존 행에서 NULL로 채워짐

    # 두 번째 init_db() 호출도 안전해야 한다(ALTER TABLE을 중복 실행하면 에러).
    db_module.init_db(conn)
    conn.close()
