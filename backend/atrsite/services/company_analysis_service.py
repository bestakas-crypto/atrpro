# backend/atrsite/services/company_analysis_service.py
# 종목탐구 오케스트레이션 -- analysis_service.py(매크로 브리핑)와 같은 설계
# 원칙: Python이 재무지표/경고를 전부 확정하고, LLM은 설명만 담당한다.
# 스펙 12절 "요청ID 먼저 반환 -> 상태 폴링" 패턴 -- api/company.py가
# BackgroundTasks로 execute_pipeline()을 백그라운드 실행하고, 이 서비스는
# 단계마다 company_analysis_requests.status를 갱신해서 폴링 가능하게 한다.
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from ..adapters import llm_client
from ..adapters import opendart_client
from ..adapters import sec_edgar_client
from ..repositories import analysis as macro_analysis_repo
from ..repositories import companies as companies_repo
from ..repositories import company_analysis as company_analysis_repo
from . import company_metrics_engine as engine

QUARTER_LIMIT = 8
ANNUAL_LIMIT = 4


class CompanyAnalysisError(RuntimeError):
    """분석 파이프라인 실패 -- 사용자에게 그대로 보여줄 수 있는 메시지."""


def _guess_security_type(title: str) -> str:
    upper = title.upper()
    if any(kw in upper for kw in (" TRUST", " FUND", " ETF", "SERIES 1")):
        return "ETF"
    return "STOCK"


def resolve_company_from_us_match(conn: sqlite3.Connection, *, cik: int, ticker: str, title: str) -> dict[str, Any]:
    """스펙 4.2 "이 종목 분석" 확인 단계 -- SEC 검색 결과 하나를 실제
    companies 행으로 만들거나(이미 있으면) 재사용한다."""
    security_type = _guess_security_type(title)
    industry_template = None if security_type == "ETF" else engine.detect_industry_template(ticker)
    company = companies_repo.create_company(
        conn, country="US", security_type=security_type, name=title, currency="USD",
        primary_ticker=ticker, exchange=None, sec_cik=f"{cik:010d}", industry_template=industry_template,
    )
    return company


def resolve_company_from_kr_match(
    conn: sqlite3.Connection, *, corp_code: str, corp_name: str, stock_code: str,
) -> dict[str, Any]:
    """스펙 4.2 확인 단계의 한국 기업 버전. DART는 검색 결과 자체에 ETF
    여부를 안 줘서(상장 종목 목록에 ETF도 섞여 나옴), 종목명에 흔한 ETF
    표기가 있으면 임시로 ETF로 분류한다(SEC 쪽 _guess_security_type과
    같은 수준의 휴리스틱 -- 완벽하지 않음, 잘못 분류돼도 "분석 불가"
    안내만 나올 뿐 위험하지 않음)."""
    security_type = "ETF" if any(kw in corp_name.upper() for kw in ("ETF", "KODEX", "TIGER", "KBSTAR", "ARIRANG")) else "STOCK"
    company = companies_repo.create_company(
        conn, country="KR", security_type=security_type, name=corp_name, currency="KRW",
        primary_ticker=stock_code, exchange="KRX", dart_corp_code=corp_code, industry_template=None,
    )
    return company


def search_companies(query: str) -> dict[str, Any]:
    """미국은 항상 시도(키 불필요), 한국은 OpenDART 키가 있을 때만 -- 스펙
    4.1 "한국/미국 종목 모두 지원"의 검색 단계."""
    us_matches = sec_edgar_client.search_companies(query)
    results = [
        {"country": "US", "ticker": m.ticker, "name": m.title, "cik": m.cik}
        for m in us_matches
    ]
    notices = []
    if opendart_client.is_configured():
        try:
            kr_matches = opendart_client.search_companies(query)
            results = [
                {"country": "KR", "corp_code": m.corp_code, "name": m.corp_name, "stock_code": m.stock_code}
                for m in kr_matches
            ] + results
        except opendart_client.OpenDartError as exc:
            notices.append(str(exc))
    else:
        notices.append("OpenDART API 키가 없어 한국 기업 검색은 아직 지원되지 않습니다.")
    return {"results": results, "notices": notices}


def _collect_us_periods(company: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    facts = sec_edgar_client.fetch_company_facts(int(company["sec_cik"]))
    quarterly = sec_edgar_client.normalize_periods(facts, period_type="QUARTER", limit=QUARTER_LIMIT)
    annual = sec_edgar_client.normalize_periods(facts, period_type="ANNUAL", limit=ANNUAL_LIMIT)
    to_dict = lambda p: {  # noqa: E731
        "period_type": p.period_type, "period_end": p.period_end, "fiscal_year": p.fiscal_year,
        "fiscal_period": p.fiscal_period, "filed_at": p.filed_at, "accession_no": p.accession_no,
        "metrics": p.metrics,
    }
    return [to_dict(p) for p in quarterly], [to_dict(p) for p in annual]


def _persist_periods(conn: sqlite3.Connection, company_id: str, annotated: list[dict], *, source: str) -> None:
    for period in annotated:
        period_id = companies_repo.upsert_financial_period(
            conn, company_id=company_id, fiscal_year=period["fiscal_year"],
            fiscal_period=period["fiscal_period"], period_type=period["period_type"],
            period_end=period["period_end"], filed_at=period.get("filed_at"), source=source,
        )
        for metric_key, value in period["metrics"].items():
            comparison = period.get("comparisons", {}).get(metric_key)
            verdict = comparison["yoy_verdict"] if comparison else None
            companies_repo.upsert_financial_metric(
                conn, period_id=period_id, metric_key=metric_key, value=value, verdict=verdict,
            )
        if period.get("accession_no"):
            companies_repo.record_filing(
                conn, company_id=company_id, source=source,
                filing_type="10-Q" if period["period_type"] == "QUARTER" else "10-K",
                filed_at=period.get("filed_at"), period_end=period["period_end"],
                accession_or_receipt_no=period["accession_no"],
                url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&filenum={period['accession_no']}"
                if source == "SEC" else None,
            )


# ---------------------------------------------------------------------------
# LLM 프롬프트 -- 스펙 9절. Python이 만든 스냅샷(재무지표+경고+출처)만 근거로
# 쓰게 하고, 검색이 필요한 뉴스/공시 맥락은 1단계(stage1_search)에, 서술/
# 시나리오는 2단계(stage2_judgment)에 맡긴다 -- analysis_service.py(매크로)와
# 동일한 체인을 그대로 재사용(스펙 지시: "중복되는 LLM 클라이언트는 새로
# 만들지 말고 재사용"). 실제 공급자 우선순위는 llm_client.py의
# _FALLBACK_CHAINS 참고(2026-08-04부터 GPT 우선, 제미나이는 제외).
# ---------------------------------------------------------------------------
STAGE1_SYSTEM_PROMPT = """\
당신은 특정 기업에 대한 객관적 정보 조회 담당입니다. 해석하거나 투자
의견을 내지 마십시오.

1. 웹서치로 이 기업의 최신 공시/실적발표/뉴스를 찾아 사실만 보고하십시오.
   모르면 "확인 안 됨"이라고 쓰고 지어내지 마십시오.
2. 각 항목마다 날짜와 출처를 함께 쓰십시오.
3. 시장 컨센서스(애널리스트 목표주가 등)는 신뢰 가능한 출처가 있을 때만
   "시장 예상치"라고 명시하고, 없으면 "시장 예상치 자료 없음"이라고
   쓰십시오. 뉴스 기사 속 추정치를 공식 컨센서스처럼 쓰지 마십시오.
4. 의견/전망/투자판단을 포함하지 마십시오 -- 다음 단계에서 다른 모델이
   해석에 사용합니다.
"""


def _build_stage1_prompt(company: dict[str, Any]) -> str:
    industry_line = ""
    if company.get("industry_template") == "semiconductor":
        industry_line = (
            "\n반도체 업종 특성상 다음도 함께 찾아주세요: DRAM/NAND 가격(ASP) 추이, "
            "AI 관련 CAPEX/수요 영향, HBM 비중 관련 최근 언급."
        )
    return (
        f"기업: {company['name']} ({company['primary_ticker']}, {company['country']})\n\n"
        f"다음을 웹서치로 조회해서 사실만 보고해주세요:\n"
        f"- 최근 분기 실적발표(어닝콜) 핵심 내용과 가이던스\n"
        f"- 최근 1~2주 이내 주요 뉴스/공시(최대 5건, 제목/출처/날짜/핵심사실)\n"
        f"- 애널리스트 목표주가/투자의견 컨센서스(있으면 출처와 함께, 없으면 명시)\n"
        f"- 향후 확인해야 할 일정(다음 실적발표일 등, 아는 범위 내에서만)"
        f"{industry_line}"
    )


SYSTEM_PROMPT = """\
당신은 개인 투자자를 위한 종목탐구 분석 보조원입니다. 반드시 지키세요.

[말투 -- 반드시 지킬 것]
친한 언니가 재무제표를 하나도 모르는 완전 초보 후배에게 설명해주는
말투로 쓰십시오. 친근하고 다정하게, "~해!", "~거든", "~야" 같은 구어체를
쓰고 이모지를 적절히 섞으십시오. 전문용어(영업이익률, FCF, 이자보상배율
등)가 나오면 반드시 괄호로 아주 쉬운 말로 풀어주십시오(예: "영업이익률
(회사가 물건 팔아서 진짜 남긴 돈의 비율)"). 딱딱한 보고서 말투는 금지
-- 숫자와 판정은 정확하게, 설명은 쉽게, 이 두 가지를 동시에 하십시오.
글 맨 앞에 짧고 밝은 인사와 오늘 무엇을 볼 건지 한 줄로 먼저 알려주고,
글 맨 끝에는 언니의 총평과 "더 궁금한 거 있으면 편하게 물어봐~" 같은
마무리 인사를 붙이십시오.

[숫자·판정 관련 절대 규칙 -- 말투를 쉽게 바꾸는 것과 무관하게 반드시 지킬 것]
1. "재무 스냅샷"(Python이 계산/확정한 지표와 판정)과 "1단계 객관적 조회
   결과"만 근거로 삼으십시오. 제공되지 않은 수치를 스스로 알고 있다고
   가정해서 말하지 마십시오. 모르는 값은 "확인 안 됨"이라고 쉽게
   풀어서라도 반드시 언급하십시오(그냥 빼먹지 마십시오).
2. 재무 스냅샷의 지표 값과 IMPROVING/STABLE/DETERIORATING 같은 판정은
   Python이 이미 계산/확정한 결과입니다. 말투는 쉽게 풀어써도 되지만,
   숫자 자체를 반올림하거나 판정을 다르게 바꾸지 마십시오(예:
   DETERIORATING을 "그냥 나쁘지 않아"처럼 얼버무리지 말고, "안 좋아진
   부분이야"처럼 쉽게 말하되 방향은 정확히 전달하십시오).
3. 이 프로그램은 절대 자동으로 주문을 넣지 않습니다. 매수/매도를 단정적
   으로 지시하지 말고, "이건 참고만 하고 진짜 사고파는 건 네가 직접
   정해야 해!"처럼 참고용임을 분명히 하십시오.
4. 목표주가를 임의로 만들지 마십시오. 1단계 결과에 출처 있는 컨센서스가
   없으면 "목표주가는 확인된 자료가 없어"라고 쓰십시오.

[구조 -- 아래 20개 내용을 전부 다루되, 번호 대신 이모지+친근한 소제목을
붙이십시오(예: "1) 기업 확인" 대신 "🏢 이 회사 뭐 하는 곳이야?")]
   1) 기업 확인 (이름/티커/거래소/통화)
   2) 분석 기준일 (재무 스냅샷의 가장 최근 분기 period_end 날짜를 쓰십시오
      -- 1단계에서 찾은 뉴스 날짜와 혼동하지 마십시오)
   3) 한 줄 판단
   4) 핵심 수치 (재무 스냅샷에서 그대로, 출처/기준일 함께)
   5) 좋아진 점
   6) 나빠진 점
   7) 매출 성장의 질
   8) 마진 (매출총이익률/영업이익률/순이익률)
   9) 현금흐름과 이익의 질
   10) 재무건전성 (부채/이자보상배율/순차입금)
   11) 업종 핵심지표 (자료 있으면, 없으면 "자료 부족"이라고 명시)
   12) 최신 공시·뉴스 (1단계 결과 기반, 출처 명시)
   13) 관련 매크로 (제공됐으면만, 없으면 생략)
   14) 밸류에이션 (제공된 자료 범위 내에서만)
   15) 단기(1~4주)·중기(3~12개월)·장기(1~3년) 방향 (재무방향/시장기대/
       주가방향을 분리해서 설명)
   16) 상승·기본·하락 시나리오
   17) 각 시나리오를 무효화하는 조건
   18) 다음 확인 일정
   19) 자료 부족·불확실한 항목 (INSUFFICIENT_DATA로 표시된 것들 정리)
   20) 출처 (1단계 조회 결과의 출처 요약 + 재무 데이터 출처)
전체 한국어로 쓰십시오.
"""


def _build_stage2_prompt(company: dict, snapshot: dict, stage1_findings: str) -> str:
    return (
        f"[재무 스냅샷 -- Python이 직접 계산/확정함(JSON)]\n\n"
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n\n"
        f"[1단계 객관적 조회 결과]\n\n{stage1_findings}\n\n"
        f"위 두 자료만 근거로 {company['name']}({company['primary_ticker']})에 대해 "
        f"시스템 프롬프트에 지정된 20개 항목 구조로 분석을 작성해주세요."
    )


def _collect_kr_periods(company: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    if not opendart_client.is_configured():
        raise CompanyAnalysisError("OpenDART API 키가 설정되지 않았습니다.")
    corp_code = company["dart_corp_code"]
    from datetime import date
    this_year = date.today().year
    # 분기는 최근 2개년(최대 8개 분기), 연간은 최근 4개년 -- SEC 쪽과 같은 범위.
    quarterly_years = [this_year - 1, this_year]
    annual_years = [this_year - i for i in range(ANNUAL_LIMIT)]

    quarterly = opendart_client.normalize_periods(corp_code, period_type="QUARTER", years=quarterly_years)
    annual = opendart_client.normalize_periods(corp_code, period_type="ANNUAL", years=annual_years)
    to_dict = lambda p: {  # noqa: E731
        "period_type": p.period_type, "period_end": p.period_end, "fiscal_year": p.fiscal_year,
        "fiscal_period": p.fiscal_period, "filed_at": p.filed_at, "accession_no": p.accession_no,
        "metrics": p.metrics,
    }
    quarterly = [to_dict(p) for p in quarterly][-QUARTER_LIMIT:]
    annual = [to_dict(p) for p in annual]
    return quarterly, annual


def build_snapshot(conn: sqlite3.Connection, company: dict[str, Any]) -> dict[str, Any]:
    if company["security_type"] == "ETF":
        raise CompanyAnalysisError("ETF 분석 모드는 이번 버전에서 아직 지원하지 않습니다(기업 재무제표 분석 대상 아님).")

    if company["country"] == "US":
        quarterly, annual = _collect_us_periods(company)
        data_source = "SEC"
    elif company["country"] == "KR":
        quarterly, annual = _collect_kr_periods(company)
        data_source = "DART"
    else:
        raise CompanyAnalysisError(f"지원하지 않는 국가입니다: {company['country']}")

    if not quarterly:
        raise CompanyAnalysisError(f"{data_source}에서 이 기업의 분기 재무 데이터를 찾지 못했습니다.")

    industry_template = company.get("industry_template")
    annotated_quarterly = engine.annotate_periods(quarterly, industry_template=industry_template)
    annotated_annual = engine.annotate_periods(annual, industry_template=industry_template) if annual else []
    warnings = engine.check_warning_rules(annotated_quarterly)

    _persist_periods(conn, company["id"], annotated_quarterly, source=data_source)
    if annotated_annual:
        _persist_periods(conn, company["id"], annotated_annual, source=data_source)

    macro_context = None
    if industry_template == "semiconductor":
        latest_macro = macro_analysis_repo.get_latest_result(conn)
        if latest_macro:
            macro_context = {"note": "가장 최근 매크로 브리핑에서 VXN/반도체 관련 부분만 참고용",
                              "macro_result_created_at": latest_macro["created_at"]}

    return {
        "company": {
            "name": company["name"], "ticker": company["primary_ticker"],
            "country": company["country"], "currency": company["currency"],
            "industry_template": industry_template,
        },
        "quarterly_periods": [
            {"period_end": p["period_end"], "fiscal_year": p["fiscal_year"], "fiscal_period": p["fiscal_period"],
             "metrics": p["metrics"], "comparisons": p["comparisons"]}
            for p in annotated_quarterly
        ],
        "annual_periods": [
            {"period_end": p["period_end"], "fiscal_year": p["fiscal_year"], "fiscal_period": p["fiscal_period"],
             "metrics": p["metrics"]}
            for p in annotated_annual
        ],
        "warnings": warnings,
        "macro_context": macro_context,
        "source": "SEC EDGAR (companyfacts XBRL)" if data_source == "SEC" else "OpenDART (전자공시)",
    }


def execute_pipeline(conn: sqlite3.Connection, *, request_id: str, company_id: str) -> None:
    """스펙 12절 상태 전이대로 진행. 각 단계마다 commit해서 진행 중에도
    폴링 응답에 최신 상태가 보이게 한다. 실패하면 FAILED + 에러메시지."""
    try:
        company_analysis_repo.update_request_status(conn, request_id, "COLLECTING_DATA")
        conn.commit()
        company = companies_repo.get_company(conn, company_id)
        if company is None:
            raise CompanyAnalysisError("회사 정보를 찾을 수 없습니다.")

        company_analysis_repo.update_request_status(conn, request_id, "COMPUTING_METRICS")
        conn.commit()
        snapshot = build_snapshot(conn, company)
        conn.commit()

        if llm_client.is_dummy_mode():
            response = llm_client.ask(SYSTEM_PROMPT, "dummy", use_web_search=False)
        else:
            company_analysis_repo.update_request_status(conn, request_id, "SEARCHING_NEWS")
            conn.commit()
            stage1 = llm_client.ask(
                STAGE1_SYSTEM_PROMPT, _build_stage1_prompt(company),
                use_web_search=True, chain="stage1_search",
            )

            company_analysis_repo.update_request_status(conn, request_id, "ANALYZING")
            conn.commit()
            # 20항목 구조라 매크로 브리핑(10항목)보다 응답이 훨씬 길다 -- 기본
            # MAX_TOKENS(2000, 웹서치 없는 호출 기준)로는 부족해서 실제로
            # 3줄 만에 잘리는 걸 라이브 테스트에서 확인함(2026-08-03). 웹서치
            # 호출용 예산(WEB_SEARCH_MAX_TOKENS)과 같은 8000으로 명시.
            response = llm_client.ask(
                SYSTEM_PROMPT, _build_stage2_prompt(company, snapshot, stage1.text),
                use_web_search=False, chain="stage2_judgment", max_tokens=8000,
            )

        company_analysis_repo.save_result(
            conn, request_id=request_id, company_id=company_id,
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            result_text=response.text, provider=response.provider, model=response.model,
        )
        company_analysis_repo.update_request_status(conn, request_id, "COMPLETED")
        conn.commit()
    except Exception as exc:  # noqa: BLE001 -- 실패 상태로 명확히 기록하고 재전파하지 않음
        company_analysis_repo.update_request_status(conn, request_id, "FAILED", error_message=str(exc))
        conn.commit()
