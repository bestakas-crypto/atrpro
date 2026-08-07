"""매매계획(트리거 감시) 리포지토리 -- Phase 1, TRAIL만.

instruments.py의 strategy_settings 버저닝 패턴을 그대로 따른다: 계획을
수정할 때마다 새 version을 만들고, 수정 전 전체 상태(연결 종목/baseline/
tiers 포함)를 trade_plan_history에 JSON 스냅샷으로 남긴다. 삭제는 실제
DELETE가 아니라 CANCELLED 전환이다.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from ..utils import new_id, utcnow_iso


def _tiers_for_plan(conn: sqlite3.Connection, plan_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM trade_plan_tiers WHERE plan_id = ? ORDER BY tier_order ASC",
        (plan_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _instruments_for_plan(conn: sqlite3.Connection, plan_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT tpi.*, i.name AS instrument_name, i.currency AS currency,
               i.kis_code AS kis_code
        FROM trade_plan_instruments tpi
        JOIN instruments i ON i.id = tpi.instrument_id
        WHERE tpi.plan_id = ?
        ORDER BY i.name ASC
        """,
        (plan_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    plan_id = row["id"]
    return {
        "id": plan_id,
        "plan_type": row["plan_type"],
        "label": row["label"],
        "lifecycle_status": row["lifecycle_status"],
        "trigger_price": row["trigger_price"],
        "trigger_direction": row["trigger_direction"],
        "trigger_activated_at": row["trigger_activated_at"],
        "peak_price_since_trigger": row["peak_price_since_trigger"],
        "confirm_mode": row["confirm_mode"],
        "price_reference_instrument_id": row["price_reference_instrument_id"],
        "approach_notified_at": row["approach_notified_at"],
        "purpose": row["purpose"],
        "invalidation_condition": row["invalidation_condition"],
        "review_date": row["review_date"],
        "reason": row["reason"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "instruments": _instruments_for_plan(conn, plan_id),
        "tiers": _tiers_for_plan(conn, plan_id),
    }


def get_plan(conn: sqlite3.Connection, plan_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM trade_plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(conn, row)


def list_plans(
    conn: sqlite3.Connection, *, instrument_id: str | None = None, statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT DISTINCT tp.* FROM trade_plans tp"
    params: list[Any] = []
    if instrument_id is not None:
        query += " JOIN trade_plan_instruments tpi ON tpi.plan_id = tp.id AND tpi.instrument_id = ?"
        params.append(instrument_id)
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        query += f" WHERE tp.lifecycle_status IN ({placeholders})"
        params.extend(statuses)
    query += " ORDER BY tp.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(conn, row) for row in rows]


def list_plans_for_polling(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """ARMED/ACTIVE/PARTIALLY_FIRED 상태(=아직 평가가 필요한) 계획만."""
    return list_plans(conn, statuses=["ARMED", "ACTIVE", "PARTIALLY_FIRED"])


def create_plan(
    conn: sqlite3.Connection,
    *,
    plan_type: str,
    label: str,
    trigger_price: float,
    trigger_direction: str,
    confirm_mode: str,
    price_reference_instrument_id: str,
    instruments: list[dict[str, Any]],  # [{instrument_id, baseline_quantity, display_note}]
    tiers: list[dict[str, Any]],  # [{tier_order, pullback_pct, sell_pct}]
    purpose: str | None = None,
    invalidation_condition: str | None = None,
    review_date: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if plan_type != "TRAIL":
        raise ValueError(f"Phase 1은 TRAIL만 지원합니다: plan_type={plan_type}")
    if trigger_direction not in ("ABOVE", "BELOW"):
        raise ValueError(f"알 수 없는 trigger_direction: {trigger_direction}")
    if not instruments:
        raise ValueError("계획에 연결할 종목이 최소 1개 필요합니다.")
    ref_ids = {i["instrument_id"] for i in instruments}
    if price_reference_instrument_id not in ref_ids:
        raise ValueError("price_reference_instrument_id는 연결 종목 중 하나여야 합니다.")

    _validate_tiers(tiers)

    plan_id = new_id()
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO trade_plans
            (id, plan_type, label, lifecycle_status, trigger_price, trigger_direction,
             trigger_activated_at, peak_price_since_trigger, confirm_mode,
             price_reference_instrument_id, approach_notified_at, purpose,
             invalidation_condition, review_date, reason, version, created_at, updated_at)
        VALUES (?, ?, ?, 'ARMED', ?, ?, NULL, NULL, ?, ?, NULL, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            plan_id, plan_type, label, trigger_price, trigger_direction, confirm_mode,
            price_reference_instrument_id, purpose, invalidation_condition, review_date,
            reason, now, now,
        ),
    )
    for inst in instruments:
        conn.execute(
            "INSERT INTO trade_plan_instruments (plan_id, instrument_id, baseline_quantity, display_note) "
            "VALUES (?, ?, ?, ?)",
            (plan_id, inst["instrument_id"], inst["baseline_quantity"], inst.get("display_note")),
        )
    for tier in tiers:
        conn.execute(
            "INSERT INTO trade_plan_tiers (plan_id, tier_order, pullback_pct, sell_pct, fired_at) "
            "VALUES (?, ?, ?, ?, NULL)",
            (plan_id, tier["tier_order"], tier["pullback_pct"], tier["sell_pct"]),
        )
    return get_plan(conn, plan_id)


def _validate_tiers(tiers: list[dict[str, Any]]) -> None:
    if not tiers:
        return
    ordered = sorted(tiers, key=lambda t: t["tier_order"])
    prev_order = None
    prev_pullback = None
    total_sell_pct = 0.0
    for t in ordered:
        if t["pullback_pct"] <= 0:
            raise ValueError("pullback_pct는 0보다 커야 합니다.")
        if not (0 < t["sell_pct"] <= 100):
            raise ValueError("sell_pct는 0보다 크고 100 이하여야 합니다.")
        if prev_order is not None and t["tier_order"] <= prev_order:
            raise ValueError("tier_order는 오름차순이어야 합니다.")
        if prev_pullback is not None and t["pullback_pct"] <= prev_pullback:
            raise ValueError("pullback_pct는 tier_order 순으로 오름차순이어야 합니다.")
        total_sell_pct += t["sell_pct"]
        prev_order = t["tier_order"]
        prev_pullback = t["pullback_pct"]
    if total_sell_pct > 100 + 1e-9:
        raise ValueError(f"sell_pct 합계는 100% 이하여야 합니다(현재 {total_sell_pct}%).")


def _snapshot_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, default=str)


def _record_history(conn: sqlite3.Connection, plan_id: str, version: int, change_reason: str | None) -> None:
    snapshot = get_plan(conn, plan_id)
    conn.execute(
        "INSERT INTO trade_plan_history (plan_id, version, snapshot_json, change_reason, changed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (plan_id, version, _snapshot_json(snapshot), change_reason, utcnow_iso()),
    )


def get_history(conn: sqlite3.Connection, plan_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM trade_plan_history WHERE plan_id = ? ORDER BY version DESC", (plan_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["snapshot"] = json.loads(d["snapshot_json"])
        result.append(d)
    return result


def update_plan_fields(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    change_reason: str,
    label: str | None = None,
    trigger_price: float | None = None,
    confirm_mode: str | None = None,
    purpose: str | None = None,
    invalidation_condition: str | None = None,
    review_date: str | None = None,
    reason: str | None = None,
) -> Optional[dict[str, Any]]:
    """트리거 발동 이후(ACTIVE 이상)의 계획도 수정 자체는 허용하되(예: 재검토
    후 하락률 조정), 이미 발동한 tier의 fired_at/스냅샷은 절대 건드리지
    않는다 -- 이 함수는 tiers 테이블을 전혀 수정하지 않는다."""
    current = get_plan(conn, plan_id)
    if current is None:
        return None
    now = utcnow_iso()
    next_version = current["version"] + 1
    conn.execute(
        """
        UPDATE trade_plans SET
            label = ?, trigger_price = ?, confirm_mode = ?, purpose = ?,
            invalidation_condition = ?, review_date = ?, reason = ?,
            version = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            label if label is not None else current["label"],
            trigger_price if trigger_price is not None else current["trigger_price"],
            confirm_mode if confirm_mode is not None else current["confirm_mode"],
            purpose if purpose is not None else current["purpose"],
            invalidation_condition if invalidation_condition is not None else current["invalidation_condition"],
            review_date if review_date is not None else current["review_date"],
            reason if reason is not None else current["reason"],
            next_version, now, plan_id,
        ),
    )
    _record_history(conn, plan_id, next_version, change_reason)
    return get_plan(conn, plan_id)


def cancel_plan(conn: sqlite3.Connection, plan_id: str, *, reason: str) -> Optional[dict[str, Any]]:
    current = get_plan(conn, plan_id)
    if current is None:
        return None
    now = utcnow_iso()
    next_version = current["version"] + 1
    conn.execute(
        "UPDATE trade_plans SET lifecycle_status = 'CANCELLED', version = ?, updated_at = ? WHERE id = ?",
        (next_version, now, plan_id),
    )
    _record_history(conn, plan_id, next_version, reason)
    return get_plan(conn, plan_id)


def apply_evaluation(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    new_lifecycle_status: str,
    new_trigger_activated_at: str | None,
    new_peak_price: float | None,
    new_approach_notified_at: str | None,
    tier_updates: list[dict[str, Any]],  # [{tier_order, fired_at, fired_peak_price, fired_reference_price}]
) -> None:
    """trade_plan_engine.PlanEvaluation을 그대로 반영한다. history는 여기서
    남기지 않는다(자동 평가는 "사용자 수정"이 아니라 시세 반영일 뿐이고,
    fired_at 자체가 이미 tier 테이블에 영구 기록되므로 별도 스냅샷이 불필요)."""
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE trade_plans SET
            lifecycle_status = ?, trigger_activated_at = ?, peak_price_since_trigger = ?,
            approach_notified_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_lifecycle_status, new_trigger_activated_at, new_peak_price, new_approach_notified_at, now, plan_id),
    )
    for u in tier_updates:
        conn.execute(
            "UPDATE trade_plan_tiers SET fired_at = ?, fired_peak_price = ?, fired_reference_price = ? "
            "WHERE plan_id = ? AND tier_order = ? AND fired_at IS NULL",
            (u["fired_at"], u["fired_peak_price"], u["fired_reference_price"], plan_id, u["tier_order"]),
        )
