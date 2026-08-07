"""scripts/restore_check.py 테스트 -- 2026-08-06 CORE_TABLES 확장 회귀.

기존엔 테스트가 전혀 없던 운영 스크립트다. 실제 백업 파일까지 만들어서 전체
흐름(gzip 복원 -> integrity_check -> 행 수 확인)을 도는 통합 테스트는 무겁고
scripts/backup.py에 이미 의존하므로, 여기서는 "CORE_TABLES에 적어둔 테이블
이름이 전부 실제 스키마에 존재하는가"만 가볍게 검증한다 -- 20개 넘게 손으로
나열하다 보면 오타가 나기 쉬운데, 오타가 나면 실제 복구 상황에서 조용히
`sqlite3.OperationalError: no such table`로 실패하게 된다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from restore_check import CORE_TABLES


def _real_table_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows}


def test_all_core_tables_exist_in_fresh_schema(db_conn):
    real_tables = _real_table_names(db_conn)
    missing = [t for t in CORE_TABLES if t not in real_tables]
    assert missing == [], f"CORE_TABLES에 오타/누락된 테이블: {missing}"


def test_core_tables_covers_every_version_feature(db_conn):
    """v1.1~v1.3에서 추가된 대표 테이블이 실제로 목록에 들어있는지 확인 --
    새 버전이 나올 때마다 이 리스트에 하나 추가하는 걸 잊지 않게 하는
    가드레일."""
    expected_present = {
        "cash_withdrawals",  # v1.1
        "investment_schedules", "schedule_occurrences",  # v1.2
        "portfolio_daily_snapshots", "portfolio_snapshot_items",  # v1.3
        "analysis_results", "companies", "company_analysis_results",  # v1.0 후반
        "trade_plans", "trade_plan_instruments", "trade_plan_tiers",  # 매매계획 Phase 1
    }
    missing = expected_present - set(CORE_TABLES)
    assert missing == set(), f"CORE_TABLES에서 빠진 핵심 테이블: {missing}"


def test_core_tables_have_no_duplicates():
    assert len(CORE_TABLES) == len(set(CORE_TABLES))
