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


settings = Settings()
