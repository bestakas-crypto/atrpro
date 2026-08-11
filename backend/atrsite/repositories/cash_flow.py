"""backend/atrsite/repositories/cash_flow.py -- 순외부현금흐름(net external cash
flow) 집계, v1.4(analyze.kunoh.top 1단계, 2026-08-12 추가) / v1.5에서
cash_ledger 단일 테이블 기준으로 재작성(2026-08-12).

cash_ledger에서 entry_type IN ('EXTERNAL_IN','EXTERNAL_OUT')만 걸러서
"이 기간에 총자산 밖에서 안으로 실제로 들어오고 나간 돈"을 계산한다.
INTERNAL_IN/INTERNAL_OUT(계좌 간 이동, 예: 예금->증권 매수 자금 이체)는
총자산 스냅샷(portfolio_daily_snapshots) 안에서 이미 위치만 바뀐 것으로
반영되므로 여기서는 의도적으로 제외한다 -- 두 번 반영하면 순입금이
부풀려진다.

TWR/Modified Dietz 계산의 입력값으로 쓰기 위한 것이라, daily_net_flow는
"기초자산 + 순입금 + 투자손익 = 기말자산" 검증(analyze.kunoh.top 1단계 완료
기준)에 필요한 일별 granularity로 제공한다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any


def net_summary(conn: sqlite3.Connection, *, start_date: str | None = None, end_date: str | None = None) -> dict[str, dict[str, float]]:
    """통화별 {inflow, outflow, net} -- entry_type이 EXTERNAL_*인 것만 집계.
    start_date/end_date는 'YYYY-MM-DD'(포함), 생략하면 전체 기간."""
    where = ["entry_type IN ('EXTERNAL_IN', 'EXTERNAL_OUT')"]
    params: list[Any] = []
    if start_date:
        where.append("occurred_at >= ?")
        params.append(f"{start_date}T00:00:00")
    if end_date:
        end_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        where.append("occurred_at < ?")
        params.append(f"{end_exclusive}T00:00:00")
    where_sql = " AND ".join(where)

    rows = conn.execute(
        f"SELECT currency, entry_type, SUM(amount) AS total FROM cash_ledger "
        f"WHERE {where_sql} GROUP BY currency, entry_type",
        params,
    ).fetchall()

    result: dict[str, dict[str, float]] = {}
    for r in rows:
        bucket = result.setdefault(r["currency"], {"inflow": 0.0, "outflow": 0.0, "net": 0.0})
        if r["entry_type"] == "EXTERNAL_IN":
            bucket["inflow"] = r["total"]
        else:
            bucket["outflow"] = r["total"]
    for bucket in result.values():
        bucket["net"] = bucket["inflow"] - bucket["outflow"]
    return result


def daily_net_flow(conn: sqlite3.Connection, *, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """일별/통화별 순외부현금흐름 -- portfolio_daily_snapshots와 날짜(snapshot_date)
    기준으로 조인해 TWR/Modified Dietz를 계산할 때 쓴다. entry_type이
    EXTERNAL_*인 것만, 출금(EXTERNAL_OUT)은 음수로 뒤집어서 합산 가능하게 한다."""
    end_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    start_bound = f"{start_date}T00:00:00"
    end_bound = f"{end_exclusive}T00:00:00"

    rows = conn.execute(
        """
        SELECT substr(occurred_at, 1, 10) AS d, currency,
               SUM(CASE WHEN entry_type = 'EXTERNAL_IN' THEN amount ELSE -amount END) AS total
        FROM cash_ledger
        WHERE entry_type IN ('EXTERNAL_IN', 'EXTERNAL_OUT') AND occurred_at >= ? AND occurred_at < ?
        GROUP BY d, currency
        ORDER BY d, currency
        """,
        (start_bound, end_bound),
    ).fetchall()

    return [{"date": r["d"], "currency": r["currency"], "net_amount": r["total"]} for r in rows]
