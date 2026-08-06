"""백업 복원 점검 -- 스펙 15.3. 매월 한 번 (자동/수동) 실행 권장.

    [TODO] 최신 백업 다운로드   <- 오라클 Object Storage가 아직 없어 로컬 최신
                                  백업 파일을 그대로 사용한다
    -> 임시 DB로 복원
    -> 무결성 검사 (PRAGMA integrity_check)
    -> 핵심 테이블 행 수 확인
    -> 애플리케이션 읽기 테스트 (리포지토리 함수로 실제 조회)
    -> 임시 복원본 삭제

사용법:
    python scripts/restore_check.py
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from atrsite.config import settings  # noqa: E402
from atrsite.repositories import instruments as instruments_repo  # noqa: E402

# 2026-08-06 -- v1.0 이후 추가된 기능(종목탐구/출금기록/투자일정/자산스냅샷)의
# 핵심 테이블이 여태 빠져 있던 걸 GPT 코드리뷰로 발견해서 전부 채워 넣었다.
# daily_bars/atr_values/quote_latest/strategy_settings/audit_log/sync_runs/
# app_settings/schema_meta는 의도적으로 계속 제외한다 -- 사용자가 직접 입력한
# "원본 데이터"가 아니라 KIS에서 재조회 가능한 캐시/파생 데이터이거나 순수
# 운영 로그라서, 복구 여부보다 무결성(PRAGMA integrity_check)만 통과하면 충분.
CORE_TABLES = [
    # v1.0 -- ATR 매매신호 핵심
    "instruments", "trades", "position_state", "deposits",
    "signal_state", "signal_events", "notification_outbox", "fx_rates",
    # v1.0 -- LLM 매크로 브리핑 + 종목탐구
    "analysis_results",
    "companies", "company_identifiers", "company_filings",
    "company_financial_periods", "company_financial_metrics",
    "company_analysis_requests", "company_analysis_results",
    # v1.1 -- 현금 출금기록
    "cash_withdrawals",
    # v1.2 -- 통합 투자 스케줄 및 예약알림
    "investment_plans", "investment_plan_items",
    "investment_schedules", "investment_schedule_items",
    "schedule_occurrences", "schedule_occurrence_items",
    "schedule_executions", "schedule_notification_outbox",
    # v1.3 -- 일별 총자산/손익 스냅샷
    "portfolio_daily_snapshots", "portfolio_snapshot_items",
]


class RestoreCheckError(RuntimeError):
    pass


def _latest_local_backup() -> Path:
    backups_dir = settings.backups_dir
    candidates = sorted(backups_dir.glob("atrsite-*.db.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RestoreCheckError(f"{backups_dir}에 백업 파일이 없습니다. 먼저 scripts/backup.py를 실행하세요.")
    return candidates[0]


def run_restore_check() -> dict:
    # TODO: 오라클 계정이 생기면 이 부분을 "최신 백업 다운로드"로 교체할 것.
    backup_gz = _latest_local_backup()

    with tempfile.TemporaryDirectory(prefix="atrsite-restore-check-") as tmp_dir:
        restored_db = Path(tmp_dir) / "restored.db"
        with gzip.open(backup_gz, "rb") as f_in, open(restored_db, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        conn = sqlite3.connect(str(restored_db))
        conn.row_factory = sqlite3.Row
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RestoreCheckError(f"integrity_check 실패: {integrity}")

            row_counts = {}
            for table in CORE_TABLES:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                row_counts[table] = count

            # 애플리케이션 읽기 테스트 -- 실제 리포지토리 함수로 조회가 되는지 확인.
            instruments_repo.list_instruments(conn, active_only=False)
        finally:
            conn.close()

    return {
        "backup_file": backup_gz.name,
        "integrity_check": "ok",
        "row_counts": row_counts,
    }
    # with 블록을 벗어나며 TemporaryDirectory가 임시 복원본을 자동 삭제한다.


if __name__ == "__main__":
    import json

    try:
        result = run_restore_check()
    except RestoreCheckError as exc:
        print(f"복원 점검 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))
