"""Application settings.

Reads from a .env file (if present) and environment variables. Secrets (KIS
keys, Telegram token) never live in this file per spec 14.2 -- only in the
environment / .env, and are never hardcoded here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.environ.get("APP_NAME", "ATRsite")
    app_short_name: str = os.environ.get("APP_SHORT_NAME", "ATR")

    db_path: Path = Path(os.environ.get("ATRSITE_DB_PATH", str(BASE_DIR / "data" / "atrsite.db")))
    frontend_dir: Path = Path(os.environ.get("ATRSITE_FRONTEND_DIR", str(BASE_DIR / "frontend")))
    backups_dir: Path = Path(os.environ.get("ATRSITE_BACKUPS_DIR", str(BASE_DIR / "backups")))

    # Spec 14.1 -- single-user auth. Empty (the local-dev default) means auth
    # is not enforced. A real deployment must set this.
    api_key: str = os.environ.get("API_KEY", "")

    kis_app_key: str = os.environ.get("KIS_APP_KEY", "")
    kis_app_secret: str = os.environ.get("KIS_APP_SECRET", "")
    kis_account_no: str = os.environ.get("KIS_ACCOUNT_NO", "")
    kis_is_paper_trading: bool = _env_bool("KIS_IS_PAPER_TRADING", True)

    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")

    # 2026-08-03 -- LLM 매크로 브리핑용. C:\mmean의 llm_chain.py 패턴을 참고했지만
    # 이 프로젝트는 완전히 독립적으로 키/설정을 관리한다(mmean의 .env를 그대로
    # 재사용하지 않음). 전부 비어있으면 더미 모드로 동작.
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    llm_model_claude: str = os.environ.get("LLM_MODEL_CLAUDE", "claude-sonnet-5")
    llm_model_gpt: str = os.environ.get("LLM_MODEL_GPT", "gpt-4o-mini")
    llm_model_gemini: str = os.environ.get("LLM_MODEL_GEMINI", "gemini-flash-latest")
    # deepseek-v4-flash: 저렴/빠른 모델. 2단계(판단)의 최후 폴백 후보라 검색
    # 능력이 필요 없고, 웹서치 안 쓰는 만큼 굳이 pro(고성능) 쓸 이유가
    # 없어서 flash를 기본값으로 함(2026-08-03, 사용자 제공 가격표 기준).
    llm_model_deepseek: str = os.environ.get("LLM_MODEL_DEEPSEEK", "deepseek-v4-flash")

    # 2026-08-03 -- 종목탐구(company-explorer)용. SEC EDGAR는 키가 필요
    # 없지만 공정이용 정책상 요청자 식별용 User-Agent(회사명+연락처)를
    # 요구한다. 비워두면 동작은 하지만 SEC가 권장하는 "진짜 연락처"가
    # 아니므로, 실사용량이 늘면 .env에 실제 연락처로 채우는 걸 권장.
    sec_user_agent: str = os.environ.get(
        "SEC_USER_AGENT", "ATRsite-pro/1.0 (personal use; contact-not-set@example.com)"
    )
    # OpenDART(한국 기업 공시)는 회원가입 후 발급받는 키가 필요 --
    # 비어있으면 opendart_client.py가 항상 UNAVAILABLE로 응답(더미 모드).
    opendart_api_key: str = os.environ.get("OPENDART_API_KEY", "")


settings = Settings()
