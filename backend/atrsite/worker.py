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
from datetime import time as dt_time

from . import db
from .adapters import telegram_client
from .adapters.kis_client import KisApiError, get_client
from .repositories import instruments as instruments_repo
from .repositories import market_data as market_data_repo
from .repositories import snapshots as snapshots_repo
from .services import notification_service, portfolio_service, schedule_notification_service, snapshot_service
from .services.atr_engine import InsufficientDataError, latest_atr
from .services.market_schedule import MarketPhase, decide_polling

logger = logging.getLogger("atrsite.worker")

REGULAR_POLL_INTERVAL_SECONDS = 300  # 스펙 11.3 -- 정규장 5분 폴링
IDLE_POLL_INTERVAL_SECONDS = 60  # PRE_MARKET/POST_MARKET에서 phase 전환을 놓치지 않기 위한 간격
HOLIDAY_POLL_INTERVAL_SECONDS = 1800  # 비거래일에는 자주 깨어날 필요 없음

# v1.3 자산 스냅샷 -- 매일 06:30 KST에 시도, 실패/부분완료면 06:40/07:00에 재시도하고
# 그 이후엔 포기한다(스펙 9절 "무한 재시도하지 않는다"). 이 3개 시각 사이에는 worker가
# 촘촘히 깨어나야 재시도가 실제로 그 시각 근처에 일어나므로, 이 구간만은 시장
# phase(휴장일 30분 간격 등)와 무관하게 IDLE_POLL_INTERVAL_SECONDS로 강제한다.
_SNAPSHOT_RETRY_TIMES = (dt_time(6, 30), dt_time(6, 40), dt_time(7, 0))
_SNAPSHOT_WINDOW_START = dt_time(6, 15)
_SNAPSHOT_WINDOW_END = dt_time(7, 15)


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

# v1.2 예약알림 -- 하루 1번만 "오늘 알릴 회차"를 훑어 outbox에 적재하면 충분하다
# (KRX 시장 상태와 무관하게 매일 돌아야 함 -- GENERAL/RULE_REVIEW 등은 휴장일에도
# 알림이 필요할 수 있음). 실제 발송(process_outbox)은 멱등하므로 매 폴링마다 돈다.
_last_schedule_notification_enqueue_date: date | None = None

# v1.3 자산 스냅샷 재시도 상태 -- 날짜가 바뀌면 0으로 리셋된다(daily-restart
# 타이머로 매일 08:00 프로세스 자체가 재시작되니 사실상 매일 새로 시작).
_snapshot_last_check_date: date | None = None
_snapshot_attempts_today: int = 0


def _maybe_run_daily_snapshot(conn: sqlite3.Connection, now: datetime) -> None:
    """스펙 3/9/18절 -- 06:30/06:40/07:00 최대 3회 시도, 동일 날짜 중복 방지(upsert),
    실패/부분/2일누락 시에만 텔레그램 경고(성공 시엔 알림 안 보냄-- 스펙 18절)."""
    global _snapshot_last_check_date, _snapshot_attempts_today

    if _snapshot_last_check_date != now.date():
        _snapshot_last_check_date = now.date()
        _snapshot_attempts_today = 0

    if _snapshot_attempts_today >= len(_SNAPSHOT_RETRY_TIMES):
        return  # 오늘 재시도 소진 -- 다음날까지 대기

    next_retry_time = _SNAPSHOT_RETRY_TIMES[_snapshot_attempts_today]
    if now.time() < next_retry_time:
        return  # 이 회차 시각이 아직 안 됨

    existing = snapshots_repo.get_snapshot(conn, now.date().isoformat())
    if existing is not None and existing["data_quality_status"] in snapshot_service.COMPLETE_LIKE_STATUSES:
        _snapshot_attempts_today = len(_SNAPSHOT_RETRY_TIMES)  # 이미 정상 완료 -- 재시도 불필요
        return

    _snapshot_attempts_today += 1
    logger.info("자산 스냅샷 시도 %s/%s (%s)", _snapshot_attempts_today, len(_SNAPSHOT_RETRY_TIMES), now.date())
    try:
        data = snapshot_service.build_snapshot(conn, snapshot_date=now.date(), now=now, triggered_by="auto")
        saved = snapshots_repo.save_snapshot(conn, data)
    except Exception:
        logger.exception("자산 스냅샷 계산 실패 date=%s", now.date())
        conn.rollback()
        _send_snapshot_alert(f"[ATRsite 자산기록 오류]\n\n{now.date().isoformat()} 자산 스냅샷 계산 중 오류가 발생했습니다.")
        return
    conn.commit()

    if saved["data_quality_status"] in snapshot_service.COMPLETE_LIKE_STATUSES:
        _snapshot_attempts_today = len(_SNAPSHOT_RETRY_TIMES)  # 성공 -- 더 재시도 안 함
        logger.info("자산 스냅샷 완료 status=%s", saved["data_quality_status"])
    else:
        is_last_attempt = _snapshot_attempts_today >= len(_SNAPSHOT_RETRY_TIMES)
        if is_last_attempt:
            _send_snapshot_alert(
                f"[ATRsite 자산기록 오류]\n\n{now.date().isoformat()} 자산 스냅샷을 완성하지 못했습니다.\n\n"
                f"상태: {saved['data_quality_status']}\n"
                f"사유: {saved.get('quality_notes') or '-'}"
            )

    gap_warning = snapshot_service.find_missing_gap_warning(conn, today=now.date())
    if gap_warning:
        _send_snapshot_alert(f"[ATRsite 자산기록 경고]\n\n{gap_warning}")


def _send_snapshot_alert(text: str) -> None:
    """알림 실패 여부와 무관하게 워커 자체는 절대 멈추면 안 된다(스펙 18절
    "Telegram 설정이 없거나 전송에 실패해도 DB 기록과 화면 경고는 정상 작동")."""
    try:
        telegram_client.send_message(text)
    except Exception:
        logger.exception("자산 스냅샷 텔레그램 경고 발송 실패")


def run_once(now: datetime | None = None) -> MarketPhase:
    """폴링 결정 1회 실행 -- 테스트와 `--once` 모드가 공유하는 진입점."""
    global _last_daily_bars_collection_date, _last_schedule_notification_enqueue_date
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

        # v1.2 예약알림 -- 시장 상태(phase)와 무관하게 매일 1회 적재, 매 주기 발송.
        if _last_schedule_notification_enqueue_date != now.date():
            enqueued = schedule_notification_service.enqueue_due_notifications(conn, today=now.date())
            conn.commit()
            _last_schedule_notification_enqueue_date = now.date()
            if enqueued:
                logger.info("schedule notification enqueued: %s건", enqueued)

        schedule_outbox_result = schedule_notification_service.process_outbox(conn)
        conn.commit()
        if schedule_outbox_result["sent"] or schedule_outbox_result["retried"] or schedule_outbox_result["failed"]:
            logger.info("schedule outbox: sent=%s retried=%s failed=%s", *schedule_outbox_result.values())

        # v1.3 자산 스냅샷 -- 시장 phase와 무관하게 매일 06:30/06:40/07:00에만 시도.
        # 실패해도 워커 루프 자체를 멈추면 안 되므로 이 함수 내부에서 예외를 삼킨다.
        _maybe_run_daily_snapshot(conn, now)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return decision.phase


def _sleep_seconds_for(phase: MarketPhase, now: datetime | None = None) -> int:
    now = now or datetime.now()
    # v1.3 자산 스냅샷 06:30/06:40/07:00 재시도가 실제로 그 시각 근처에 일어나려면
    # 이 구간만은 휴장일 30분 간격(HOLIDAY_POLL_INTERVAL_SECONDS) 등을 무시하고
    # 촘촘히 깨어나야 한다.
    if _SNAPSHOT_WINDOW_START <= now.time() <= _SNAPSHOT_WINDOW_END:
        return IDLE_POLL_INTERVAL_SECONDS
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
