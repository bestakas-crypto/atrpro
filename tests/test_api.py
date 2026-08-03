"""FastAPI 엔드포인트 통합 테스트 -- TestClient로 실제 HTTP 요청/응답 경로를 검증한다."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from atrsite.config import settings
    from atrsite.main import create_app

    # frozen dataclass 싱글턴을 테스트별 임시 경로로 바꿔친다 -- main.py/deps.py
    # 둘 다 이 동일 인스턴스를 참조하므로 여기서 한 번만 바꾸면 전체에 적용된다.
    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "frontend_dir", tmp_path / "no_frontend")
    object.__setattr__(settings, "api_key", "")

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_and_get_instrument(client):
    resp = client.post("/api/v1/instruments", json={"name": "삼성전자", "currency": "KRW"})
    assert resp.status_code == 201
    inst = resp.json()
    assert inst["buy_multiple"] == pytest.approx(1.0)

    resp = client.get(f"/api/v1/instruments/{inst['id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["instrument"]["name"] == "삼성전자"
    assert detail["position"]["quantity"] == 0
    assert detail["trades"] == []


def test_full_trade_flow_matches_spec_example(client):
    """스펙 필수 예시를 HTTP 경로로 재확인: 평균단가 150원."""
    inst = client.post("/api/v1/instruments", json={"name": "테스트", "buy_multiple": 1.0, "sell_multiple": 1.5, "stop_multiple": 2.0}).json()
    iid = inst["id"]

    r1 = client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    assert r1.status_code == 201

    r2 = client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "sell", "price": 120, "quantity": 5, "executed_at": "2026-01-02T09:00:00",
    })
    assert r2.status_code == 201

    r3 = client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 200, "quantity": 5, "executed_at": "2026-01-03T09:00:00",
    })
    assert r3.status_code == 201

    detail = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail["position"]["avg_price"] == pytest.approx(150)
    assert detail["position"]["quantity"] == pytest.approx(10)
    assert len(detail["trades"]) == 3


def test_oversell_returns_409(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
    iid = inst["id"]
    client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    resp = client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "sell", "price": 100, "quantity": 11, "executed_at": "2026-01-02T09:00:00",
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["available_quantity"] == pytest.approx(10)


def test_invalid_trade_type_returns_422(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
    resp = client.post(f"/api/v1/instruments/{inst['id']}/trades", json={
        "trade_type": "hold", "price": 100, "quantity": 1, "executed_at": "2026-01-01T09:00:00",
    })
    assert resp.status_code == 422  # pydantic Literal 검증에서 이미 걸러짐


def test_quote_and_atr_commit_produce_signal(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트", "buy_multiple": 1.0, "sell_multiple": 1.5, "stop_multiple": 2.0}).json()
    iid = inst["id"]
    client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    client.post(f"/api/v1/instruments/{iid}/atr", json={"atr": 10, "trade_date": "2026-01-01"})
    client.post(f"/api/v1/instruments/{iid}/quote", json={"price": 140})

    detail = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail["signal"]["status"] == "TAKE_PROFIT_TRIGGERED"
    # 현재가 140이 auto_update_high로 최고가를 100->140까지 끌어올렸으므로
    # 손절선도 레칫으로 100-10*2=80 -> 140-10*2=120까지 같이 올라가야 한다.
    assert detail["instrument"]["post_entry_high_price"] == pytest.approx(140)
    assert detail["instrument"]["trailing_stop_price"] == pytest.approx(120)


def test_settings_update_creates_new_strategy_version(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트", "buy_multiple": 1.0}).json()
    resp = client.patch(f"/api/v1/instruments/{inst['id']}", json={"buy_multiple": 2.5})
    assert resp.status_code == 200
    assert resp.json()["buy_multiple"] == pytest.approx(2.5)


def test_reset_instrument_clears_trades(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
    iid = inst["id"]
    client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    resp = client.post(f"/api/v1/instruments/{iid}/reset")
    assert resp.status_code == 200
    detail = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail["trades"] == []
    assert detail["position"]["quantity"] == 0


def test_delete_instrument(client):
    inst = client.post("/api/v1/instruments", json={"name": "삭제될종목"}).json()
    resp = client.delete(f"/api/v1/instruments/{inst['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/v1/instruments/{inst['id']}").status_code == 404


def test_deposits_crud(client):
    created = client.post("/api/v1/deposits", json={"account_name": "증권", "amount": 1000, "currency": "USD"}).json()
    listed = client.get("/api/v1/deposits").json()
    assert len(listed) == 1

    updated = client.patch(f"/api/v1/deposits/{created['id']}", json={"amount": 2000}).json()
    assert updated["amount"] == pytest.approx(2000)

    resp = client.delete(f"/api/v1/deposits/{created['id']}")
    assert resp.status_code == 204
    assert client.get("/api/v1/deposits").json() == []


def test_dashboard_totals_with_fx(client):
    client.put("/api/v1/fx", json={"rates": {"USD": 1300}, "display_currency": "KRW"})
    inst = client.post("/api/v1/instruments", json={"name": "달러종목", "currency": "USD"}).json()
    client.post(f"/api/v1/instruments/{inst['id']}/trades", json={
        "trade_type": "buy", "price": 10, "quantity": 100, "executed_at": "2026-01-01T09:00:00",
    })
    dash = client.get("/api/v1/dashboard").json()
    assert dash["totals"]["cost_basis"] == pytest.approx(10 * 100 * 1300)
    assert dash["totals"]["missing_cost_count"] == 0


def test_dashboard_includes_atr_per_instrument(client):
    """보고서(2026-08-02 추가)가 종목별 ATR을 표시하려면 대시보드 응답에
    필요 -- instrument_snapshot()에는 이미 있었지만 build_dashboard()의
    행에는 빠져 있던 필드."""
    inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
    client.post(f"/api/v1/instruments/{inst['id']}/atr", json={"atr": 12.5, "trade_date": "2026-01-01"})
    dash = client.get("/api/v1/dashboard").json()
    row = next(r for r in dash["instruments"] if r["instrument"]["id"] == inst["id"])
    assert row["atr"]["atr"] == 12.5


def test_acknowledge_signal_endpoint(client):
    inst = client.post("/api/v1/instruments", json={"name": "테스트", "buy_multiple": 1.0, "sell_multiple": 1.5, "stop_multiple": 2.0}).json()
    iid = inst["id"]
    client.post(f"/api/v1/instruments/{iid}/trades", json={
        "trade_type": "buy", "price": 100, "quantity": 10, "executed_at": "2026-01-01T09:00:00",
    })
    client.post(f"/api/v1/instruments/{iid}/atr", json={"atr": 10, "trade_date": "2026-01-01"})
    client.post(f"/api/v1/instruments/{iid}/quote", json={"price": 79})  # 손절선 80 이하

    detail = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail["signal"]["status"] == "STOP_TRIGGERED"
    assert detail["signal"]["acknowledged_event_id"] is None

    resp = client.post(f"/api/v1/instruments/{iid}/acknowledge")
    assert resp.status_code == 200
    assert resp.json()["acknowledged_event_id"] == resp.json()["latest_event_id"]

    detail_after = client.get(f"/api/v1/instruments/{iid}").json()
    assert detail_after["signal"]["status"] == "STOP_TRIGGERED"  # 상태 자체는 그대로
    assert detail_after["signal"]["acknowledged_event_id"] == detail_after["signal"]["latest_event_id"]


def test_acknowledge_signal_404_for_unknown_instrument(client):
    resp = client.post("/api/v1/instruments/does-not-exist/acknowledge")
    assert resp.status_code == 404


def test_refresh_quote_requires_kis_code(client):
    from atrsite.config import settings
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    try:
        inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
        resp = client.post(f"/api/v1/instruments/{inst['id']}/quote/refresh")
        assert resp.status_code == 400
    finally:
        object.__setattr__(settings, "kis_app_key", original_key)
        object.__setattr__(settings, "kis_app_secret", original_secret)


def test_refresh_quote_succeeds_with_dummy_kis_when_code_set(client):
    from atrsite.config import settings
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    try:
        inst = client.post("/api/v1/instruments", json={"name": "테스트", "kis_code": "005930", "kis_market": "KRX"}).json()
        resp = client.post(f"/api/v1/instruments/{inst['id']}/quote/refresh")
        assert resp.status_code == 200
        detail = client.get(f"/api/v1/instruments/{inst['id']}").json()
        assert detail["quote"]["source"] == "kis"
    finally:
        object.__setattr__(settings, "kis_app_key", original_key)
        object.__setattr__(settings, "kis_app_secret", original_secret)


def test_refresh_all_quotes(client):
    from atrsite.config import settings
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    try:
        client.post("/api/v1/instruments", json={"name": "코드있음", "kis_code": "005930"})
        client.post("/api/v1/instruments", json={"name": "코드없음"})

        resp = client.post("/api/v1/dashboard/refresh-quotes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] == ["코드있음"]
        assert body["skipped"] == ["코드없음"]
        assert body["failed"] == []
    finally:
        object.__setattr__(settings, "kis_app_key", original_key)
        object.__setattr__(settings, "kis_app_secret", original_secret)


def test_create_instrument_with_kis_code_backfills_atr_immediately(client):
    """장마감(worker.py의 다음 POST_MARKET)까지 기다리지 않고, 종목 등록
    직후 바로 ATR이 채워져야 한다(2026-08-02 추가, GPT 리뷰 계기로 발견한
    실제 갭)."""
    from atrsite.config import settings
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    try:
        inst = client.post(
            "/api/v1/instruments", json={"name": "테스트", "kis_code": "005930", "kis_market": "KRX"}
        ).json()
        assert inst["trailing_stop_price"] is None  # 아직 매수 전이라 레칫은 없음
        detail = client.get(f"/api/v1/instruments/{inst['id']}").json()
        assert detail["atr"] is not None
        assert detail["atr"]["atr"] > 0
    finally:
        object.__setattr__(settings, "kis_app_key", original_key)
        object.__setattr__(settings, "kis_app_secret", original_secret)


def test_create_instrument_without_kis_code_leaves_atr_empty(client):
    inst = client.post("/api/v1/instruments", json={"name": "수동종목"}).json()
    detail = client.get(f"/api/v1/instruments/{inst['id']}").json()
    assert detail["atr"] is None


def test_update_settings_backfills_atr_when_kis_code_added_later(client):
    """처음엔 KIS 코드 없이 등록했다가 설정에서 나중에 채우는 경우도 동일하게
    바로 백필돼야 한다."""
    from atrsite.config import settings
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    try:
        inst = client.post("/api/v1/instruments", json={"name": "테스트"}).json()
        detail_before = client.get(f"/api/v1/instruments/{inst['id']}").json()
        assert detail_before["atr"] is None

        client.patch(f"/api/v1/instruments/{inst['id']}", json={"kis_code": "005930", "kis_market": "KRX"})
        detail_after = client.get(f"/api/v1/instruments/{inst['id']}").json()
        assert detail_after["atr"] is not None
    finally:
        object.__setattr__(settings, "kis_app_key", original_key)
        object.__setattr__(settings, "kis_app_secret", original_secret)


def test_update_settings_does_not_overwrite_existing_atr(client):
    """이미 ATR이 있는 종목의 설정을 저장할 때(배수만 바꾸는 경우 등) 기존
    ATR을 덮어써서는 안 된다."""
    from atrsite.config import settings
    original_key, original_secret = settings.kis_app_key, settings.kis_app_secret
    object.__setattr__(settings, "kis_app_key", "")
    object.__setattr__(settings, "kis_app_secret", "")
    try:
        inst = client.post(
            "/api/v1/instruments", json={"name": "테스트", "kis_code": "005930", "kis_market": "KRX"}
        ).json()
        # get_latest_atr()은 trade_date가 가장 최신인 행을 고른다 -- 방금 자동
        # 백필된 KIS 더미 ATR의 trade_date보다 확실히 미래인 날짜를 써야
        # 이 수동 값이 "최신"으로 남는다.
        client.post(f"/api/v1/instruments/{inst['id']}/atr", json={"atr": 999.0, "trade_date": "2030-01-01"})
        detail_before = client.get(f"/api/v1/instruments/{inst['id']}").json()
        assert detail_before["atr"]["atr"] == 999.0

        # kis_code를 다시 포함해서 저장해도(예: 배수를 바꾸면서 그대로 다시
        # 보냄) 이미 ATR이 있으면 백필 로직이 개입하면 안 된다.
        client.patch(
            f"/api/v1/instruments/{inst['id']}",
            json={"buy_multiple": 1.5, "kis_code": "005930", "kis_market": "KRX"},
        )
        detail_after = client.get(f"/api/v1/instruments/{inst['id']}").json()
        assert detail_after["atr"]["atr"] == 999.0  # 자동 백필로 덮어써지면 안 됨
    finally:
        object.__setattr__(settings, "kis_app_key", original_key)
        object.__setattr__(settings, "kis_app_secret", original_secret)


def test_api_key_enforced_when_configured(client):
    from atrsite.config import settings
    object.__setattr__(settings, "api_key", "secret123")
    try:
        resp = client.get("/api/v1/instruments")
        assert resp.status_code == 401
        resp = client.get("/api/v1/instruments", headers={"X-API-Key": "secret123"})
        assert resp.status_code == 200
    finally:
        object.__setattr__(settings, "api_key", "")


@pytest.fixture()
def force_llm_dummy_and_stub_vxn(monkeypatch):
    """analysis API 테스트가 실제 LLM/VXN 네트워크를 타지 않게 막는다."""
    from atrsite.adapters.market_index_client import IndexQuote
    from atrsite.config import settings
    from atrsite.services import analysis_service

    original = (
        settings.anthropic_api_key, settings.openai_api_key,
        settings.gemini_api_key, settings.deepseek_api_key,
    )
    object.__setattr__(settings, "anthropic_api_key", "")
    object.__setattr__(settings, "openai_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "")
    object.__setattr__(settings, "deepseek_api_key", "")

    def fake_fetch(symbol, label, *, http=None):
        return IndexQuote(symbol=symbol, label=label, status="OK", value=26.0)

    monkeypatch.setattr(analysis_service, "fetch_index", fake_fetch)
    yield
    object.__setattr__(settings, "anthropic_api_key", original[0])
    object.__setattr__(settings, "openai_api_key", original[1])
    object.__setattr__(settings, "gemini_api_key", original[2])
    object.__setattr__(settings, "deepseek_api_key", original[3])


def test_analysis_latest_404_before_any_run(client, force_llm_dummy_and_stub_vxn):
    resp = client.get("/api/v1/analysis/latest")
    assert resp.status_code == 404


def test_analysis_run_and_latest(client, force_llm_dummy_and_stub_vxn):
    resp = client.post("/api/v1/analysis/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "dummy"
    assert "더미" in body["result_text"]

    latest = client.get("/api/v1/analysis/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]


def test_analysis_run_reuses_cache_without_force(client, force_llm_dummy_and_stub_vxn):
    first = client.post("/api/v1/analysis/run").json()
    second = client.post("/api/v1/analysis/run").json()
    assert second["id"] == first["id"]

    third = client.post("/api/v1/analysis/run?force=true").json()
    assert third["id"] != first["id"]


# ---------------------------------------------------------------------------
# 종목탐구(company-explorer) API -- 2026-08-03 추가. SEC 호출은 monkeypatch,
# LLM은 더미모드(force_llm_dummy_and_stub_vxn 재사용 -- VXN은 이 API와
# 무관하지만 LLM 키를 비우는 부분만 필요해서 그대로 씀). OpenDART는 이
# 개발 환경의 .env에 실제 키가 들어갈 수 있어서(2026-08-03 이후), 미국만
# 검증하는 테스트는 명시적으로 키를 비운다(kis_client.py와 동일 원칙).
# ---------------------------------------------------------------------------

@pytest.fixture()
def no_opendart_key():
    from atrsite.config import settings
    original = settings.opendart_api_key
    object.__setattr__(settings, "opendart_api_key", "")
    yield
    object.__setattr__(settings, "opendart_api_key", original)


@pytest.fixture()
def stub_sec_search(monkeypatch):
    from atrsite.adapters import sec_edgar_client

    monkeypatch.setattr(
        sec_edgar_client, "search_companies",
        lambda q, **kw: [sec_edgar_client.CompanyMatch(cik=723125, ticker="MU", title="MICRON TECHNOLOGY INC")],
    )


@pytest.fixture()
def stub_sec_facts(monkeypatch):
    from atrsite.adapters import sec_edgar_client

    def fake_normalize(facts, *, period_type, limit=8):
        if period_type != "QUARTER":
            return []
        return [
            sec_edgar_client.NormalizedPeriod(
                fiscal_year=2025, fiscal_period="Q4", period_type="QUARTER", period_end="2025-11-27",
                filed_at="2025-12-18", accession_no="0000723125-25-000046",
                metrics={"revenue": 13643000000.0, "gross_profit": 7646000000.0, "net_income": 5240000000.0},
            ),
            sec_edgar_client.NormalizedPeriod(
                fiscal_year=2026, fiscal_period="Q1", period_type="QUARTER", period_end="2026-02-26",
                filed_at="2026-03-19", accession_no="0000723125-26-000006",
                metrics={"revenue": 23860000000.0, "gross_profit": 17755000000.0, "net_income": 13785000000.0},
            ),
        ]

    monkeypatch.setattr(sec_edgar_client, "fetch_company_facts", lambda cik, **kw: {"stub": True})
    monkeypatch.setattr(sec_edgar_client, "normalize_periods", fake_normalize)


def test_company_search_returns_us_matches(client, stub_sec_search, no_opendart_key):
    resp = client.get("/api/v1/company/search?q=MU")
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["ticker"] == "MU"
    assert any("OpenDART" in n for n in body["notices"])


def test_company_search_empty_query_returns_empty(client):
    resp = client.get("/api/v1/company/search?q=")
    assert resp.status_code == 200
    assert resp.json() == {"results": [], "notices": []}


def test_company_resolve_creates_company(client):
    resp = client.post("/api/v1/company/resolve", json={"cik": 723125, "ticker": "MU", "name": "MICRON TECHNOLOGY INC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["primary_ticker"] == "MU"
    assert body["industry_template"] == "semiconductor"


def test_company_analysis_run_returns_pending_then_completes(client, force_llm_dummy_and_stub_vxn, stub_sec_facts):
    company = client.post(
        "/api/v1/company/resolve", json={"cik": 723125, "ticker": "MU", "name": "MICRON TECHNOLOGY INC"},
    ).json()

    run_resp = client.post("/api/v1/company-analysis/run", json={"company_id": company["id"]})
    assert run_resp.status_code == 202
    request = run_resp.json()
    assert request["status"] == "PENDING"

    # TestClient는 BackgroundTasks를 응답 반환 전에 동기 실행하므로 이 시점에
    # 이미 끝나 있어야 한다.
    poll = client.get(f"/api/v1/company-analysis/{request['id']}")
    assert poll.status_code == 200
    polled = poll.json()
    assert polled["status"] == "COMPLETED"
    assert polled["result"]["provider"] == "dummy"


def test_company_analysis_run_404_for_unknown_company(client):
    resp = client.post("/api/v1/company-analysis/run", json={"company_id": "no-such-id"})
    assert resp.status_code == 404


def test_company_analysis_get_404_for_unknown_request(client):
    resp = client.get("/api/v1/company-analysis/no-such-request")
    assert resp.status_code == 404


def test_company_analysis_recent_lists_completed_only(client, force_llm_dummy_and_stub_vxn, stub_sec_facts):
    company = client.post(
        "/api/v1/company/resolve", json={"cik": 723125, "ticker": "MU", "name": "MICRON TECHNOLOGY INC"},
    ).json()
    assert client.get("/api/v1/company-analysis/recent").json() == []

    client.post("/api/v1/company-analysis/run", json={"company_id": company["id"]})
    recent = client.get("/api/v1/company-analysis/recent").json()
    assert len(recent) == 1
    assert recent[0]["primary_ticker"] == "MU"
