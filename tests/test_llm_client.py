"""llm_client.py 단위 테스트 -- 실제 API 키 없이 더미 모드/필드 파싱만 검증한다.

kis_client.py/telegram_client.py와 동일한 원칙: 이 개발 환경의 .env에 실제
키가 들어갈 수 있으므로, 더미 모드 테스트는 항상 명시적으로 키를 비운다.
"""
import httpx
import pytest

from atrsite.adapters import llm_client
from atrsite.config import settings


@pytest.fixture()
def restore_llm_keys():
    original = (
        settings.anthropic_api_key, settings.openai_api_key,
        settings.gemini_api_key, settings.deepseek_api_key,
    )
    yield
    object.__setattr__(settings, "anthropic_api_key", original[0])
    object.__setattr__(settings, "openai_api_key", original[1])
    object.__setattr__(settings, "gemini_api_key", original[2])
    object.__setattr__(settings, "deepseek_api_key", original[3])


@pytest.fixture()
def dummy_mode(restore_llm_keys):
    object.__setattr__(settings, "anthropic_api_key", "")
    object.__setattr__(settings, "openai_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "")
    object.__setattr__(settings, "deepseek_api_key", "")


@pytest.fixture()
def fake_claude_key(restore_llm_keys):
    object.__setattr__(settings, "anthropic_api_key", "dummy-anthropic-key")
    object.__setattr__(settings, "openai_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "")
    object.__setattr__(settings, "deepseek_api_key", "")


def test_is_dummy_mode_true_when_no_keys(dummy_mode):
    assert llm_client.is_dummy_mode() is True


def test_ask_returns_labeled_dummy_response(dummy_mode):
    result = llm_client.ask("system prompt", "user prompt")
    assert result.provider == "dummy"
    assert "더미" in result.text


def test_ask_parses_claude_text_blocks(fake_claude_key, monkeypatch):
    """web_search를 쓰면 응답에 text 외 블록도 섞여 오는데, text 타입만
    이어붙여야 한다."""
    def fake_post(self, url, headers=None, json=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "content": [
                        {"type": "server_tool_use", "id": "x", "name": "web_search"},
                        {"type": "text", "text": "오늘 브리핑 "},
                        {"type": "text", "text": "요약입니다."},
                    ]
                }
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = llm_client.ask("system", "user", use_web_search=True)
    assert result.text == "오늘 브리핑 요약입니다."
    assert result.provider == "claude"
    assert result.used_web_search is True


def test_ask_falls_back_to_gpt_when_claude_fails(restore_llm_keys, monkeypatch):
    object.__setattr__(settings, "anthropic_api_key", "bad-key")
    object.__setattr__(settings, "openai_api_key", "dummy-openai-key")
    object.__setattr__(settings, "gemini_api_key", "")

    def fake_post(self, url, headers=None, json=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                if "anthropic" in url:
                    raise httpx.HTTPStatusError("500", request=None, response=None)

            def json(self):
                if "anthropic" in url:
                    return {}
                return {"choices": [{"message": {"content": "GPT 응답"}}]}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = llm_client.ask("system", "user")
    assert result.provider == "gpt"
    assert result.text == "GPT 응답"


def test_ask_raises_when_all_providers_fail(restore_llm_keys, monkeypatch):
    object.__setattr__(settings, "anthropic_api_key", "bad-key")
    object.__setattr__(settings, "openai_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "")

    def fake_post(self, url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    with pytest.raises(llm_client.LLMError):
        llm_client.ask("system", "user")


def test_ask_stage1_search_tries_gpt_first(restore_llm_keys, monkeypatch):
    """chain="stage1_search"면 제미나이 키가 있어도(2026-08-04, 사용자가
    제미나이를 신뢰하지 않아 체인에서 뺌 -- 환각/과거데이터 버퍼 증상, 시황
    웹서치 괴리) GPT를 먼저 시도해야 한다. Claude 키도 있지만 GPT가 성공하면
    호출 안 됨."""
    object.__setattr__(settings, "anthropic_api_key", "dummy-anthropic-key")
    object.__setattr__(settings, "openai_api_key", "dummy-openai-key")
    object.__setattr__(settings, "gemini_api_key", "dummy-gemini-key")

    called_urls = []

    def fake_post(self, url, headers=None, json=None, timeout=None):
        called_urls.append(url)

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "GPT 응답"}}]}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = llm_client.ask("system", "user", chain="stage1_search")
    assert result.provider == "gpt"
    assert len(called_urls) == 1
    assert "openai" in called_urls[0]  # 제미나이는 호출조차 안 됨(체인에서 빠짐)


def test_call_gemini_uses_google_search_tool_when_requested(restore_llm_keys, monkeypatch):
    """제미나이는 2026-08-04부로 기본 폴백 체인에서 빠졌지만(사용자가 신뢰
    안 함) _call_gemini() 자체는 코드에 남겨뒀다("주석 처리" 개념, 완전
    삭제 아님) -- 나중에 되돌릴 수 있게 이 함수 단위로 직접 검증."""
    object.__setattr__(settings, "gemini_api_key", "dummy-gemini-key")

    captured = {}

    def fake_post(self, url, headers=None, json=None, timeout=None):
        captured["body"] = json

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "검색 결과 요약"}]}}]}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = llm_client._call_gemini(
        "system", "user", use_web_search=True, max_tokens=2000, http=httpx.Client(),
    )
    assert result.used_web_search is True
    assert captured["body"]["tools"] == [{"google_search": {}}]


def test_ask_stage1_gpt_fails_over_to_claude(restore_llm_keys, monkeypatch):
    """1단계에서 GPT가 실패하면(2026-08-04, 제미나이는 체인에서 빠졌으니
    다음 폴백은 곧장 Claude) Claude로 넘어가야 한다."""
    object.__setattr__(settings, "anthropic_api_key", "dummy-anthropic-key")
    object.__setattr__(settings, "openai_api_key", "dummy-openai-key")
    object.__setattr__(settings, "gemini_api_key", "dummy-gemini-key")

    called_urls = []

    def fake_post(self, url, headers=None, json=None, timeout=None):
        called_urls.append(url)
        if "openai" in url:
            raise httpx.HTTPStatusError("quota exhausted", request=None, response=None)

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"content": [{"type": "text", "text": "Claude 응답"}]}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = llm_client.ask("system", "user", chain="stage1_search")
    assert result.provider == "claude"
    assert len(called_urls) == 2
    assert "openai" in called_urls[0]
    assert "anthropic" in called_urls[1]
    assert not any("generativelanguage" in u for u in called_urls)  # 제미나이 호출 안 됨


def test_ask_parses_deepseek_response(restore_llm_keys, monkeypatch):
    """DeepSeek는 OpenAI 호환 chat completions -- GPT와 동일한 응답 형태.
    (GPT 키가 없으니 stage2_judgment 체인에서 바로 DeepSeek까지 폴백된다 --
    제미나이는 2026-08-04부로 이 체인에 아예 없음.)"""
    object.__setattr__(settings, "anthropic_api_key", "")
    object.__setattr__(settings, "openai_api_key", "")
    object.__setattr__(settings, "gemini_api_key", "")
    object.__setattr__(settings, "deepseek_api_key", "dummy-deepseek-key")

    def fake_post(self, url, headers=None, json=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "딥시크 응답"}}]}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = llm_client.ask("system", "user", chain="stage2_judgment")
    assert result.provider == "deepseek"
    assert result.text == "딥시크 응답"


def test_ask_stage2_tries_gpt_first_and_claude_last(restore_llm_keys, monkeypatch):
    """2단계(chain="stage2_judgment")는 클로드가 가장 비싸서 맨 마지막
    최후 폴백이고, GPT를 맨 먼저 시도한다(2026-08-04 -- 제미나이는 사용자가
    신뢰 안 해서 체인에서 빠짐). 검증 안 된 DeepSeek는 클로드 바로 앞 자리."""
    object.__setattr__(settings, "anthropic_api_key", "dummy-anthropic-key")
    object.__setattr__(settings, "openai_api_key", "dummy-openai-key")
    object.__setattr__(settings, "gemini_api_key", "dummy-gemini-key")
    object.__setattr__(settings, "deepseek_api_key", "dummy-deepseek-key")

    called_urls = []

    def fake_post(self, url, headers=None, json=None, timeout=None):
        called_urls.append(url)
        if "openai" in url:
            raise httpx.HTTPStatusError("fail", request=None, response=None)

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "딥시크 응답"}}]}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = llm_client.ask("system", "user", chain="stage2_judgment")
    assert result.provider == "deepseek"
    assert len(called_urls) == 2
    assert "openai" in called_urls[0]
    assert "deepseek" in called_urls[1]
    # anthropic(claude)/제미나이는 한 번도 호출 안 됨 -- 진짜 최후 폴백/미사용인지 확인
    assert not any("anthropic" in u for u in called_urls)
    assert not any("generativelanguage" in u for u in called_urls)


def test_ask_max_tokens_override_passed_to_provider(fake_claude_key, monkeypatch):
    captured = {}

    def fake_post(self, url, headers=None, json=None, timeout=None):
        captured["max_tokens"] = json["max_tokens"]

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"content": [{"type": "text", "text": "응답"}]}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    llm_client.ask("system", "user", max_tokens=8000)
    assert captured["max_tokens"] == 8000


def test_ask_without_override_uses_default_max_tokens(fake_claude_key, monkeypatch):
    captured = {}

    def fake_post(self, url, headers=None, json=None, timeout=None):
        captured["max_tokens"] = json["max_tokens"]

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"content": [{"type": "text", "text": "응답"}]}
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    llm_client.ask("system", "user")
    assert captured["max_tokens"] == llm_client.MAX_TOKENS
