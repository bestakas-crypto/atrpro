"""legacy_import 서비스 테스트 -- 스펙 16절. 옛 atrsite app.js의 실제
localStorage 스키마(stock-portfolio/cash-deposits/fx-rates)를 그대로 흉내낸
샘플로 검증한다.
"""
import pytest

from atrsite.repositories import deposits as deposits_repo
from atrsite.repositories import fx as fx_repo
from atrsite.repositories import instruments as instruments_repo
from atrsite.repositories import position as position_repo
from atrsite.services import legacy_import


def _sample_payload(**overrides):
    payload = {
        "app_version": "1.0",
        "exported_at": "2026-07-31T10:00:00Z",
        "schema_version": 1,
        "stock-portfolio": [
            {
                "id": "id_abc123",
                "name": "삼성전자",
                "currency": "KRW",
                "buyMultiple": 1.0,
                "sellMultiple": 1.5,
                "stopMultiple": 2.0,
                "postEntryHighPrice": 72000,
                "autoUpdateHigh": True,
                "trailingStopLine": 68000,
                "lastPrice": 71000,
                "lastAtr": 1200,
                "transactions": [
                    {"id": "id_tx1", "type": "buy", "price": 70000, "qty": 10, "date": "2026-07-01", "atrAtExecution": 1000},
                    {"id": "id_tx2", "type": "sell", "price": 72000, "qty": 3, "date": "2026-07-15", "atrAtExecution": 1100},
                ],
            }
        ],
        "cash-deposits": [
            {"id": "id_dep1", "accountName": "우리은행", "amount": 5000000, "currency": "KRW"},
        ],
        "fx-rates": {"rates": {"USD": 1350.5}, "updatedAt": "2026-07-31T09:00:00Z", "source": "manual", "displayCurrency": "KRW"},
    }
    payload.update(overrides)
    return payload


def test_validate_clean_payload_has_no_errors():
    report = legacy_import.validate(_sample_payload())
    assert report.valid
    assert report.errors == []
    assert report.instrument_count == 1
    assert report.trade_count == 2
    assert report.deposit_count == 1
    # trailingStopLine 이전 경고는 정상적으로 뜬다 (에러 아님)
    assert any(w.code == "carried_trailing_stop" for w in report.warnings)


def test_validate_detects_oversell():
    payload = _sample_payload()
    payload["stock-portfolio"][0]["transactions"].append(
        {"id": "id_tx3", "type": "sell", "price": 70000, "qty": 100, "date": "2026-07-20"}
    )
    report = legacy_import.validate(payload)
    assert not report.valid
    assert any(e.code == "oversell" for e in report.errors)


def test_validate_detects_duplicate_ids_and_bad_type():
    payload = _sample_payload()
    payload["stock-portfolio"][0]["transactions"][1]["id"] = "id_tx1"  # 중복
    payload["stock-portfolio"].append({
        "id": "id_abc123",  # 종목 id 중복
        "name": "중복종목", "transactions": [{"id": "x", "type": "hold", "price": 1, "qty": 1, "date": "2026-01-01"}],
    })
    report = legacy_import.validate(payload)
    assert not report.valid
    codes = {e.code for e in report.errors}
    assert "duplicate_id" in codes
    assert "invalid_trade_type" in codes


def test_apply_writes_instrument_trades_deposits_and_fx(db_conn):
    report = legacy_import.apply(db_conn, _sample_payload())
    assert report.valid

    instruments = instruments_repo.list_instruments(db_conn)
    assert len(instruments) == 1
    inst = instruments[0]
    assert inst["name"] == "삼성전자"
    assert inst["trailing_stop_price"] == pytest.approx(68000)
    assert inst["post_entry_high_price"] == pytest.approx(72000)

    position = position_repo.get_position(db_conn, inst["id"])
    assert position["quantity"] == pytest.approx(7)  # 10 - 3
    assert position["avg_price"] == pytest.approx(70000)

    deposits = deposits_repo.list_deposits(db_conn)
    assert len(deposits) == 1
    assert deposits[0]["account_name"] == "우리은행"

    rates = fx_repo.list_rates(db_conn)
    assert rates["USD"]["rate_to_krw"] == pytest.approx(1350.5)
    assert fx_repo.get_display_currency(db_conn) == "KRW"


def test_apply_rejects_invalid_payload_without_writing(db_conn):
    payload = _sample_payload()
    payload["stock-portfolio"][0]["transactions"][0]["qty"] = -5
    report = legacy_import.apply(db_conn, payload)
    assert not report.valid
    assert instruments_repo.list_instruments(db_conn) == []
