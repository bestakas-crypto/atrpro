"""시세 수집 Worker -- 스펙 5.2(웹과 분리된 별도 프로세스), 11.3(폴링 정책).

웹 프로세스(main.py)와 이 프로세스는 동일한 SQLite DB만 공유하고 서로 직접
통신하지 않는다. 3단계 이후 kis_client.py는 여전히 더미 데이터를 반환하므로,
이 스크립트는 "시세->ATR->신호->알림 Outbox" 전체 배선이 끊김 없이 돌아가는지
확인하는 용도다. 4단계부터는 매 폴링 주기마다 Outbox도 함께 비운다
(notification_service.process_outbox) -- 텔레그램 토큰이 없으면 더미 모드로
콘솔에만 로그를 남긴다.

실행:
    python -m atrsite.worker            # 무한 폴링 루프
    python -m atrsite.worker --once     # 한 번만 실행하고 종료 (테스트/검증용)
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta

from . import db
from .adapters.kis_client import KisApiError, get_client
from .repositories import instruments as instruments_repo
from .repositories import market_data as market_data_repo
from .services import notification_service, portfolio_service
from .services.atr_engine import InsufficientDataError, latest_atr
from .services.market_schedule import MarketPhase, decide_polling

logger = logging.getLogger("atrsite.worker")

REGULAR_POLL_INTERVAL_SECONDS = 300  # 스펙 11.3 -- 정규장 5분 폴링
IDLE_POLL_INTERVAL_SECONDS = 60  # PRE_MARKET/POST_MARKET에서 phase 전환을 놓치지 않기 위한 간격
HOLIDAY_POLL_INTERVAL_SECONDS = 1800  # 비거래일에는 자주 깨어날 필요 없음


def poll_quotes(conn: sqlite3.Connection) -> None:
    """정규장: 현재가 + 당일 고가 조회 후 반영 (스펙 11.3)."""
    client = get_client()
    for instrument in instruments_repo.list_instruments(conn):
        code = instrument["kis_code"]
        if not code:
            logger.debug("종목 %s(%s)에 kis_code가 없어 건너뜀", instrument["name"], instrument["id"])
            continue
        try:
            quote = client.get_current_price(code, market=instrument["kis_market"], is_etf=instrument["is_etf"])
        except KisApiError as exc:
            logger.warning("현재가 조회 실패 instrument=%s: %s", instrument["name"], exc)
            # 스펙 17.3 "연속 3회 실패 -> API 장애" 카운터 갱신 + 즉시 재판정.
            # 성공을 기다리지 않고 바로 반영해야 3번째 실패 순간 신호가 곧장
            # API_ERROR로 막힌다(다음 성공한 폴링까지 기다리지 않음).
            market_data_repo.record_quote_failure(conn, instrument["id"])
            portfolio_service.recompute_signal(conn, instrument["id"])
            continue
        portfolio_service.commit_quote(
            conn, instrument["id"], price=quote.price, quoted_at=quote.quoted_at,
            source="kis", data_status="FRESH", day_high=quote.day_high, change_pct=quote.change_pct,
        )
        logger.info("현재가 반영 %s: %s", instrument["name"], quote.price)


def collect_daily_bars_and_update_atr(conn: sqlite3.Connection) -> None:
    """마감 후: 확정 일봉 수집 -> ATR 갱신 -> (체크포인트/백업은 4~5단계에서 연결) (스펙 11.3)."""
    client = get_client()
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=60)).isoformat()

    for instrument in instruments_repo.list_instruments(conn):
        code = instrument["kis_code"]
        if not code:
            continue
        try:
            bars = client.get_daily_bars(
                code, start, end, market=instrument["kis_market"], is_etf=instrument["is_etf"],
            )
        except KisApiError as exc:
            logger.warning("일봉 조회 실패 instrument=%s: %s", instrument["name"], exc)
            continue

        try:
            point = latest_atr(bars, period=14)
        except InsufficientDataError as exc:
            logger.warning("ATR 계산에 필요한 일봉이 부족함 instrument=%s: %s", instrument["name"], exc)
            continue

        portfolio_service.commit_atr(
            conn, instrument["id"], atr=point.atr, trade_date=point.trade_date, source="kis",
        )
        logger.info("ATR 갱신 %s: %s (%s)", instrument["name"], point.atr, point.trade_date)


# POST_MARKET은 장마감(15:30)부터 자정까지 몇 시간이나 이어지고, 그 사이
# IDLE_POLL_INTERVAL_SECONDS(60초)마다 run_once()가 반복 호출된다. 이 가드가
# 없으면 하루치 확정 일봉/ATR을 이미 다 반영해놓고도 자정까지 매분 KIS에
# 똑같은 일봉 조회를 수백 번 반복하게 된다 -- 날짜가 바뀌면(다음 거래일
# POST_MARKET) 자동으로 다시 수집하도록 날짜만 기억한다.
_last_daily_bars_collection_date: date | None = None


def run_once(now: datetime | None = None) -> MarketPhase:
    """폴링 결정 1회 실행 -- 테스트와 `--once` 모드가 공유하는 진입점."""
    global _last_daily_bars_collection_date
    now = now or datetime.now()
    decision = decide_polling(now)
    logger.info("phase=%s reason=%s", decision.phase.value, decision.reason)

    conn = db.connect()
    try:
        db.init_db(conn)
        if decision.should_poll_quotes:
            poll_quotes(conn)
        if decision.should_collect_daily_bars and _last_daily_bars_collection_date != now.date():
            collect_daily_bars_and_update_atr(conn)
            _last_daily_bars_collection_date = now.date()
        conn.commit()

        # Outbox 발송은 별도 커밋 경계로 분리한다 -- 텔레그램 발송 자체가
        # 실패해도(재시도 상태로 기록될 뿐) 이미 반영된 시세/ATR/신호 갱신은
        # 그대로 남아야 하기 때문이다.
        outbox_result = notification_service.process_outbox(conn)
        conn.commit()
        if outbox_result["sent"] or outbox_result["retried"] or outbox_result["failed"]:
            logger.info("outbox: sent=%s retried=%s failed=%s", *outbox_result.values())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return decision.phase


def _sleep_seconds_for(phase: MarketPhase) -> int:
    if phase == MarketPhase.REGULAR:
        return REGULAR_POLL_INTERVAL_SECONDS
    if phase == MarketPhase.HOLIDAY:
        return HOLIDAY_POLL_INTERVAL_SECONDS
    return IDLE_POLL_INTERVAL_SECONDS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="한 번만 실행하고 종료한다")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.once:
        run_once()
        return

    while True:
        phase = run_once()
        time.sleep(_sleep_seconds_for(phase))


if __name__ == "__main__":
    main()
