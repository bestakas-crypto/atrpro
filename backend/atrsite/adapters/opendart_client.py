# backend/atrsite/adapters/opendart_client.py
# 한국 기업 재무 데이터 -- OpenDART(금융감독원 전자공시). 2026-08-03 실제
# 키로 라이브 검증 완료(삼성전자 005930 기준).
#
# 실제 확인한 응답 구조(공식 문서와 일부 다름, 라이브로 재확인한 내용):
# - corpCode.xml: crtfc_key만 있으면 ZIP(전체 약 11만 개 법인, 3천만자 XML)
#   을 그대로 준다. <list><corp_code>/<corp_name>/<stock_code>/...</list>
# - fnlttSinglAcntAll.json: sj_div(BS/IS/CIS/CF/SCE)별로 계정이 뒤섞여
#   옴. account_id가 IFRS 표준 태그(예: ifrs-full_Revenue)라 회사가
#   바뀌어도 비교적 안정적.
# - **분기 리포트(11012 반기/11013 1분기/11014 3분기)의 손익계산서(IS/CIS)
#   항목은 thstrm_amount가 이미 "그 분기 단독" 값이고, thstrm_add_amount가
#   누적값**(직접 산수로 검증: 3분기 누적 - 반기 누적 = 3분기 thstrm_amount
#   정확히 일치). 반면 **현금흐름표(CF) 항목은 thstrm_amount가 항상
#   "연초부터 그 시점까지 누적"값**이라(thstrm_add_amount 필드 자체가
#   없음) 분기 단독 값을 얻으려면 직접 빼야 한다 -- 연간 영업현금흐름
#   총합과 대조해서 이 해석이 맞다는 것도 확인함(3분기 누적을 그대로
#   "3분기 단독"으로 쓰면 4분기가 음수에 가까워지는 등 앞뒤가 안 맞았음).
# - 대차대조표(BS) 항목은 항상 시점값(그 보고서 기준일 잔액).
from __future__ import annotations

import json
import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

import httpx

from ..config import settings

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIAL_STATEMENT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

REPORT_CODE_Q1 = "11013"
REPORT_CODE_H1 = "11012"   # 반기(1~6월 누적/2분기 단독 -- IS는 분기단독, CF는 누적)
REPORT_CODE_Q3 = "11014"   # 3분기(1~9월 누적/3분기 단독 -- 위와 동일 구분)
REPORT_CODE_ANNUAL = "11011"

_CORP_CODE_CACHE_TTL_SECONDS = 24 * 60 * 60
_corp_code_cache: dict = {"data": None, "fetched_at": 0.0}

# corpCode.xml은 전체 상장/비상장 법인 목록(비압축 약 30MB, 11만여 건)이라
# 매번 새로 받으면 느리다 -- 실제 VPS에서 라이브로 측정한 결과 CPU 시간은
# 2.6초뿐인데 전체 소요시간은 242초(4분)나 걸림, 즉 파싱이 아니라 OpenDART
# 서버<->VPS 회선 자체가 느린 게 원인(2026-08-03 실제 측정 확인). 서버가
# 재시작될 때마다 메모리 캐시(_corp_code_cache)가 비어서 매번 이 4분을
# 다시 겪었고, nginx 기본 타임아웃(60초)보다 길어서 504로 실패했음 --
# 파싱된 결과 자체를 디스크에 남겨서 재시작해도 다시 안 받게 한다.
def _corp_code_disk_cache_path() -> Path:
    return settings.db_path.parent / "opendart_corpcode_cache.json"


def _load_corp_code_cache_from_disk() -> Optional[list[DartCompanyMatch]]:
    path = _corp_code_disk_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - payload.get("fetched_at", 0) > _CORP_CODE_CACHE_TTL_SECONDS:
            return None
        return [DartCompanyMatch(**item) for item in payload["items"]]
    except Exception:
        return None  # 디스크 캐시가 깨져있어도 그냥 다시 받으면 되니 조용히 무시


def _save_corp_code_cache_to_disk(items: list[DartCompanyMatch], *, fetched_at: float) -> None:
    path = _corp_code_disk_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": fetched_at,
            "items": [
                {"corp_code": m.corp_code, "corp_name": m.corp_name, "stock_code": m.stock_code} for m in items
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 캐시 저장 실패는 치명적이지 않음(다음에 다시 받으면 됨)

# metric_key -> account_id. IS/CIS는 분기단독 값을 그대로 쓰고, CF는
# 누적값이라 정규화 단계(normalize_periods)에서 별도로 뺄셈 처리한다.
IS_TAGS = {
    "revenue": "ifrs-full_Revenue",
    "gross_profit": "ifrs-full_GrossProfit",
    "operating_income": "dart_OperatingIncomeLoss",
    "net_income": "ifrs-full_ProfitLoss",
    "eps_diluted": "ifrs-full_DilutedEarningsLossPerShare",
}
BS_TAGS = {
    "cash": "ifrs-full_CashAndCashEquivalents",
    "receivables": "ifrs-full_CurrentTradeReceivables",
    "inventory": "ifrs-full_Inventories",
}
CF_TAGS = {
    "operating_cash_flow": "ifrs-full_CashFlowsFromUsedInOperatingActivities",
    "capex": "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
}
# total_debt: 회사마다 차입금 계정 구성이 크게 달라(SEC의 LongTermDebt처럼
# 표준화된 단일 태그가 없음) 이번 버전에서는 매핑하지 않는다 -- 임의로
# 여러 계정을 더해 추정하지 않고 INSUFFICIENT_DATA로 남긴다(원칙 준수).


class OpenDartError(RuntimeError):
    """OpenDART 호출/파싱 실패."""


def is_configured() -> bool:
    return bool(settings.opendart_api_key)


@dataclass(frozen=True)
class DartCompanyMatch:
    corp_code: str
    corp_name: str
    stock_code: Optional[str]


@dataclass(frozen=True)
class NormalizedPeriod:
    fiscal_year: int
    fiscal_period: str
    period_type: str
    period_end: str
    filed_at: Optional[str]
    accession_no: Optional[str]
    metrics: dict


def _headers_note() -> dict:
    return {}


def _fetch_corp_code_list(*, http: Optional[httpx.Client] = None) -> list[DartCompanyMatch]:
    owns_client = http is None
    client = http or httpx.Client()
    try:
        try:
            # 실제 라이브 측정 결과 전체 다운로드에 최대 4분(242초)까지 걸림
            # (CPU는 2.6초뿐 -- OpenDART<->VPS 회선 자체가 느린 게 원인,
            # 2026-08-03 확인). 기존 30초는 우연히 안 걸렸을 뿐 언제든
            # 실패할 수 있어서 여유있게 늘림.
            res = client.get(CORP_CODE_URL, params={"crtfc_key": settings.opendart_api_key}, timeout=300)
            res.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenDartError(f"OpenDART 회사코드 조회 실패: {exc}") from exc

        try:
            with zipfile.ZipFile(BytesIO(res.content)) as zf:
                xml_bytes = zf.read("CORPCODE.xml")
        except zipfile.BadZipFile as exc:
            raise OpenDartError(f"OpenDART 회사코드 응답이 ZIP이 아님(키 오류 가능): {exc}") from exc

        root = ElementTree.fromstring(xml_bytes)
        matches = []
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            if not stock_code:
                continue  # 비상장/코드 없는 법인은 검색 대상에서 제외(투자 가능 종목만)
            matches.append(DartCompanyMatch(
                corp_code=item.findtext("corp_code"), corp_name=item.findtext("corp_name"),
                stock_code=stock_code,
            ))
        return matches
    finally:
        if owns_client:
            client.close()


def _ensure_corp_code_cache(*, http: Optional[httpx.Client] = None) -> list[DartCompanyMatch]:
    """메모리 캐시 -> 디스크 캐시 -> (둘 다 없거나 오래됐으면) 실제 조회
    순서로 채운다. 서버가 방금 재시작돼서 메모리 캐시가 비어있어도, 디스크
    캐시가 24시간 이내라면 4분짜리 재다운로드 없이 즉시 응답 가능."""
    now = time.time()
    if _corp_code_cache["data"] is not None and now - _corp_code_cache["fetched_at"] <= _CORP_CODE_CACHE_TTL_SECONDS:
        return _corp_code_cache["data"]

    from_disk = _load_corp_code_cache_from_disk()
    if from_disk is not None:
        _corp_code_cache["data"] = from_disk
        _corp_code_cache["fetched_at"] = now
        return from_disk

    fetched = _fetch_corp_code_list(http=http)
    _corp_code_cache["data"] = fetched
    _corp_code_cache["fetched_at"] = now
    _save_corp_code_cache_to_disk(fetched, fetched_at=now)
    return fetched


def warm_corp_code_cache() -> None:
    """서버 시작 시 백그라운드로 미리 불러둔다(api/main.py의 lifespan에서
    fire-and-forget으로 호출) -- 첫 사용자가 4분을 기다리지 않게 하는 게
    목적이라, 실패해도 조용히 넘어간다(다음 실제 검색 때 다시 시도됨)."""
    if not is_configured():
        return
    try:
        _ensure_corp_code_cache()
    except OpenDartError:
        pass


def search_companies(query: str, *, http: Optional[httpx.Client] = None, limit: int = 10) -> list[DartCompanyMatch]:
    if not is_configured():
        return []
    all_items = _ensure_corp_code_cache(http=http)

    q = query.strip()
    if not q:
        return []

    exact_code = [it for it in all_items if it.stock_code == q]
    name_match = [it for it in all_items if q in it.corp_name and it not in exact_code]
    ordered = exact_code + name_match
    return ordered[:limit]


def fetch_financial_statements(
    corp_code: str, *, bsns_year: str, reprt_code: str, fs_div: str = "CFS", http: Optional[httpx.Client] = None,
) -> dict:
    if not is_configured():
        raise OpenDartError("OpenDART API 키가 설정되지 않았습니다(더미 모드).")

    owns_client = http is None
    client = http or httpx.Client()
    try:
        params = {
            "crtfc_key": settings.opendart_api_key, "corp_code": corp_code,
            "bsns_year": bsns_year, "reprt_code": reprt_code, "fs_div": fs_div,
        }
        try:
            res = client.get(FINANCIAL_STATEMENT_URL, params=params, timeout=20)
            res.raise_for_status()
            payload = res.json()
        except httpx.HTTPError as exc:
            raise OpenDartError(f"OpenDART 호출 실패: {exc}") from exc

        # status 코드: 000=정상, 013=조회된 데이터 없음(예: 아직 그 분기 공시
        # 전), 020=사용한도초과 등. 013은 "실패"가 아니라 "그 분기엔 자료
        # 없음"이라 호출자가 구분해서 처리한다(예외로 던지되 status를 실어줌).
        if payload.get("status") != "000":
            raise OpenDartError(f"OpenDART 응답(status={payload.get('status')}): {payload.get('message')}")
        return payload
    finally:
        if owns_client:
            client.close()


def _extract(rows: list[dict], sj_divs: tuple[str, ...], account_id: str, field: str = "thstrm_amount") -> Optional[float]:
    for row in rows:
        if row.get("sj_div") in sj_divs and row.get("account_id") == account_id:
            raw = row.get(field)
            if raw in (None, "", "-"):
                return None
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def _extract_report_metrics(rows: list[dict]) -> tuple[dict[str, Optional[float]], dict[str, Optional[float]]]:
    """한 보고서 응답에서 IS(분기단독 그대로, thstrm_amount)/BS(시점값
    그대로)/CF(★ 누적값, 호출자가 구간 뺄셈 처리)를 뽑는다. CF는 별도
    딕셔너리로 반환해서 normalize_periods가 뺄셈할 수 있게 한다."""
    metrics = {}
    for key, tag in IS_TAGS.items():
        metrics[key] = _extract(rows, ("IS", "CIS"), tag)
    for key, tag in BS_TAGS.items():
        metrics[key] = _extract(rows, ("BS",), tag)
    cf_cumulative = {key: _extract(rows, ("CF",), tag) for key, tag in CF_TAGS.items()}
    return metrics, cf_cumulative


def _extract_is_cumulative(rows: list[dict]) -> dict[str, Optional[float]]:
    """반기/3분기 보고서의 thstrm_add_amount(연초부터 누적) -- 4분기 단독
    값을 "연간 총합 - 3분기 누적"으로 구하기 위해 필요(2026-08-03 실제
    검증 중 발견: 4분기를 연간 보고서의 thstrm_amount로 그대로 쓰면 그건
    연간 총합이지 4분기 단독이 아니라서 완전히 틀린 값이 나옴)."""
    return {key: _extract(rows, ("IS", "CIS"), tag, field="thstrm_add_amount") for key, tag in IS_TAGS.items()}


def _subtract(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    return current - previous


def normalize_periods(
    corp_code: str, *, period_type: str, years: list[int], fs_div: str = "CFS", http: Optional[httpx.Client] = None,
) -> list[NormalizedPeriod]:
    """지정한 연도들의 분기(4개/년) 또는 연간(1개/년) 재무를 표준화한다.
    과거 -> 최신 순으로 반환(SEC 어댑터와 동일한 규약)."""
    owns_client = http is None
    client = http or httpx.Client()
    try:
        periods: list[NormalizedPeriod] = []
        for year in sorted(years):
            if period_type == "ANNUAL":
                try:
                    payload = fetch_financial_statements(
                        corp_code, bsns_year=str(year), reprt_code=REPORT_CODE_ANNUAL, fs_div=fs_div, http=client,
                    )
                except OpenDartError:
                    continue
                metrics, cf = _extract_report_metrics(payload.get("list", []))
                metrics.update(cf)  # 연간 보고서는 CF도 이미 연간 총합이라 그대로 씀(뺄셈 불필요)
                periods.append(NormalizedPeriod(
                    fiscal_year=year, fiscal_period="FY", period_type="ANNUAL",
                    period_end=f"{year}-12-31", filed_at=None,
                    accession_no=(payload.get("list") or [{}])[0].get("rcept_no"),
                    metrics=metrics,
                ))
                continue

            # QUARTER: Q1/H1/3Q/FY 4개 보고서를 전부 받아서 CF만 구간 뺄셈.
            reports = {}
            for code in (REPORT_CODE_Q1, REPORT_CODE_H1, REPORT_CODE_Q3, REPORT_CODE_ANNUAL):
                try:
                    reports[code] = fetch_financial_statements(
                        corp_code, bsns_year=str(year), reprt_code=code, fs_div=fs_div, http=client,
                    )
                except OpenDartError:
                    reports[code] = None

            parsed = {}
            for code, payload in reports.items():
                if payload is None:
                    parsed[code] = ({}, {})
                else:
                    parsed[code] = _extract_report_metrics(payload.get("list", []))

            is_bs_q1, cf_q1 = parsed[REPORT_CODE_Q1]
            is_bs_h1, cf_h1_cum = parsed[REPORT_CODE_H1]
            is_bs_q3, cf_9mo_cum = parsed[REPORT_CODE_Q3]
            is_bs_fy, cf_fy = parsed[REPORT_CODE_ANNUAL]

            # 4분기 단독 IS는 DART가 직접 안 줌(Q1/반기/3분기 보고서만 있고
            # "4분기" 보고서 자체가 없음) -- 연간 총합(is_bs_fy)에서 3분기
            # 누적(9개월치, thstrm_add_amount)을 빼서 구한다. BS(시점값)는
            # 뺄셈 대상이 아니라 연말 시점 그대로(is_bs_fy) 쓴다.
            q3_payload = reports.get(REPORT_CODE_Q3)
            is_cum_9mo = _extract_is_cumulative(q3_payload.get("list", [])) if q3_payload else {}
            is_q4 = {
                **is_bs_fy,
                **{key: _subtract(is_bs_fy.get(key), is_cum_9mo.get(key)) for key in IS_TAGS},
            }

            quarter_defs = [
                ("Q1", f"{year}-03-31", is_bs_q1, cf_q1, REPORT_CODE_Q1),
                ("Q2", f"{year}-06-30", is_bs_h1, _subtract_cf(cf_h1_cum, cf_q1), REPORT_CODE_H1),
                ("Q3", f"{year}-09-30", is_bs_q3, _subtract_cf(cf_9mo_cum, cf_h1_cum), REPORT_CODE_Q3),
                ("Q4", f"{year}-12-31", is_q4, _subtract_cf(cf_fy, cf_9mo_cum), REPORT_CODE_ANNUAL),
            ]
            for fiscal_period, period_end, is_bs, cf, source_code in quarter_defs:
                metrics = {**is_bs, **cf}
                if not any(v is not None for v in metrics.values()):
                    # 그 분기 보고서 자체가 아직 없으면(미래 분기 등) 건너뜀.
                    # cf는 _subtract_cf()가 항상 CF_TAGS 전체 키를 채운
                    # dict(값이 전부 None이라도)를 돌려주므로 "dict가
                    # 비어있는가"로는 판정이 안 되고, 실제 값이 하나라도
                    # 있는지(None이 아닌지)로 판정해야 한다(2026-08-03 실제
                    # 삼성전자로 라이브 검증 중 발견 -- 미래 분기가 전부
                    # None인 채로 그대로 끼어들어 "한눈에 보기"가 항상
                    # 자료부족으로만 나오던 버그).
                    continue
                payload = reports.get(source_code)
                accn = (payload.get("list") or [{}])[0].get("rcept_no") if payload else None
                periods.append(NormalizedPeriod(
                    fiscal_year=year, fiscal_period=fiscal_period, period_type="QUARTER",
                    period_end=period_end, filed_at=None, accession_no=accn, metrics=metrics,
                ))
        return periods
    finally:
        if owns_client:
            client.close()


def _subtract_cf(cumulative: dict, previous_cumulative: dict) -> dict:
    return {key: _subtract(cumulative.get(key), previous_cumulative.get(key)) for key in CF_TAGS}
