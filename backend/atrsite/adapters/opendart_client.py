# backend/atrsite/adapters/opendart_client.py
# 한국 기업 재무 데이터 -- OpenDART(금융감독원 전자공시). 회원가입 후
# 발급받는 API 키가 필요하다(2026-08-03 기준 아직 발급 안 됨).
#
# 키가 없으면 회사코드 매핑 파일(corpCode.xml)조차 받아올 수 없어서
# 검색/식별 단계부터 막힌다 -- kis_client.py/telegram_client.py와 동일한
# 원칙으로 키 없을 때는 명확히 UNAVAILABLE을 반환하는 더미 모드로 동작한다.
#
# 아래 엔드포인트/파라미터/응답 필드명은 OpenDART 공식 API 문서 기준으로
# 작성했지만, 실제 키로 라이브 검증은 아직 못 했다(2026-08-03) -- 키를
# 받으면 반드시 먼저 라이브로 응답 형태를 재확인할 것. SEC EDGAR 어댑터
# 작업 중 "필드가 문서와 실제 데이터에서 미묘하게 다를 수 있다"는 걸
# 실제로 겪었으므로(비교 컬럼 fy/fp 라벨 문제), 이 어댑터도 같은 종류의
# 검증 없이는 신뢰하면 안 된다.
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import settings

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIAL_STATEMENT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

# 분기보고서 코드(문서 기준) -- 11011=사업보고서(연간), 11012=반기,
# 11013=1분기, 11014=3분기.
REPORT_CODE_ANNUAL = "11011"
REPORT_CODE_Q1 = "11013"
REPORT_CODE_Q3 = "11014"


class OpenDartError(RuntimeError):
    """OpenDART 호출/파싱 실패."""


def is_configured() -> bool:
    return bool(settings.opendart_api_key)


@dataclass(frozen=True)
class DartCompanyMatch:
    corp_code: str
    corp_name: str
    stock_code: Optional[str]


def search_companies(query: str, *, http: Optional[httpx.Client] = None, limit: int = 10) -> list[DartCompanyMatch]:
    """corpCode.xml(전체 상장/비상장 법인 목록 ZIP)을 내려받아 파싱 --
    OPENDART_API_KEY가 없으면 아예 시도하지 않고 빈 목록을 반환한다."""
    if not is_configured():
        return []

    # TODO(2026-08-03, 키 받으면 구현): corpCode.xml은 ZIP 파일이라
    # zipfile+xml.etree로 풀어서 CORPCODE.xml 안의 <list><corp_code>/
    # <corp_name>/<stock_code></list> 목록을 파싱해야 한다. 파일이 꽤
    # 커서(전체 상장사, 수만 건) SEC 어댑터의 company_tickers.json처럼
    # 24시간 캐시가 필요할 것. 지금은 키가 없어서 실제 응답 구조를 못
    # 봤으니 여기서 추측으로 구현하지 않는다 -- 빈 목록 반환.
    raise OpenDartError(
        "OpenDART API 키가 없어서 회사코드 검색을 구현/검증하지 못했습니다. "
        "OPENDART_API_KEY를 .env에 채운 뒤 다시 시도하세요."
    )


def fetch_financial_statements(
    corp_code: str, *, bsns_year: str, reprt_code: str, fs_div: str = "CFS", http: Optional[httpx.Client] = None,
) -> dict:
    """단일회사 전체 재무제표 조회. fs_div: CFS(연결, 우선) / OFS(별도).

    미검증 상태 -- 키가 생기면 반드시 삼성전자(corp_code는 DART 문서/
    corpCode.xml에서 확인) 등 실제 종목으로 먼저 라이브 검증할 것."""
    if not is_configured():
        raise OpenDartError("OpenDART API 키가 설정되지 않았습니다(더미 모드).")

    owns_client = http is None
    client = http or httpx.Client()
    try:
        params = {
            "crtfc_key": settings.opendart_api_key,
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }
        try:
            res = client.get(FINANCIAL_STATEMENT_URL, params=params, timeout=20)
            res.raise_for_status()
            payload = res.json()
        except httpx.HTTPError as exc:
            raise OpenDartError(f"OpenDART 호출 실패: {exc}") from exc

        if payload.get("status") != "000":
            # 문서 기준 status 코드: 000=정상, 013=조회된 데이터 없음, 020=사용한도초과 등.
            raise OpenDartError(f"OpenDART 응답 오류(status={payload.get('status')}): {payload.get('message')}")
        return payload
    finally:
        if owns_client:
            client.close()
