"""SQLite connection management -- spec 10.3.

journal_mode=WAL, foreign_keys=ON, busy_timeout=5000, synchronous=NORMAL on
every connection. Web and worker processes each open their own connection(s)
against the same DB file; WAL allows concurrent readers with a single writer.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings
from .schema import DDL_STATEMENTS, SCHEMA_VERSION
from .utils import utcnow_iso


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI 동기 의존성 제너레이터는 시작/재개가
    # 서로 다른 워커 스레드에서 일어날 수 있다(anyio 스레드풀). 커넥션은 항상
    # 요청 1건 범위 안에서만 열고 닫으므로(get_conn 참고) 스레드 간 동시
    # 접근은 애초에 없다 -- 이 플래그는 그 제약을 완화할 뿐이다.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """CREATE TABLE IF NOT EXISTS는 이미 존재하는 테이블에 새 컬럼을 추가해주지
    않는다 -- 이미 실제 데이터가 든 DB(예: VPS)에서 스키마에 컬럼을 새로
    추가했을 때, 여기서 없으면 ALTER TABLE로 보충한다. 매번 PRAGMA로 확인 후
    없을 때만 실행하므로 반복 호출해도 안전하다."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate_legacy_cash_records(conn: sqlite3.Connection) -> None:
    """v1.5(2026-08-12) -- cash_withdrawals(v1.1)/cash_inflows(v1.4)를
    cash_ledger로 1회성 이관한다. schema_meta 플래그로 딱 한 번만 실행 --
    안 그러면 매 startup마다 재실행되면서, 사용자가 마이그레이션 후 ledger
    쪽에서 지운 행이 부활하거나 중복 삽입된다. 두 옛 테이블은 삭제하지
    않는다(원본 보존 -- 문제가 생기면 언제든 재대조 가능).

    purpose/source(옛 필드)는 새 스키마에 없으므로 memo 앞에 그대로 접어
    넣어서 데이터가 유실되지 않게 한다(schema.py의 cash_ledger 주석 참고)."""
    already = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'cash_ledger_migrated_v1'"
    ).fetchone()
    if already is not None:
        return

    def fold_memo(label: str, memo: str | None) -> str | None:
        label = (label or "").strip()
        memo = (memo or "").strip()
        if not label:
            return memo or None
        return f"{label} — {memo}" if memo else label

    for row in conn.execute("SELECT * FROM cash_withdrawals"):
        entry_type = "INTERNAL_OUT" if row["flow_type"] == "INTERNAL_TRANSFER" else "EXTERNAL_OUT"
        conn.execute(
            """
            INSERT INTO cash_ledger
                (id, occurred_at, deposit_account_id, account_name_snapshot, entry_type,
                 amount, currency, memo, edited, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"legacy-wd-{row['id']}",  # 원본 id와 충돌 없게 접두사(withdrawals/inflows가 독립 PK 공간이라
                row["withdrawn_at"], row["deposit_account_id"], row["account_name_snapshot"], entry_type,
                row["amount"], row["currency"], fold_memo(row["purpose"], row["memo"]),
                row["edited"], row["created_at"], row["updated_at"],
            ),
        )

    for row in conn.execute("SELECT * FROM cash_inflows"):
        entry_type = "INTERNAL_IN" if row["flow_type"] == "INTERNAL_TRANSFER" else "EXTERNAL_IN"
        conn.execute(
            """
            INSERT INTO cash_ledger
                (id, occurred_at, deposit_account_id, account_name_snapshot, entry_type,
                 amount, currency, memo, edited, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"legacy-in-{row['id']}",
                row["deposited_at"], row["deposit_account_id"], row["account_name_snapshot"], entry_type,
                row["amount"], row["currency"], fold_memo(row["source"], row["memo"]),
                row["edited"], row["created_at"], row["updated_at"],
            ),
        )

    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('cash_ledger_migrated_v1', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (utcnow_iso(),),
    )


def _migrate_schedule_executions_fk(conn: sqlite3.Connection) -> None:
    """v1.5(2026-08-12) -- schedule_executions.linked_withdrawal_id의 FK 대상을
    cash_withdrawals -> cash_ledger로 옮긴다. SQLite는 ALTER TABLE로 기존 FK
    제약을 못 바꾸므로, 옛 FK를 쓰는 테이블이면 드롭해서 DDL_STATEMENTS의
    CREATE TABLE IF NOT EXISTS가 새 FK로 다시 만들게 한다(반드시 그 루프보다
    먼저 호출해야 함). 이 링크 기능은 지금까지 프런트엔드에 실제 입력 UI가
    없어서(investment-schedule.js가 linked_withdrawal_id를 채운 적 없음)
    운영 DB에 행이 있었던 적이 없다(2026-08-12 확인, 0건) -- 혹시라도 행이
    있으면 데이터 손실을 피하기 위해 안전하게 건너뛴다(수동 처리 필요)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_executions'"
    ).fetchone()
    if not exists:
        return  # 신규 DB -- 이후 DDL_STATEMENTS가 새 FK로 바로 만듦
    fk_rows = conn.execute("PRAGMA foreign_key_list(schedule_executions)").fetchall()
    points_to_old_table = any(
        r["table"] == "cash_withdrawals" and r["from"] == "linked_withdrawal_id" for r in fk_rows
    )
    if not points_to_old_table:
        return  # 이미 마이그레이션됨
    count = conn.execute("SELECT COUNT(*) AS n FROM schedule_executions").fetchone()["n"]
    if count > 0:
        return  # 안전하게 건너뜀(현재까지 실제로는 항상 0건) -- 수동 처리 필요
    conn.execute("DROP TABLE schedule_executions")


def _rename_cash_ledger_for_entry_type_migration(conn: sqlite3.Connection) -> None:
    """v1.7(2026-08-12, 이자소득) -- cash_ledger.entry_type CHECK 제약에
    INTEREST_INCOME을 추가하기 위한 1단계. SQLite는 ALTER TABLE로 CHECK
    제약을 못 바꾸므로, 옛 제약을 쓰는 테이블이면 임시 이름으로 옮겨서
    DDL_STATEMENTS의 CREATE TABLE IF NOT EXISTS가 새 제약으로 다시 만들게
    한다(반드시 그 루프보다 먼저 호출). schedule_executions FK 마이그레이션
    (v1.5)과 다르게 cash_ledger는 이미 실데이터가 있어서 "비어있을 때만"이
    아니라 무조건 옮기고, 데이터는 _finish_cash_ledger_entry_type_migration
    에서 그대로 복사해온다(DROP 전용이 아님)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cash_ledger'"
    ).fetchone()
    if not exists:
        return  # 신규 DB -- 이후 DDL_STATEMENTS가 새 제약으로 바로 만듦
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cash_ledger'"
    ).fetchone()
    if row and row["sql"] and "INTEREST_INCOME" in row["sql"]:
        return  # 이미 마이그레이션됨
    conn.execute("ALTER TABLE cash_ledger RENAME TO cash_ledger_v1_6_migrating")


def _finish_cash_ledger_entry_type_migration(conn: sqlite3.Connection) -> None:
    """위 함수가 옮겨둔 임시 테이블(있다면)에서 새로 만들어진 cash_ledger로
    행을 전부 그대로 복사하고 임시 테이블을 지운다. DDL_STATEMENTS 루프
    다음에 호출해야 한다(그래야 새 cash_ledger가 이미 존재)."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cash_ledger_v1_6_migrating'"
    ).fetchone()
    if not exists:
        return
    conn.execute(
        """
        INSERT INTO cash_ledger
            (id, occurred_at, deposit_account_id, account_name_snapshot, entry_type,
             amount, currency, memo, edited, created_at, updated_at)
        SELECT id, occurred_at, deposit_account_id, account_name_snapshot, entry_type,
               amount, currency, memo, edited, created_at, updated_at
        FROM cash_ledger_v1_6_migrating
        """
    )
    conn.execute("DROP TABLE cash_ledger_v1_6_migrating")


def init_db(conn: sqlite3.Connection) -> None:
    _migrate_schedule_executions_fk(conn)  # DDL_STATEMENTS 루프보다 먼저(드롭 후 재생성).
    _rename_cash_ledger_for_entry_type_migration(conn)  # 마찬가지로 루프보다 먼저.
    for statement in DDL_STATEMENTS:
        conn.execute(statement)
    _finish_cash_ledger_entry_type_migration(conn)  # 루프 다음(새 cash_ledger로 데이터 복원).
    # 2026-08-02 추가: 이미 만들어져 있던 quote_latest 테이블에 change_pct
    # 컬럼을 보충한다(신규 DB는 위 CREATE TABLE에 이미 포함돼 있어 no-op).
    _add_column_if_missing(conn, "quote_latest", "change_pct", "change_pct REAL")
    # 2026-08-04 추가: NXT 장 반영을 위해 종목별 ETF 여부가 필요해짐(ETF는
    # NXT에 상장되지 않아 항상 KRX 기준으로 조회해야 함).
    _add_column_if_missing(conn, "instruments", "is_etf", "is_etf INTEGER NOT NULL DEFAULT 0")
    # 2026-08-12 추가(v1.4 현금 입금기록/analyze.kunoh.top 1단계): 이미 운영 중인
    # DB의 cash_withdrawals에 flow_type을 보충한다. 기본값 EXTERNAL은 신규 CREATE
    # TABLE과 동일 -- schema.py의 flow_type 주석 참고.
    _add_column_if_missing(
        conn, "cash_withdrawals", "flow_type",
        "flow_type TEXT NOT NULL DEFAULT 'EXTERNAL'",
    )
    _migrate_legacy_cash_records(conn)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


@contextmanager
def session(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """단일 트랜잭션 컨텍스트 매니저 -- 정상 종료 시 commit, 예외 시 rollback."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
