"""backend/atrsite/adapters/fx_rate_client.py -- 외화 환율 자동조회 (2026-08-06 추가).

지금까지 fx_rates는 PUT /api/v1/fx로 사용자가 직접 입력하는 수동값만 있었다
(v1.3 자산 스냅샷 검토 때 확인된 한계 -- 자동 갱신 전혀 없음). 사용자 요청
("환율을 주가갱신 버튼을 누르면 환율도 같이 자동으로 갱신되도록 해")에 따라,
"주가 갱신" 버튼(POST /api/v1/dashboard/refresh-quotes)을 누를 때 시세와 함께
환율도 같이 자동으로 가져온다.

frankfurter.dev(ECB 공식 기준환율 재배포, API 키 불필요)를 쓴다 -- 이 프로젝트
안에서 이미 한 번(2026-08-02, USD/KRW 수동 채우기 때) 실제로 검증된 소스다.
market_index_client.py와 동일 원칙: 실패해도 예외를 절대 던지지 않는다 --
환율 하나 실패했다고 "주가 갱신" 버튼 전체가 막히면 안 되고, 실패 시 기존에
저장된 값을 그대로 두는 게(조용히 값을 지우거나 0으로 덮어쓰는 것보다) 안전하다.

주의: ECB 기준환율이라 은행 매매기준율과는 소폭 다를 수 있고, 유럽 주말/휴일엔
갱신되지 않아 가장 최근 영업일 값을 그대로 준다 -- 은행 앱 환율과 비교했을 때
몇 원 정도 차이가 나는 건 정상이다. worker.py의 5분 자동 폴링에는 연결하지
않는다(ECB 환율은 하루 한 번만 갱신되므로 5분마다 다시 조회할 이유가 없고,
불필요한 외부 호출만 늘어남) -- 사용자가 명시적으로 요청한 범위(수동 "주가
갱신" 버튼)만 반영한다.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger("atrsite.fx_rate")

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"


def fetch_rate_to_krw(currency: str, *, http: Optional[httpx.Client] = None) -> Optional[float]:
    """1 <currency> = ? KRW. 실패하면 예외 대신 None을 반환한다."""
    if currency == "KRW":
        return 1.0

    owns_client = http is None
    client = http or httpx.Client()
    try:
        res = client.get(FRANKFURTER_URL, params={"base": currency, "symbols": "KRW"}, timeout=10)
        res.raise_for_status()
        payload = res.json()
        rate = (payload.get("rates") or {}).get("KRW")
        if rate is None:
            raise ValueError(f"응답에 rates.KRW가 없음: {payload}")
        return float(rate)
    except Exception as exc:  # noqa: BLE001 -- 시세 갱신 흐름 전체가 계속 진행돼야 함
        logger.warning("환율 조회 실패 currency=%s: %s", currency, exc)
        return None
    finally:
        if owns_client:
            client.close()
