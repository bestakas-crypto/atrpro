"""텔레그램 발송 클라이언트 -- 스펙 12절.

TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID가 .env에 없으면 실제 HTTP 호출 대신
콘솔 로그로만 출력하는 더미 모드로 동작한다 (프롬프트 4단계 지시 -- 실제
토큰 없이도 Outbox 재시도 로직을 테스트할 수 있어야 함).
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger("atrsite.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"


def is_dummy_mode() -> bool:
    return not settings.telegram_bot_token or not settings.telegram_chat_id


def send_message(text: str) -> bool:
    """성공하면 True, 실패하면 False를 반환한다 -- 예외를 던지지 않는다.
    호출자(notification_service.process_outbox)가 이 반환값만 보고 Outbox의
    재시도 상태(스펙 12.2)를 결정하기 때문이다.
    """
    if is_dummy_mode():
        logger.info("[더미 텔레그램 발송 -- BOT_TOKEN/CHAT_ID 미설정]\n%s", text)
        return True

    url = f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    try:
        res = httpx.post(url, json={"chat_id": settings.telegram_chat_id, "text": text}, timeout=10)
        res.raise_for_status()
        body = res.json()
        if not body.get("ok"):
            logger.warning("텔레그램 발송 실패: %s", body)
            return False
        return True
    except httpx.HTTPError as exc:
        logger.warning("텔레그램 발송 오류: %s", exc)
        return False
