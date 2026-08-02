"""SQLite 백업 -- 스펙 15.2 순서 중 로컬에서 검증 가능한 단계까지 구현한다.

    SQLite Online Backup 생성
    -> PRAGMA integrity_check
    -> 외래키 검사
    -> gzip 압축
    -> [TODO] Object Storage 업로드           <- 오라클 계정이 아직 없어 비워둠
    -> [TODO] 업로드 크기.해시 확인 (로컬 해시는 미리 계산해서 매니페스트에 남김)
    -> 로컬 최신 2개 외 삭제 (스펙 15.1)
    -> [TODO] 원격 14개 초과분 삭제

신규 백업이 하나라도 실패하면(Online Backup, 무결성 검사, 외래키 검사 중 하나라도)
기존 로컬 백업은 건드리지 않는다 (스펙 15.2 "신규 백업이 실패하면 기존 백업을
삭제하지 않는다").

사용법:
    python scripts/backup.py
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from atrsite.config import settings  # noqa: E402


class BackupError(RuntimeError):
    pass


def _run_integrity_checks(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupError(f"integrity_check 실패: {result}")

        conn.execute("PRAGMA foreign_keys = ON")
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            raise BackupError(f"foreign_key_check 위반 {len(fk_violations)}건: {fk_violations[:5]}")
    finally:
        conn.close()


def _online_backup(source_path: Path, dest_path: Path) -> None:
    """SQLite Online Backup API -- 실행 중인 DB에서도 일관된 복사본을 만든다."""
    source = sqlite3.connect(str(source_path))
    dest = sqlite3.connect(str(dest_path))
    try:
        source.backup(dest)
    finally:
        source.close()
        dest.close()


def _gzip_and_hash(src_path: Path, dest_gz_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(src_path, "rb") as f_in, gzip.open(dest_gz_path, "wb") as f_out:
        while chunk := f_in.read(1024 * 1024):
            hasher.update(chunk)
            f_out.write(chunk)
    return hasher.hexdigest()


def _rotate_local_backups(backups_dir: Path, keep: int = 2) -> list[Path]:
    """스펙 15.1 -- 서버 내부(로컬)는 최신 2개만 보관."""
    gz_files = sorted(backups_dir.glob("atrsite-*.db.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for old in gz_files[keep:]:
        manifest = old.with_suffix(old.suffix + ".json")
        old.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        removed.append(old)
    return removed


def run_backup() -> Path:
    db_path = settings.db_path
    if not db_path.exists():
        raise BackupError(f"DB 파일이 없습니다: {db_path}")

    backups_dir = settings.backups_dir
    backups_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp_copy = backups_dir / f".tmp-{timestamp}.db"
    gz_path = backups_dir / f"atrsite-{timestamp}.db.gz"
    manifest_path = gz_path.with_suffix(gz_path.suffix + ".json")

    try:
        _online_backup(db_path, tmp_copy)
        _run_integrity_checks(tmp_copy)
        sha256 = _gzip_and_hash(tmp_copy, gz_path)
    except Exception:
        # 실패 시 이번에 만들던 임시/부분 산출물만 지우고, 기존 백업은 그대로 둔다.
        gz_path.unlink(missing_ok=True)
        raise
    finally:
        tmp_copy.unlink(missing_ok=True)

    manifest = {
        "created_at": timestamp,
        "source_db": str(db_path),
        "sha256": sha256,
        "size_bytes": gz_path.stat().st_size,
        # TODO: 오라클 Object Storage 업로드 후 실제 원격 경로/업로드 시각을 채울 것.
        "uploaded_to_object_storage": False,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    removed = _rotate_local_backups(backups_dir, keep=2)

    print(f"백업 완료: {gz_path.name} (sha256={sha256[:12]}..., {manifest['size_bytes']}bytes)")
    if removed:
        print(f"로컬 보관 한도(2개) 초과로 삭제: {[p.name for p in removed]}")
    print("TODO: Object Storage 업로드는 아직 구현되지 않았습니다 (오라클 계정 준비 후 추가).")
    return gz_path


if __name__ == "__main__":
    try:
        run_backup()
    except BackupError as exc:
        print(f"백업 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
