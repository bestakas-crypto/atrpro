"""기존 ATRsite(C:\\atrsite) localStorage 데이터 이전 -- 스펙 16절.

내보내기 포맷 (스펙 16.3): 아래 3개 키를 담은 JSON.
    "stock-portfolio": [...]   # 옛 app.js의 portfolio 배열 그대로
    "cash-deposits":   [...]   # 옛 app.js의 deposits 배열 그대로
    "fx-rates":        {...}   # 옛 app.js의 fxRates 객체 그대로
선택 필드: app_version, exported_at, schema_version, checksum(sha256, 있으면
검증하고 없으면 건너뜀 -- 아직 옛 앱에 내보내기 버튼이 없어 수동으로 만든
페이로드도 받아줘야 하기 때문).

두 단계로 나뉜다:
    validate(payload) -> ImportReport   (DB에 아무것도 쓰지 않음)
    apply(conn, payload) -> ImportReport (검증 통과 후 실제 저장, 트랜잭션 1개)
API 계층은 항상 먼저 validate()로 미리보기를 보여주고, 사용자 확인 후에만
apply()를 호출한다 (스펙 16.2 "사전 검증 결과 표시 → 사용자 확인").
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..repositories import deposits as deposits_repo
from ..repositories import fx as fx_repo
from ..repositories import instruments as instruments_repo
from ..repositories import market_data as market_data_repo
from ..repositories import trades as trades_repo
from .portfolio_service import recompute_position, recompute_signal
from .position_engine import InvalidTradeError, OversellError, Trade as EngineTrade, replay_trades

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


@dataclass
class ImportIssue:
    code: str
    message: str
    path: str


@dataclass
class ImportReport:
    valid: bool = True
    errors: list[ImportIssue] = field(default_factory=list)
    warnings: list[ImportIssue] = field(default_factory=list)
    instrument_count: int = 0
    trade_count: int = 0
    deposit_count: int = 0

    def add_error(self, code: str, message: str, path: str) -> None:
        self.errors.append(ImportIssue(code, message, path))
        self.valid = False

    def add_warning(self, code: str, message: str, path: str) -> None:
        self.warnings.append(ImportIssue(code, message, path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [vars(i) for i in self.errors],
            "warnings": [vars(i) for i in self.warnings],
            "summary": {
                "instruments": self.instrument_count,
                "trades": self.trade_count,
                "deposits": self.deposit_count,
            },
        }


def _verify_checksum(payload: dict[str, Any], report: ImportReport) -> None:
    checksum = payload.get("checksum")
    if not checksum:
        return
    canonical = json.dumps(
        {k: payload.get(k) for k in ("stock-portfolio", "cash-deposits", "fx-rates")},
        sort_keys=True, ensure_ascii=False,
    )
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual != checksum:
        report.add_warning("checksum_mismatch", "체크섬이 일치하지 않습니다 (내용은 계속 검증합니다).", "checksum")


def _validate_stocks(stocks: list[Any], report: ImportReport) -> None:
    seen_stock_ids: set[str] = set()
    for si, stock in enumerate(stocks):
        path = f"stock-portfolio[{si}]"
        if not isinstance(stock, dict):
            report.add_error("invalid_shape", "종목 항목이 객체가 아닙니다.", path)
            continue
        stock_id = stock.get("id")
        if not stock_id:
            report.add_error("missing_id", "종목 id가 없습니다.", path)
        elif stock_id in seen_stock_ids:
            report.add_error("duplicate_id", f"중복된 종목 id: {stock_id}", path)
        else:
            seen_stock_ids.add(stock_id)

        currency = stock.get("currency") or "KRW"
        if not CURRENCY_RE.match(currency):
            report.add_warning("currency_format", f"통화 형식이 의심스럽습니다: {currency!r}", f"{path}.currency")

        transactions = stock.get("transactions") or []
        seen_tx_ids: set[str] = set()
        engine_trades: list[EngineTrade] = []
        for ti, tx in enumerate(transactions):
            tx_path = f"{path}.transactions[{ti}]"
            if not isinstance(tx, dict):
                report.add_error("invalid_shape", "거래 항목이 객체가 아닙니다.", tx_path)
                continue
            tx_id = tx.get("id")
            if tx_id and tx_id in seen_tx_ids:
                report.add_error("duplicate_id", f"중복된 거래 id: {tx_id}", tx_path)
            elif tx_id:
                seen_tx_ids.add(tx_id)

            tx_type = tx.get("type")
            if tx_type not in ("buy", "sell"):
                report.add_error("invalid_trade_type", f"알 수 없는 거래유형: {tx_type!r}", f"{tx_path}.type")
                continue

            price = tx.get("price")
            qty = tx.get("qty")
            if not isinstance(price, (int, float)) or price <= 0:
                report.add_error("invalid_price", f"단가가 올바르지 않습니다: {price!r}", f"{tx_path}.price")
                continue
            if not isinstance(qty, (int, float)) or qty <= 0:
                report.add_error("negative_quantity", f"수량이 올바르지 않습니다: {qty!r}", f"{tx_path}.qty")
                continue

            tx_date = tx.get("date")
            if not isinstance(tx_date, str) or not DATE_RE.match(tx_date):
                report.add_error("invalid_date_format", f"날짜 형식이 올바르지 않습니다: {tx_date!r}", f"{tx_path}.date")

            if tx.get("atrAtExecution") is None:
                report.add_warning("missing_atr", "체결 당시 ATR 값이 없습니다 (참고용 필드라 이전에는 지장 없음).", f"{tx_path}.atrAtExecution")

            engine_trades.append(EngineTrade(trade_type=tx_type, price=price, quantity=qty, trade_id=tx_id))
            report.trade_count += 1

        try:
            final_state, _steps = replay_trades(engine_trades)
        except OversellError as exc:
            report.add_error("oversell", f"보유수량 초과 매도가 포함되어 있습니다: {exc}", f"{path}.transactions")
        except InvalidTradeError as exc:
            report.add_error("invalid_trade", str(exc), f"{path}.transactions")
        else:
            if final_state.avg_price < 0 or (final_state.quantity > 0 and final_state.avg_price == 0):
                report.add_warning("abnormal_avg_price", "재계산된 평균단가가 비정상적입니다.", f"{path}")

        if stock.get("trailingStopLine") is not None:
            report.add_warning(
                "carried_trailing_stop",
                "기존 추적 손절선 값을 그대로 이전합니다 (과거 레칫 이력은 재계산하지 않음).",
                f"{path}.trailingStopLine",
            )

        report.instrument_count += 1


def _validate_deposits(deposits: list[Any], report: ImportReport) -> None:
    seen_ids: set[str] = set()
    for di, dep in enumerate(deposits):
        path = f"cash-deposits[{di}]"
        if not isinstance(dep, dict):
            report.add_error("invalid_shape", "예금 항목이 객체가 아닙니다.", path)
            continue
        dep_id = dep.get("id")
        if dep_id and dep_id in seen_ids:
            report.add_error("duplicate_id", f"중복된 예금 id: {dep_id}", path)
        elif dep_id:
            seen_ids.add(dep_id)
        amount = dep.get("amount")
        if not isinstance(amount, (int, float)) or amount < 0:
            report.add_error("negative_quantity", f"예금액이 올바르지 않습니다: {amount!r}", f"{path}.amount")
        currency = dep.get("currency") or "KRW"
        if not CURRENCY_RE.match(currency):
            report.add_warning("currency_format", f"통화 형식이 의심스럽습니다: {currency!r}", f"{path}.currency")
        report.deposit_count += 1


def validate(payload: dict[str, Any]) -> ImportReport:
    report = ImportReport()
    _verify_checksum(payload, report)
    _validate_stocks(payload.get("stock-portfolio") or [], report)
    _validate_deposits(payload.get("cash-deposits") or [], report)
    fx = payload.get("fx-rates")
    if fx is not None and not isinstance(fx, dict):
        report.add_error("invalid_shape", "fx-rates가 객체가 아닙니다.", "fx-rates")
    return report


def apply(conn, payload: dict[str, Any]) -> ImportReport:
    """validate()를 통과했다고 가정하고 실제로 저장한다. 호출자가 트랜잭션
    경계(commit/rollback)를 관리한다."""
    report = validate(payload)
    if not report.valid:
        return report

    for stock in payload.get("stock-portfolio") or []:
        instrument = instruments_repo.create_instrument(
            conn,
            name=stock.get("name") or "이름없음",
            currency=stock.get("currency") or "KRW",
            buy_multiple=stock.get("buyMultiple", 1.0),
            sell_multiple=stock.get("sellMultiple", 1.5),
            stop_multiple=stock.get("stopMultiple", 2.0),
        )
        iid = instrument["id"]
        for tx in stock.get("transactions") or []:
            executed_at = f"{tx['date']}T09:00:00"
            trades_repo.create_trade(
                conn, instrument_id=iid, trade_type=tx["type"], price=tx["price"], quantity=tx["qty"],
                executed_at=executed_at, atr_at_execution=tx.get("atrAtExecution"),
            )
        recompute_position(conn, iid)
        instruments_repo.update_manual_fields(
            conn, iid,
            post_entry_high_price=stock.get("postEntryHighPrice"),
            trailing_stop_price=stock.get("trailingStopLine"),
            auto_update_high=stock.get("autoUpdateHigh", True),
        )
        if stock.get("lastPrice") is not None:
            market_data_repo.upsert_quote(conn, iid, price=stock["lastPrice"], source="manual", data_status="MANUAL_OVERRIDE")
        if stock.get("lastAtr") is not None:
            market_data_repo.upsert_manual_atr(conn, iid, atr=stock["lastAtr"], trade_date=date.today().isoformat())
        recompute_signal(conn, iid)

    for dep in payload.get("cash-deposits") or []:
        deposits_repo.create_deposit(
            conn, account_name=dep.get("accountName") or "이름없음",
            amount=dep.get("amount", 0), currency=dep.get("currency") or "KRW",
        )

    fx = payload.get("fx-rates") or {}
    for currency, rate in (fx.get("rates") or {}).items():
        if currency != "KRW":
            fx_repo.upsert_rate(conn, currency, rate, source=fx.get("source") or "manual")
    if fx.get("displayCurrency"):
        fx_repo.set_display_currency(conn, fx["displayCurrency"])

    return report
