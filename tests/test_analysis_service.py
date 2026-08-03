"""analysis_service.py 테스트 -- 스냅샷 조합 + 캐시 + 더미 LLM 경로 검증.

VXN 조회(market_index_client)는 네트워크를 타므로 여기서는 monkeypatch로
막는다 -- 그 모듈 자체의 파싱/실패 처리는 test_market_index_client.py가
따로 검증한다.
"""
from datetime import datetime, timedelta, timezone

import pytest

from atrsite.adapters import llm_client
from atrsite.adapters.market_index_client import IndexQuote
from atrsite.repositories import analysis as analysis_repo
from atrsite.repositories import fx as fx_repo
from atrsite.repositories import instruments as instruments_repo
from atrsite.repositories import market_data as market_data_repo
from atrsite.services import analysis_service


@pytest.fixture(autouse=True)
def stub_vxn(monkeypatch):
    """VXN 조회는 항상 성공한 것처럼 고정값을 준다(네트워크 호출 방지)."""
    def fake_fetch(symbol, label, *, http=None):
        return IndexQuote(
            symbol=symbol, label=label, status="OK", value=26.0, prev_close=28.39,
            change_pct=-8.4, observed_at="2026-08-03T00:00:00+00:00",
            quote_time="2026-07-31T20:15:01+00:00",
        )
    monkeypatch.setattr(analysis_service, "fetch_index", fake_fetch)


@pytest.fixture()
def force_llm_dummy_mode(monkeypatch):
    from atrsite.config import settings
    original = (
        settings.anthropic_api_key, settings.openai_api_key,
        settings.gemini_api_key, settings.deepseek_api_key,
    )
    object.__setattr__(settings, "anthropic_api_key", "")
    object.__setattr__(settings, "openai_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "")
    object.__setattr__(settings, "deepseek_api_key", "")
    yield
    object.__setattr__(settings, "anthropic_api_key", original[0])
    object.__setattr__(settings, "openai_api_key", original[1])
    object.__setattr__(settings, "gemini_api_key", original[2])
    object.__setattr__(settings, "deepseek_api_key", original[3])


def test_build_macro_snapshot_includes_holdings_fx_and_vxn(db_conn, force_llm_dummy_mode):
    inst = instruments_repo.create_instrument(db_conn, name="QQQ", currency="USD")
    market_data_repo.upsert_quote(db_conn, inst["id"], price=687.99, source="kis", data_status="FRESH", change_pct=1.18)
    fx_repo.upsert_rate(db_conn, "USD", 1449.28)

    snapshot = analysis_service.build_macro_snapshot(db_conn)

    assert snapshot["holdings"][0]["name"] == "QQQ"
    assert snapshot["holdings"][0]["price"] == 687.99
    assert snapshot["holdings"][0]["change_pct"] == 1.18
    assert snapshot["usd_krw"]["value"] == 1449.28
    assert snapshot["vxn"]["value"] == 26.0
    assert snapshot["vxn"]["status"] == "OK"
    # VXN=26.0 -> 25~30 구간 -> QQQ 1주 (2026-08-03 사용자 확인 규칙)
    assert snapshot["vxn"]["grid_rule"]["status"] == "OK"
    assert snapshot["vxn"]["grid_rule"]["ticker"] == "QQQ"
    assert snapshot["vxn"]["grid_rule"]["quantity"] == 1


def test_build_macro_snapshot_marks_missing_quote_as_unavailable(db_conn, force_llm_dummy_mode):
    instruments_repo.create_instrument(db_conn, name="현재가 없는 종목", currency="KRW")
    snapshot = analysis_service.build_macro_snapshot(db_conn)
    assert snapshot["holdings"][0]["status"] == "UNAVAILABLE"
    assert snapshot["holdings"][0]["price"] is None


def test_run_briefing_dummy_mode_stores_result(db_conn, force_llm_dummy_mode):
    result = analysis_service.run_briefing(db_conn)
    assert result["provider"] == "dummy"
    assert "더미" in result["result_text"]
    stored = analysis_repo.get_latest_result(db_conn)
    assert stored["id"] == result["id"]


def test_run_briefing_uses_cache_within_window(db_conn, force_llm_dummy_mode, monkeypatch):
    call_count = {"n": 0}
    original_ask = llm_client.ask

    def counting_ask(*a, **kw):
        call_count["n"] += 1
        return original_ask(*a, **kw)

    monkeypatch.setattr(llm_client, "ask", counting_ask)

    first = analysis_service.run_briefing(db_conn)
    second = analysis_service.run_briefing(db_conn)  # 캐시 윈도우 안 -- 재호출 안 해야 함

    assert call_count["n"] == 1
    assert second["id"] == first["id"]


def test_run_briefing_force_bypasses_cache(db_conn, force_llm_dummy_mode, monkeypatch):
    call_count = {"n": 0}
    original_ask = llm_client.ask

    def counting_ask(*a, **kw):
        call_count["n"] += 1
        return original_ask(*a, **kw)

    monkeypatch.setattr(llm_client, "ask", counting_ask)

    analysis_service.run_briefing(db_conn)
    analysis_service.run_briefing(db_conn, force=True)

    assert call_count["n"] == 2


def test_run_briefing_ignores_stale_cache(db_conn, force_llm_dummy_mode):
    """캐시 윈도우가 지난 오래된 결과는 재사용하면 안 된다."""
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=analysis_service.SNAPSHOT_CACHE_SECONDS + 60)).isoformat()
    analysis_repo.save_result(db_conn, snapshot_json="{}", result_text="옛날 결과", provider="dummy", model="dummy")
    db_conn.execute("UPDATE analysis_results SET created_at = ?", (old_time,))
    db_conn.commit()

    result = analysis_service.run_briefing(db_conn)
    assert result["result_text"] != "옛날 결과"


# ---------------------------------------------------------------------------
# VXN 그리드 매수 규칙 (2026-08-03 사용자 확인: 0~15 IQQ 10주 / 15~24 QQQM
# 1주 / 25~30 QQQ 1주 / 30~40 상황체크). 24~25, 40 초과는 정의 안 된 구간.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vxn,expected_status,expected_ticker,expected_qty", [
    (0.0, "OK", "IQQ", 10),
    (14.9, "OK", "IQQ", 10),
    (15.0, "OK", "QQQM", 1),
    (23.9, "OK", "QQQM", 1),
    (25.0, "OK", "QQQ", 1),
    (29.9, "OK", "QQQ", 1),
    (30.0, "SITUATION_CHECK", None, None),
    (39.9, "SITUATION_CHECK", None, None),
])
def test_check_vxn_grid_rule_bands(vxn, expected_status, expected_ticker, expected_qty):
    result = analysis_service.check_vxn_grid_rule(vxn)
    assert result["status"] == expected_status
    if expected_ticker is not None:
        assert result["ticker"] == expected_ticker
        assert result["quantity"] == expected_qty


def test_check_vxn_grid_rule_undefined_gap_and_out_of_range():
    """24~25(정의 안 된 틈)와 40 초과는 임의로 채우지 않고 UNDEFINED로 남긴다."""
    assert analysis_service.check_vxn_grid_rule(24.5)["status"] == "UNDEFINED"
    assert analysis_service.check_vxn_grid_rule(45.0)["status"] == "UNDEFINED"


def test_check_vxn_grid_rule_unavailable_when_no_value():
    assert analysis_service.check_vxn_grid_rule(None)["status"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 2단계 파이프라인 -- 1단계는 stage1_search 체인(Gemini 우선, 객관적 조회),
# 2단계는 stage2_judgment 체인(Gemini 우선, 실패시 GPT/DeepSeek/Claude 순)
# 으로 호출돼야 한다 (2026-08-03 사용자 확인 사항 + 이후 "클로드가 더
# 비싸니 제미나이-GPT-클로드 순으로" 지시 반영).
# ---------------------------------------------------------------------------

def test_run_briefing_two_stage_routing(db_conn, monkeypatch):
    from atrsite.config import settings

    original = (settings.anthropic_api_key, settings.openai_api_key, settings.gemini_api_key)
    object.__setattr__(settings, "anthropic_api_key", "dummy-anthropic-key")
    object.__setattr__(settings, "openai_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "dummy-gemini-key")

    calls = []

    def fake_ask(system, user, *, use_web_search=False, chain=None, max_tokens=None):
        calls.append({"chain": chain, "use_web_search": use_web_search, "max_tokens": max_tokens})
        if chain == "stage1_search":
            return llm_client.LLMResponse(text="1단계 객관적 조회 결과", provider="gemini", model="gemini-test")
        return llm_client.LLMResponse(text="2단계 최종 브리핑", provider="gemini", model="gemini-test")

    monkeypatch.setattr(llm_client, "ask", fake_ask)
    try:
        result = analysis_service.run_briefing(db_conn)
    finally:
        object.__setattr__(settings, "anthropic_api_key", original[0])
        object.__setattr__(settings, "openai_api_key", original[1])
        object.__setattr__(settings, "gemini_api_key", original[2])

    assert len(calls) == 2
    assert calls[0]["chain"] == "stage1_search"
    assert calls[0]["use_web_search"] is True
    assert calls[1]["chain"] == "stage2_judgment"
    # 2026-08-03 -- 실서버에서 161자짜리 반토막 브리핑이 저장된 걸 발견
    # (기본 MAX_TOKENS=2000으로는 10항목 전체를 못 채우고 잘림). 종목탐구
    # 20항목과 같은 예산(8000)을 명시적으로 주는지 검증.
    assert calls[1]["max_tokens"] == 8000
    assert result["result_text"] == "2단계 최종 브리핑"
    assert result["provider"] == "gemini"
