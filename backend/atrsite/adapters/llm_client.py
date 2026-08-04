"""LLM 매크로 브리핑용 클라이언트 -- 2026-08-03 추가.

C:\\mmean\\llm\\llm_chain.py의 폴백/재시도/일일한도 패턴을 참고해서 새로
작성했다. mmean 코드를 직접 import하지 않고, API 키/로그/DB도 완전히
독립적으로 관리한다(사용자 지시: "두 프로젝트는 계속 독립적으로 유지").

핵심 원칙(사용자 지시, 2026-08-03): LLM은 수치를 스스로 찾거나 추측하면 안
된다. 여기서 만드는 클라이언트는 순수하게 "프롬프트를 보내고 텍스트를
받는" 역할만 하고, 수치 검증/조합은 이 모듈을 호출하는 쪽(analysis_service)
책임이다.

단계별 우선순위는 _FALLBACK_CHAINS 참고. ANTHROPIC_API_KEY/OPENAI_API_KEY/
GEMINI_API_KEY/DEEPSEEK_API_KEY 중 어느 것도 없으면 더미 모드로 동작한다
(telegram_client.py/kis_client.py와 동일한 패턴 -- pytest가 실제 키 없이도
항상 통과해야 함).

2026-08-04: 사용자가 제미나이를 신뢰하지 않아(환각 증상, 과거 데이터 버퍼
문제, 시황 웹서치 결과 괴리) 실제 폴백 체인(_FALLBACK_CHAINS)에서 제미나이를
뺐다 -- GEMINI_API_KEY 자체나 _call_gemini()는 그대로 남겨둠(주석 처리
개념, 완전 삭제 아님). 필요해지면 체인에 다시 넣기만 하면 복구됨.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger("atrsite.llm")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_API_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

MAX_TOKENS = 2000
# 웹 검색 도구를 켠 호출은 검색 라운드마다 thinking/tool_use 블록이 쌓여
# 최종 text 블록 전에 예산을 다 써버릴 수 있다 -- 실제로 stop_reason=
# max_tokens로 text 없이 끝나는 걸 확인해서(2026-08-03) 여유있게 크게 잡는다.
WEB_SEARCH_MAX_TOKENS = 8000


class LLMError(RuntimeError):
    """모든 공급자가 실패했을 때 발생."""


def is_dummy_mode() -> bool:
    return not (
        settings.anthropic_api_key or settings.openai_api_key
        or settings.gemini_api_key or settings.deepseek_api_key
    )


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    used_web_search: bool = False


def _call_claude(system: str, user: str, *, use_web_search: bool, max_tokens: int, http: httpx.Client) -> LLMResponse:
    """Anthropic Messages API. web_search 도구는 Claude가 최신 뉴스를 직접
    검색하게 할 때만 켠다(사용자 요청: 뉴스는 LLM이 검색, 수치는 프로그램이
    검증해서 넘김 -- 이 함수 자체는 뉴스/수치 구분을 모르고 호출자가 결정)."""
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict = {
        "model": settings.llm_model_claude,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if use_web_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    res = http.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=60)
    res.raise_for_status()
    payload = res.json()
    content = payload.get("content") or []
    # web_search를 쓰면 응답에 검색 도구 사용 블록이 섞여 들어온다 -- text 타입만
    # 이어붙인다(도구 사용 자체를 사용자에게 보여줄 필요는 없음).
    text = "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
    if not text:
        raise LLMError(f"Claude 응답에 text 블록이 없음: {payload}")
    return LLMResponse(text=text, provider="claude", model=settings.llm_model_claude, used_web_search=use_web_search)


def _call_gpt(system: str, user: str, *, max_tokens: int, http: httpx.Client) -> LLMResponse:
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    body = {
        "model": settings.llm_model_gpt,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    res = http.post(OPENAI_API_URL, headers=headers, json=body, timeout=60)
    res.raise_for_status()
    payload = res.json()
    choices = payload.get("choices") or []
    if not choices:
        raise LLMError(f"GPT 응답에 choices가 없음: {payload}")
    text = (choices[0].get("message") or {}).get("content", "").strip()
    if not text:
        raise LLMError(f"GPT 응답이 비어있음: {payload}")
    return LLMResponse(text=text, provider="gpt", model=settings.llm_model_gpt)


def _call_deepseek(system: str, user: str, *, max_tokens: int, http: httpx.Client) -> LLMResponse:
    """DeepSeek는 OpenAI 호환 chat completions API. 자체 웹서치 도구가 없어서
    (2026-08-03 확인) 1단계(실시간 조회)에는 못 쓰고, 2단계(이미 모은 데이터
    해석만 하는 단계, 검색 불필요)의 저렴한 폴백 후보 자리만 만들어둔다 --
    사용자 지시: "일단 딥시크 자리는 만들어놔"."""
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"}
    body = {
        "model": settings.llm_model_deepseek,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    res = http.post(DEEPSEEK_API_URL, headers=headers, json=body, timeout=60)
    res.raise_for_status()
    payload = res.json()
    choices = payload.get("choices") or []
    if not choices:
        raise LLMError(f"DeepSeek 응답에 choices가 없음: {payload}")
    text = (choices[0].get("message") or {}).get("content", "").strip()
    if not text:
        raise LLMError(f"DeepSeek 응답이 비어있음: {payload}")
    return LLMResponse(text=text, provider="deepseek", model=settings.llm_model_deepseek)


def _call_gemini(system: str, user: str, *, use_web_search: bool, max_tokens: int, http: httpx.Client) -> LLMResponse:
    """Gemini generateContent API. use_web_search=True면 google_search
    그라운딩 도구를 켠다(2026-08-03 추가 -- "객관적이고 판단이 필요없는
    조회는 제미나이" 지시에 따라 검색 지원 필요). Gemini의 정확한 도구
    스펙은 실제 키로 라이브 검증을 아직 못 했다 -- 문서화된 형태(google_search
    grounding)로 구현해뒀고, 키가 생기면 첫 실행에서 바로 확인 가능."""
    # 키를 URL 쿼리스트링(?key=)이 아니라 헤더로 보낸다 -- httpx 예외 메시지에
    # 요청 URL이 그대로 노출되는데(예: HTTPStatusError), 쿼리스트링 방식이면
    # 그 키가 로그 파일에 평문으로 남는다(2026-08-03 실제 발생 확인 후 수정).
    url = GEMINI_API_URL_TMPL.format(model=settings.llm_model_gemini)
    body: dict = {
        "contents": [{"parts": [{"text": f"{system}\n\nUser: {user}"}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if use_web_search:
        body["tools"] = [{"google_search": {}}]

    res = http.post(url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}, timeout=60)
    res.raise_for_status()
    payload = res.json()
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LLMError(f"Gemini 응답에 candidates가 없음: {payload}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise LLMError(f"Gemini 응답이 비어있음: {payload}")
    return LLMResponse(text=text, provider="gemini", model=settings.llm_model_gemini, used_web_search=use_web_search)


# 2026-08-04: 제미나이를 신뢰하지 않는다는 사용자 판단(환각/과거데이터 버퍼
# 증상, 시황 웹서치 괴리)으로 기본 순서에서도 제외. 이전: ("claude", "gpt", "gemini")
_PROVIDER_ORDER = ("claude", "gpt")

# 단계(stage)별로 폴백 순서를 다르게 준다(2026-08-03). 처음엔 preferred
# provider 이름을 그대로 딕셔너리 키로 썼는데, 1단계/2단계가 둘 다 Gemini를
# 맨 앞에 두게 되면서(2026-08-03, "클로드가 더 비싸니 제미나이-GPT-클로드
# 순으로") 같은 키("gemini")로 서로 다른 두 체인을 표현해야 하는 문제가
# 생김 -- provider 이름이 아니라 "무엇을 하는 단계인가"로 키를 바꿈:
#
# - stage1_search(1단계, 객관조회): gpt -> claude. 검색 도구가 없는 DeepSeek는
#   애초에 제외.
# - stage2_judgment(2단계, 판단): deepseek -> gpt -> claude(2026-08-04,
#   "딥시크로 해봐" 사용자 지시로 1순위). 클로드는 가장 비싸서 최후 폴백.
#
# 2026-08-04: 제미나이는 사용자가 신뢰하지 않아(환각 증상, 과거 데이터 버퍼
# 문제, 시황 웹서치 결과 괴리) 두 체인 모두에서 제외했다(실사용 기록으로도
# 항상 1순위 제미나이가 응답해서 폴백이 한 번도 발동 안 했던 걸 확인함,
# 즉 "독점"이었음). GEMINI_API_KEY/_call_gemini()는 코드에 그대로 남겨둠
# (주석 처리 개념) -- 필요해지면 아래 튜플에 "gemini"만 다시 넣으면 복구됨.
# 2026-08-04: stage2_judgment은 DeepSeek를 1순위로("딥시크로 해봐" 사용자
# 지시) -- stage1_search는 웹서치가 필요한데 DeepSeek는 검색 도구가 없어서
# 그대로 GPT 우선 유지.
# 이전:
#   "stage1_search": ("gemini", "gpt", "claude"),
#   "stage2_judgment": ("gemini", "gpt", "deepseek", "claude"),
#   "stage2_judgment": ("gpt", "deepseek", "claude"),
_FALLBACK_CHAINS = {
    "stage1_search": ("gpt", "claude"),
    "stage2_judgment": ("deepseek", "gpt", "claude"),
}


def ask(
    system: str, user: str, *, use_web_search: bool = False, chain: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """기본 순서는 Claude -> GPT 폴백이지만, chain을 주면
    _FALLBACK_CHAINS에 정의된 단계별 전용 순서를 따른다(2026-08-03 추가 --
    analysis_service가 1단계/2단계마다 다른 순서로 공급자를 시도하게 함).
    체인 안의 공급자가 키 미설정/실패면 정해진 순서대로 폴백한다.

    use_web_search는 Claude/Gemini에 적용된다(GPT/DeepSeek는 이 클라이언트에서
    별도 검색 도구를 안 붙임 -- 표준 chat completions에 쓸만한 웹서치 도구가
    없어서 생략).

    max_tokens를 안 주면 use_web_search 여부로 MAX_TOKENS/WEB_SEARCH_MAX_TOKENS
    중 하나를 자동 선택한다. 호출자가 원래 예상보다 긴 응답이 필요하다고
    이미 아는 경우(예: 종목탐구의 20항목 분석 -- 매크로 브리핑의 10항목보다
    구조적으로 길어서 검색 없이도 2000토큰으로는 부족함, 2026-08-03 실제
    라이브 테스트 중 응답이 3줄에서 잘리는 걸 발견해서 추가) 명시적으로
    더 크게 줄 수 있다.

    더미 모드일 때는 실제 호출 없이 명확히 "더미"라고 표시된 텍스트를
    반환한다 -- pytest 및 키 설정 전 로컬 개발에서 UI 배선을 확인할 수
    있게 하기 위함.
    """
    if is_dummy_mode():
        logger.info("[더미 LLM 응답 -- ANTHROPIC/OPENAI/GEMINI API 키 전부 미설정]")
        return LLMResponse(
            text=(
                "[더미 응답] LLM API 키가 설정되어 있지 않습니다. .env에 "
                "ANTHROPIC_API_KEY(권장) / OPENAI_API_KEY / GEMINI_API_KEY 중 "
                "하나 이상을 채우면 실제 브리핑이 생성됩니다."
            ),
            provider="dummy",
            model="dummy",
        )

    order = list(_FALLBACK_CHAINS.get(chain, _PROVIDER_ORDER))
    effective_max_tokens = max_tokens if max_tokens is not None else (
        WEB_SEARCH_MAX_TOKENS if use_web_search else MAX_TOKENS
    )

    http = httpx.Client()
    last_exc: Optional[Exception] = None
    try:
        for provider in order:
            if provider == "claude" and settings.anthropic_api_key:
                try:
                    return _call_claude(
                        system, user, use_web_search=use_web_search, max_tokens=effective_max_tokens, http=http,
                    )
                except Exception as exc:  # noqa: BLE001 -- 다음 공급자로 폴백
                    logger.warning("Claude 호출 실패, 다음 공급자로 폴백: %s", exc)
                    last_exc = exc
            elif provider == "gpt" and settings.openai_api_key:
                try:
                    return _call_gpt(system, user, max_tokens=effective_max_tokens, http=http)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("GPT 호출 실패, 다음 공급자로 폴백: %s", exc)
                    last_exc = exc
            elif provider == "gemini" and settings.gemini_api_key:
                try:
                    return _call_gemini(
                        system, user, use_web_search=use_web_search, max_tokens=effective_max_tokens, http=http,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Gemini 호출 실패: %s", exc)
                    last_exc = exc
            elif provider == "deepseek" and settings.deepseek_api_key:
                try:
                    return _call_deepseek(system, user, max_tokens=effective_max_tokens, http=http)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("DeepSeek 호출 실패, 다음 공급자로 폴백: %s", exc)
                    last_exc = exc
    finally:
        http.close()

    raise LLMError(f"모든 LLM 공급자 호출 실패: {last_exc}") from last_exc
