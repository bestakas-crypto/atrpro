"""pytest 공통 설정 — backend/ 를 sys.path에 추가해 `atrsite.*` 패키지를 임포트 가능하게 한다."""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from atrsite import db as db_module  # noqa: E402


@pytest.fixture()
def db_conn(tmp_path):
    """스키마가 초기화된 임시 SQLite 연결. 테스트마다 새 파일을 사용한다."""
    conn = db_module.connect(tmp_path / "test.db")
    db_module.init_db(conn)
    yield conn
    conn.close()
