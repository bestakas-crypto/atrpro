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


def init_db(conn: sqlite3.Connection) -> None:
    for statement in DDL_STATEMENTS:
        conn.execute(statement)
    # 2026-08-02 추가: 이미 만들어져 있던 quote_latest 테이블에 change_pct
    # 컬럼을 보충한다(신규 DB는 위 CREATE TABLE에 이미 포함돼 있어 no-op).
    _add_column_if_missing(conn, "quote_latest", "change_pct", "change_pct REAL")
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
