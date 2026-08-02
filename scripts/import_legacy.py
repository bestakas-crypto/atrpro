"""CLI: 기존 ATRsite에서 내보낸 JSON 파일을 strpro DB로 가져온다 (스펙 7, 16절).

사용법:
    python scripts/import_legacy.py path/to/export.json           # 검증만
    python scripts/import_legacy.py path/to/export.json --apply   # 실제 저장
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from atrsite import db  # noqa: E402
from atrsite.services import legacy_import  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--apply", action="store_true", help="검증만 하지 않고 실제로 저장한다")
    args = parser.parse_args()

    payload = json.loads(args.json_path.read_text(encoding="utf-8"))

    if not args.apply:
        report = legacy_import.validate(payload)
    else:
        conn = db.connect()
        db.init_db(conn)
        try:
            report = legacy_import.apply(conn, payload)
            if report.valid:
                conn.commit()
            else:
                conn.rollback()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    result = report.to_dict()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"]:
        print("\n검증 실패 -- 저장하지 않았습니다." if args.apply else "\n검증 실패.", file=sys.stderr)
        return 1
    if args.apply:
        print(f"\n가져오기 완료: 종목 {result['summary']['instruments']}개, "
              f"거래 {result['summary']['trades']}건, 예금 {result['summary']['deposits']}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
