"""LLM 매크로 브리핑 오케스트레이션 -- 2026-08-03 추가, 같은 날 사용자 확인
후 2단계 파이프라인/VXN 그리드 규칙 반영.

핵심 원칙(사용자 지시): LLM은 검증이 필요한 수치를 스스로 추측하면 안 된다.
이 모듈이 "검증된 Macro Snapshot"(보유종목/환율/VXN, 프로그램이 직접
계산·조회)을 먼저 만들고, VXN 그리드 매수 규칙도 Python이 판정한다.
실시간까지는 필요 없지만 그래도 객관적인 조회가 필요한 값(SOXX/MU/미 국채
금리/일본 국채금리/JPY환율 등)은 LLM 웹서치로 찾되, "객관적 조회는 제미나이,
해석/판단은 클로드(또는 GPT)"로 역할을 나눈 2단계 구조를 쓴다:

  1단계(Gemini 우선, 웹서치 켬): SOXX/MU/US10Y/US30Y/JP10Y/JPY-KRW 등을
    있는 그대로 찾아 보고만 함 -- 해석/추천 없이 사실+출처만.
  2단계(Claude 우선, GPT 폴백): 검증된 스냅샷 + VXN 그리드 판정(Python 확정
    결과) + 1단계 조회 결과를 근거로 최종 10항목 브리핑을 작성.

범위(2026-08-03, 이후 필요해지면 확장):
  - 버튼 하나("오늘 브리핑")
  - 뉴스: 자체 파이프라인 없이 LLM 웹서치에 위임
  - 캐시: 같은 스냅샷이면 재호출 안 함(SNAPSHOT_CACHE_SECONDS 이내 재요청은
    최신 저장 결과를 그대로 반환)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from ..adapters import llm_client
from ..adapters.market_index_client import SYMBOL_VXN, fetch_index
from ..repositories import analysis as analysis_repo
from ..repositories import fx as fx_repo
from ..repositories import instruments as instruments_repo
from ..repositories import market_data as market_data_repo

SNAPSHOT_CACHE_SECONDS = 5 * 60  # 버튼 연타해도 5분 안이면 재호출 안 함

# ---------------------------------------------------------------------------
# VXN 그리드 매수 규칙 (2026-08-03 사용자 확인) -- 매일 적립하는 개인 그리드
# 매수법. 하한 포함/상한 미포함(half-open)으로 해석했다. 사용자가 준 값에
# 24~25, 40 초과 구간이 원래 정의돼 있지 않아서 그 구간은 UNDEFINED로 남기고
# 임의로 채우지 않았다 -- 실제로 그 값이 나오면 화면에서 "정의 안 된 구간"
# 이라고 명확히 보여주고 사용자가 그때 판단하게 한다.
# ---------------------------------------------------------------------------
VXN_GRID_RULES: list[tuple[float, float, Optional[str], Optional[int]]] = [
    (0, 15, "IQQ", 10),
    (15, 24, "QQQM", 1),
    (25, 30, "QQQ", 1),
    (30, 40, None, None),  # 상황 체크 후 매수 판단 -- 자동 확정 안 함
]


def check_vxn_grid_rule(vxn_value: Optional[float]) -> dict[str, Any]:
    """VXN 값으로 오늘의 그리드 매수 대상을 판정한다 -- Python이 확정하고
    LLM은 이 결과를 그대로 설명만 한다(재판정 금지)."""
    if vxn_value is None:
        return {"status": "UNAVAILABLE", "message": "VXN 값을 가져오지 못해 판정할 수 없습니다."}
    for lo, hi, ticker, qty in VXN_GRID_RULES:
        if lo <= vxn_value < hi:
            if ticker is None:
                return {
                    "status": "SITUATION_CHECK",
                    "band": [lo, hi],
                    "message": f"VXN {vxn_value:.1f} -- {lo}~{hi} 구간은 자동 판정 대상이 아니라 상황을 직접 확인한 뒤 매수 여부를 정해야 합니다.",
                }
            return {
                "status": "OK",
                "band": [lo, hi],
                "ticker": ticker,
                "quantity": qty,
                "message": f"VXN {vxn_value:.1f} -- {ticker} {qty}주 매수 구간입니다.",
            }
    return {
        "status": "UNDEFINED",
        "message": f"VXN {vxn_value:.1f} -- 정의된 구간(0~40) 밖입니다. 그리드 규칙을 다시 확인해주세요.",
    }


# ---------------------------------------------------------------------------
# 1단계: 객관적 조회(Gemini 우선) -- SOXX/MU는 사용자 확인 항목, DXY/VIX는
# Claude가 판단해서 추가한 항목(반도체 비중이 큰 그리드 전략이라 SOXX/MU와
# 함께 보면 유용, VIX는 VXN의 나스닥100 특화 버전과 대비되는 전체 시장
# 변동성 지표라서 추가함). 실시간일 필요는 없다는 사용자 확인에 따라 이
# 항목들은 하드 검증된 fetch 대신 LLM 웹서치 결과를 그대로 쓰되, 출처와
# 조회 시점을 반드시 명시하게 한다.
# ---------------------------------------------------------------------------
STAGE1_WATCHLIST = [
    "SOXX(필라델피아 반도체 ETF) 최근 추이",
    "MU(마이크론) 주가 및 최근 변화",
    "미국 10년물 국채금리(US10Y)",
    "미국 30년물 국채금리(US30Y)",
    "일본 10년물 국채금리(JP10Y)",
    "USD/KRW, JPY/KRW 환율",
    "달러인덱스(DXY)",
    "VIX(S&P500 변동성지수)",
]

STAGE1_SYSTEM_PROMPT = """\
당신은 객관적 시장 데이터 조회 담당입니다. 해석하거나 추천하지 마십시오.
아래 원칙을 지키십시오.

1. 웹서치로 오늘 기준 실제 값을 찾아 사실만 보고하십시오. 모르면 "확인 안 됨"
   이라고 쓰고 절대 지어내지 마십시오.
2. 각 항목마다 값, 조회 시점(또는 기준일), 출처를 함께 쓰십시오. 실시간이
   아니어도 됩니다 -- 정확히 언제 기준인지만 명시하면 됩니다.
3. 의견/전망/투자판단을 절대 포함하지 마십시오. 이 결과는 다음 단계에서
   다른 모델이 해석에 사용합니다.
4. 마지막에 "오늘의 주요 매크로/시장 뉴스" 3~5건을 제목+출처+한 줄 요약으로
   추가하십시오.
"""


def _build_stage1_prompt() -> str:
    items = "\n".join(f"- {item}" for item in STAGE1_WATCHLIST)
    return f"다음 항목을 웹서치로 조회해서 사실만 보고해주세요:\n\n{items}"


# ---------------------------------------------------------------------------
# 2단계: 해석/판단(Claude 우선, GPT 폴백)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
당신은 개인 투자자를 위한 매크로 브리핑 보조원입니다. 아래 원칙을 반드시 지키세요.

1. 절대 규칙: "검증된 Macro Snapshot"에 제공된 수치와 "1단계 객관적 조회
   결과"에 있는 내용만 근거로 삼으십시오. 그 외의 수치(금리, 지수, 환율
   등)를 스스로 알고 있다고 가정해서 말하면 안 됩니다. 모르는 값은
   "제공된 데이터에 없음"이라고 명시하십시오.
2. "VXN 그리드 규칙 판정"은 Python 프로그램이 이미 확정한 결과입니다.
   당신은 이 결과를 재계산하거나 다르게 판단하지 말고, 그대로 설명만
   하십시오.
3. 이 프로그램은 절대 자동으로 주문을 넣지 않습니다. 매수/매도를 단정적으로
   지시하지 말고, 참고용 시나리오로만 제시하십시오.
4. 다음 10개 항목 구조를 그대로 따르되, 항목별 소제목을 붙이십시오:
   1) 한 줄 결론
   2) 객관적 핵심 수치 (제공된 값 그대로, 출처/기준시각 함께)
   3) 전일 대비 가장 큰 변화
   4) 오늘의 주요 뉴스 (최대 3건, 출처 명시)
   5) 상승 요인
   6) 하락 요인
   7) 오늘 유념할 일정 (아는 범위 내에서만, 모르면 생략)
   8) 사용자 보유 종목 및 VXN 그리드 매수 규칙 관련 참고사항
   9) 기본/상승/하락 시나리오와 각 시나리오를 무효화하는 조건
   10) 데이터 누락/지연 항목 (UNAVAILABLE/확인 안 됨으로 표시된 값을 모아서 언급)
5. 전체 한국어로, 운전 중에도 들을 수 있게 문장은 짧고 명확하게 쓰십시오.
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_macro_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """검증된 수치만 모은 스냅샷(하드 검증 -- KIS/fx/Yahoo 지수 직접 조회).
    VXN 그리드 판정도 여기서 Python이 확정한다."""
    holdings = []
    for inst in instruments_repo.list_instruments(conn):
        quote = market_data_repo.get_quote(conn, inst["id"])
        holdings.append({
            "name": inst["name"],
            "currency": inst["currency"],
            "price": quote["price"] if quote else None,
            "change_pct": quote.get("change_pct") if quote else None,
            "quoted_at": quote["quoted_at"] if quote else None,
            "data_status": quote["data_status"] if quote else "INSUFFICIENT_DATA",
            "status": "OK" if quote else "UNAVAILABLE",
        })

    rates = fx_repo.list_rates(conn)
    usd_krw = rates.get("USD")

    vxn = fetch_index(SYMBOL_VXN, "VXN")
    vxn_grid = check_vxn_grid_rule(vxn.value)

    return {
        "generated_at": _now_iso(),
        "holdings": holdings,
        "usd_krw": {
            "value": usd_krw["rate_to_krw"] if usd_krw else None,
            "source": usd_krw["source"] if usd_krw else None,
            "updated_at": usd_krw["updated_at"] if usd_krw else None,
            "status": "OK" if usd_krw else "UNAVAILABLE",
        },
        "vxn": {
            "value": vxn.value,
            "prev_close": vxn.prev_close,
            "change_pct": vxn.change_pct,
            "quote_time": vxn.quote_time,
            "source": vxn.source,
            "status": vxn.status,
            "grid_rule": vxn_grid,
        },
    }


def _build_stage2_prompt(snapshot: dict[str, Any], stage1_findings: str) -> str:
    return (
        "[검증된 Macro Snapshot -- 프로그램이 직접 계산/조회했고 VXN 그리드 "
        "규칙 판정도 이미 확정됨(JSON)]\n\n"
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n\n"
        "[1단계 객관적 조회 결과 -- 다른 모델이 웹서치로 찾은 사실 보고]\n\n"
        f"{stage1_findings}\n\n"
        "위 두 자료만 근거로 시스템 프롬프트에 지정된 10개 항목 구조로 "
        "브리핑을 작성해주세요."
    )


def run_briefing(conn: sqlite3.Connection, *, force: bool = False) -> dict[str, Any]:
    """캐시 확인 -> 스냅샷+VXN 판정 -> (1단계 객관적 조회 -> 2단계 해석) -> 저장.

    force=False면 SNAPSHOT_CACHE_SECONDS 이내의 기존 결과를 그대로 재사용한다
    (버튼 연타/실수로 인한 불필요한 LLM 호출 방지 -- 비용 관리).
    """
    if not force:
        latest = analysis_repo.get_latest_result(conn)
        if latest is not None:
            created = datetime.fromisoformat(latest["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age < SNAPSHOT_CACHE_SECONDS:
                return latest

    snapshot = build_macro_snapshot(conn)

    if llm_client.is_dummy_mode():
        # 키가 하나도 없으면 1/2단계로 나눠봐야 둘 다 같은 더미 문구만
        # 반환하므로, 한 번만 호출해서 중복을 피한다.
        response = llm_client.ask(SYSTEM_PROMPT, "dummy", use_web_search=False)
    else:
        stage1 = llm_client.ask(
            STAGE1_SYSTEM_PROMPT, _build_stage1_prompt(),
            use_web_search=True, chain="stage1_search",
        )
        response = llm_client.ask(
            SYSTEM_PROMPT, _build_stage2_prompt(snapshot, stage1.text),
            use_web_search=False, chain="stage2_judgment",
        )

    return analysis_repo.save_result(
        conn,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        result_text=response.text,
        provider=response.provider,
        model=response.model,
    )
