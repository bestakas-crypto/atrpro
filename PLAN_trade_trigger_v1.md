# ATRsite-pro 매매계획(트리거 감시) 기능 -- 구현 계획서 v1

작성일: 2026-08-07
근거 문서: `C:\Users\besta\Downloads\claude_trading_rules_v1_2026-08-07.txt` (설계 토론
전체 기록), GPT/Claude와의 다회차 설계 대화(이 세션)
상태: **설계 확정, 구현 전 최종 확인 대기** (5절 "확정 전 확인 필요" 참고)

## 0. 이 기능이 하는 일 (한 문장)

사용자가 종목별로 트리거 가격과 매도 계획을 직접 입력하면, ATRsite-pro는 그
계획을 그대로 저장하고 가격을 감시하다가 정해진 조건이 되면 알려준다.
**타당성 판단(찬반, 매크로 해석)은 프로그램이 하지 않는다** -- 그건 사용자가
LLM과 별도로 상의해서 이미 끝낸 뒤 프로그램에는 결정된 계획만 입력한다.

## 1. 범위

### 이번에 만드는 것
- 종목별(또는 여러 종목 묶음) 매매계획 저장/수정/이력 관리
- 4가지 계획 유형을 하나의 유연한 스키마로 표현: `TRAIL` / `LIQUIDATE_NOW` /
  `ACCUMULATE` / `NONE`
- 트리거 접근/도달/단계발동 알림 (텔레그램 + 화면)
- 사용자 기준선과 ATR 기반 데이터 권고선 동시 표시

### 이번에 만들지 않는 것 (범위 밖, 별도 논의 필요)
- T+M+G 자동 국면점수, 매크로 자동 채점 -- 전부 기각됨(이 세션에서 결론남)
- SMA200/126일 모멘텀 등 장기 추세 지표 -- 이걸 쓰려면 daily_bars 저장 경로
  전체를 다시 만들어야 하는 별도 v2 작업(이미 이전 세션에서 규모 확인함).
  이번 기능은 ATR(14)만 쓰고 장기 이평선은 쓰지 않는다.
- 코어/전술 물량 분리, 투자단계(ACCUMULATION~FINAL_EXIT) 자동판정 -- 이번엔
  사용자가 계획 유형을 직접 고르는 것으로 대체(아래 2.1)
- 자동 주문 실행 -- 이 프로젝트의 절대 원칙(스펙 2절)에 따라 계속 알림만 하고
  실제 매매는 사용자가 각자의 증권사에서 직접 함

## 2. 데이터 모델

### 2.1 새 테이블: `trade_plans`

```sql
CREATE TABLE trade_plans (
    id                    TEXT PRIMARY KEY,
    plan_type             TEXT NOT NULL,   -- TRAIL | LIQUIDATE_NOW | ACCUMULATE | NONE
    label                 TEXT NOT NULL,   -- 예: "나스닥100 부분익절", "KODEX200 종료"
    status                TEXT NOT NULL,   -- ARMED | ACTIVE | COMPLETED | CANCELLED
    -- TRAIL 전용
    trigger_price          REAL,
    trigger_direction      TEXT,            -- ABOVE | BELOW
    trigger_activated_at   TEXT,            -- 도달 시각(NULL이면 아직 ARMED)
    peak_price_since_trigger REAL,          -- 트리거 도달 이후 최고가(기존
                                             -- post_entry_high_price와 다름 --
                                             -- "매수 이후"가 아니라 "트리거
                                             -- 도달 이후" 최고가라 별도 필드 필요)
    allow_auto_reentry      INTEGER NOT NULL DEFAULT 0,
    confirm_mode            TEXT NOT NULL DEFAULT 'CLOSE', -- CLOSE | INTRADAY
    -- 공통
    purpose                TEXT,            -- 계획목적 자유서술
    invalidation_condition  TEXT,           -- 무효화 조건 자유서술
    review_date             TEXT,           -- 재검토일(지나면 PLAN_REVIEW)
    reason                  TEXT,           -- LLM 토론 후 최종 판단 요약
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE TABLE trade_plan_instruments (   -- 계획 하나에 종목 여러 개 연결(KODEX
                                          -- 200 두 계좌처럼)
    plan_id        TEXT NOT NULL REFERENCES trade_plans(id) ON DELETE CASCADE,
    instrument_id  TEXT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    PRIMARY KEY (plan_id, instrument_id)
);

CREATE TABLE trade_plan_tiers (          -- TRAIL 유형의 단계별 매도 규칙
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id        TEXT NOT NULL REFERENCES trade_plans(id) ON DELETE CASCADE,
    tier_order     INTEGER NOT NULL,     -- 1, 2, 3 ...
    pullback_pct   REAL NOT NULL,        -- 최고가 대비 하락률(사용자 기준)
    sell_pct       REAL NOT NULL,        -- baseline_quantity 대비 매도 비율
                                          -- (누적 아님, 이번 단계에서 추가로
                                          -- 파는 양)
    fired_at       TEXT,                 -- 이번 사이클에 발동했으면 시각 기록
    UNIQUE (plan_id, tier_order)
);
```

**`trade_plans.baseline_quantity`(신규 컬럼, 위 표에 추가 필요) -- 계획 확정
시점의 보유수량을 스냅샷으로 고정한다.** tier의 `sell_pct`는 항상 이
고정값에 곱해서 매도수량을 계산하고, 트리거 발동 시점의 실시간
`position_state.quantity`는 쓰지 않는다.

이유(2026-08-07 QQQ 1차 계획 확정 때 나온 실제 요건): QQQ는 717달러
트레일링 계획과는 별개로 VXN 조건부 적립(ACCUMULATE)이 동시에 진행 중이다.
"427주의 40%"라는 계획을 실시간 보유수량 기준으로 계산하면, 적립으로
수량이 늘어날 때마다 매도 목표수량도 같이 늘어나버려 원래 취지(기존
물량만 부분 익절)가 깨진다. 그래서 `baseline_quantity`를 계획 생성 시점에
한 번 확정해서 저장하고, 이후 적립으로 실보유수량이 얼마가 되든 tier
매도수량 계산에는 영향을 주지 않게 한다.

```sql
-- trade_plans에 추가할 컬럼
baseline_quantity  REAL NOT NULL   -- 계획 확정 시점 보유수량 스냅샷(고정)
```

CREATE TABLE trade_plan_history (        -- 계획 변경 이력(수정 시 새 행,
                                          -- instruments.py의 기존 버저닝
                                          -- 패턴 재사용)
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id        TEXT NOT NULL REFERENCES trade_plans(id) ON DELETE CASCADE,
    version        INTEGER NOT NULL,
    snapshot_json  TEXT NOT NULL,        -- 변경 전 상태 스냅샷
    change_reason  TEXT,
    changed_at     TEXT NOT NULL
);
```

**핵심 설계 판단(이 세션에서 확정한 것)**:
- 계획 유형을 테이블 4개로 쪼개지 않고 하나의 `trade_plans`에서 `plan_type`
  으로 분기한다 -- 유형별 화면/로직 중복을 피하기 위함.
  - `TRAIL`의 마지막 tier `sell_pct` 누적이 100%면 자연히 "청산형"(NVDA,
    KODEX 200), 100% 미만이면 "부분익절형"(QQQ). 별도 타입 안 나눔.
  - `allow_auto_reentry=0`이면 매도 완료 후 자동으로 재진입 후보를 띄우지
    않는다(NVDA, KODEX 200). `=1`이면 기존 매수신호 로직이 정상 재개된다(QQQ).
- `LIQUIDATE_NOW`(QLD)는 가격감시 필드를 전부 비워두고, 화면에서는
  `trades` 테이블의 누적 매도수량만 집계해서 "남은 수량"을 보여준다 --
  이 유형은 사실상 신규 계산 로직이 거의 필요 없다.
- `ACCUMULATE`(QQQM, IQQ)는 이 테이블에 최소한만 걸치고 실제 로직은 기존
  `investment_plans`/`investment_schedules`/`schedule_occurrences` 기능
  (v1.2에서 만든 반복매수 엔진)을 확장해서 처리한다(3.3 참고).
- `NONE`(JEPQ, Rise위클리200커버드)은 `trade_plans` 행 자체를 안 만들거나,
  매매신호 없이 평가금액·누적분배금만 표시하는 최소 행 하나만 둔다.

### 2.2 기존 테이블과의 관계
- `post_entry_high_price`(instruments 테이블)는 그대로 두고 건드리지 않는다
  -- 기존 ATR 기반 트레일링스탑(스펙 8.4)이 계속 이걸 쓴다. 이번 기능은
  완전히 별도의 `peak_price_since_trigger`를 쓴다(의미가 다르므로 절대
  같은 필드를 공유하면 안 됨 -- NVDA처럼 4년 전 매수한 종목은 "매수 이후
  최고가"와 "트리거 도달 이후 최고가"가 전혀 다르다).
- `notification_outbox`는 그대로 재사용, `notification_type`에 새 값만
  추가(TRIGGER_APPROACH/TRIGGER_REACHED/TIER_FIRED/PLAN_REVIEW/DATA_STALE).

## 3. 백엔드 로직

### 3.1 신규 순수함수 모듈: `services/trade_plan_engine.py`
`signal_engine.py`와 동일한 스타일(부작용 없는 순수함수, DB/HTTP 모름)로:

```python
def evaluate_trail_plan(plan, tiers, current_price, atr, data_status) -> TrailPlanResult
```
- 상태 전이: `ARMED` -> (거리 <= max(2%, 1*ATR%)) -> `TRIGGER_APPROACH`(종목당
  1회만) -> (트리거 통과, confirm_mode에 따라 종가/장중 확인) -> `TRIGGER_REACHED`
  -> `TRAIL_HOLD` <-> tier 발동
- **데이터 장애 시 상태 보존**: `data_status`가 STALE/API_ERROR면 아무 전이도
  안 하고 마지막 상태 그대로 반환 -- 지난주에 고친 신호소실 방지 버그와 정확히
  같은 원칙을 여기도 적용한다(같은 실수를 두 번 하지 않기 위해 처음부터
  이 원칙을 코드에 박아넣는다).
- tier 발동은 종가 확정 기준이 기본값(`confirm_mode='CLOSE'`), 장중 이탈은
  예비 알림만 보내고 `fired_at`을 찍지 않는다.
- 같은 tier가 같은 사이클에서 중복 발동하지 않도록 `fired_at` 체크.

### 3.2 오케스트레이션
`portfolio_service.py`에 `evaluate_trade_plans(conn, instrument_id)` 추가,
아래 두 지점에서 호출:
- `worker.py`의 5분 정규장 폴링 (`poll_quotes` 이후)
- `refresh_all_quotes_now()`(수동 "주가 갱신" 버튼) 이후

KODEX 200처럼 `trade_plan_instruments`에 종목이 여러 개 연결된 계획은,
가격/ATR은 대표 종목(kis_code가 같으므로 아무 쪽이나) 1회만 조회하고,
tier 발동 시에는 연결된 각 instrument의 **개별 보유수량**을 기준으로
매도수량을 계산해서 계좌별로 따로 알린다(예: "908주 계좌는 363주 매도
권고, 367주 계좌는 147주 매도 권고").

### 3.3 ACCUMULATE 유형 -- 기존 기능 확장
완전 신규 기능이 아니라 `services/schedule_engine.py`(v1.2에서 만든 반복매수
엔진)에 조건부 게이트를 하나 얹는다:
- 기존: 지정된 요일/주기마다 무조건 매수 후보 생성
- 확장: 여기에 "VXN(나스닥 변동성지수) 조건" 같은 선택적 필터를 추가 --
  조건 미충족이면 그날은 매수 후보를 만들지 않고 건너뜀
- **리스크**: VXN을 무료로 안정적으로 받아올 수 있는 소스가 아직 확인 안 됨
  (조사 필요, 5절 참고). 확인 전까지는 조건 없이 기존 스케줄 그대로 동작하게
  하고, 소스가 확인되면 필터를 켜는 순서로 진행한다.

### 3.4 알림
`services/notification_service.py`의 기존 재시도/Outbox 패턴 그대로 재사용.
메시지 포맷만 추가:
```
[TRIGGER_APPROACH] QQQ가 트리거 710달러까지 2.1% 남았습니다.
[TRIGGER_REACHED]  QQQ가 트리거 710달러에 도달, 트레일링 감시를 시작합니다.
[TIER_FIRED]       QQQ 1차선(최고가 대비 -1.2%) 도달. 40% 매도 권고.
                    사용자 기준선 712.80 / ATR 권고선 708.40 (참고 표시)
[PLAN_REVIEW]       QQQ 계획의 재검토일이 지났습니다. 계획을 유지할지 확인하세요.
```

## 4. API 및 프론트엔드

### API (`backend/atrsite/api/trade_plans.py` 신규)
```
GET    /api/v1/trade-plans                 목록(종목별 필터 가능)
POST   /api/v1/trade-plans                 신규 생성(연결 종목 배열 포함)
PATCH  /api/v1/trade-plans/{id}            수정(이력은 자동으로 history에 적재)
DELETE /api/v1/trade-plans/{id}            취소(CANCELLED로 상태 변경, 실삭제 안 함)
GET    /api/v1/trade-plans/{id}/history    변경 이력 조회
```

### 프론트엔드
- 신규 화면 `trade-plans.html`(기존 독립 페이지 패턴 -- withdrawals.html,
  investment-schedule.html과 동일 구조)
- 종목 상세 화면에 "매매계획" 요약 카드 추가(활성 계획이 있으면 트리거가/
  현재상태/다음 단계 표시)
- 계획 생성 폼: 유형 선택 -> 유형별 필드만 노출(TRAIL이면 트리거가+단계
  목록 편집기, ACCUMULATE면 스케줄 화면으로 연결, LIQUIDATE_NOW면 진행률만)
- 사용자선/데이터권고선 동시 표시 위젯(5단계에서 논의한 그 형태)

## 5. 구현 전 확정 필요 (사용자 확인 대기)

### 5.1 확정 완료

**QQQ 1차 익절 계획 (2026-08-07 확정)**:
```
대상 baseline_quantity: 427주 (계획 확정 시점 스냅샷, 이후 VXN 적립분 제외)
트리거 가격: $717 (ABOVE)
최고가 추적 시작: 계획 확정 이후 $717 도달 시점부터
1차 매도선: 활성화 이후 최고가 x 0.9875 (= 최고가 대비 -1.25%)
1차 매도비율: baseline_quantity의 40% = 171주
매도 후 잔여: 256주 (baseline 기준, 실보유수량과는 별개)
확인방식: 종가 기준(장중 이탈은 예비알림만)
2차 계획: 미확정
```
계산 검증 완료(717x0.9875=708.04, 720x0.9875=711.00, 725x0.9875=715.94,
730x0.9875=720.88, 740x0.9875=730.75, 171/427=40.05%=40%). 이 사례를
Phase 1 구현의 기준 테스트 케이스로 쓴다.

### 5.1.2 확정 완료 -- KODEX 200 (2026-08-07)

```
대상 baseline_quantity: 1,275주 (Kodex 200 908주 + kodex 200 New 367주 합산,
  trade_plan_instruments로 두 instrument_id 모두 이 계획에 연결)
트리거 가격: 115,000원 (ABOVE)
1차 매도선: 활성화 이후 최고가 x 0.975 (최고가 대비 -2.5%, 고정 %)
1차 매도비율: baseline_quantity의 40% = 510주
2차 매도선: 활성화 이후 최고가 x 0.94 (최고가 대비 -6.0%, 고정 %)
2차 매도비율: 잔여 60% = 765주 (전량)
확인방식: 종가 기준(장중 이탈은 예비알림만)
재진입: allow_auto_reentry=false, 청산 이후 국내시장 신규 진입 보류
```

**설계 판단 근거(KIS 실데이터 검증 후 결정, 2026-08-07)**: 현재 ATR(14)=9.20%는
2026년 7월 말 국내 ETF 시장 전반의 실제 급락(레버리지 ETF -40% 등 언론보도로
교차확인됨)으로 일시적으로 부풀어 있는 값이라 그대로 곱하면 밴드가 너무
넓어진다(0.5~1.25 ATR 적용 시 -4.6%~-11.5%). 그래서 라이브 ATR 배수가 아니라
120일 ATR% 중앙값(4.68%)을 참고치로만 삼아 고정 %(-2.5%/-6.0%, 각각 정상
ATR의 0.53배/1.28배)로 못박았다 -- "115,000원 이후 국내주식 전량 정리"라는
목적에 맞게 변동성이 커져도 밴드가 함께 넓어지지 않게 하기 위함.

### 5.2 아직 확정 필요

1. QQQ 2차 계획(잔여 256주 처리 방식) -- 미확정
2. `confirm_mode` 기본값을 전 종목 공통 `CLOSE`로 할지, 종목별로 다르게
   할지
3. VXN 데이터 소스 확보 여부(3.3 리스크) -- QQQ 적립 계획에 이미 VXN>=28
   조건이 실제로 쓰이고 있음이 확인됨(2026-08-07). 소스 확보가 Phase 3뿐
   아니라 QQQ 적립·매도 로직의 정확성에도 직접 영향을 주므로 우선순위를
   높여야 함

## 6. 테스트 계획

- `trade_plan_engine.py` 순수함수 단위테스트: 상태전이 전 구간, tier 중복
  발동 방지, STALE/API_ERROR 시 상태 보존(기존 신호소실 방지 테스트와
  동일 패턴), TRIGGER_APPROACH가 종목당 1회만 발생하는지
- 여러 instrument에 연결된 계획(KODEX 200 두 계좌) 통합테스트: 계좌별
  매도수량이 각자의 보유수량 기준으로 따로 계산되는지
- 계획 이력(history) 테스트: 수정할 때마다 새 버전이 쌓이고 이전 값이
  안 사라지는지
- 전체 회귀 재실행(현재 373개 + 신규분)

## 7. 배포 순서 (이 프로젝트의 기존 관례 그대로)

1. `feature/trade-plans` 브랜치에서 개발
2. Phase 1(TRAIL, QQQ/NVDA/KODEX200 커버) 우선 완료 -- 가장 시급하고 설계도
   이미 끝남
3. Phase 2(LIQUIDATE_NOW, QLD) -- Phase 1과 거의 동시 진행 가능, 작업량 작음
4. Phase 3(ACCUMULATE, QQQM/IQQ) -- VXN 소스 조사 결과에 따라 범위 조정
5. Phase 4(NONE 표시, JEPQ 등) -- 아주 작은 작업, 마지막에
6. 로컬 pytest 전체 통과 확인 -> main 병합 -> VPS 배포 -> history.txt 기록
   (이 세션 내내 적용된 "로컬 완료 후 자동 배포" 원칙 그대로)

## 8. 이 계획서가 재사용하는 기존 코드 자산 (신규 개발 최소화)

- `signal_engine.py`의 순수함수 스타일 그대로 `trade_plan_engine.py`에 적용
- STALE/API_ERROR 시 상태 보존 원칙(2026-08-06에 이미 구현) 그대로 재사용
- `instruments.py` repository의 버저닝 패턴(version+created_at으로 이력
  적재) 그대로 `trade_plan_history`에 적용
- `investment_plans`/`investment_schedules`/`schedule_occurrences`(v1.2)
  확장으로 ACCUMULATE 처리 -- 새 스케줄 엔진을 또 만들지 않음
- `notification_outbox`/`notification_service.py`의 재시도 Outbox 패턴 그대로
- ATR(14) 계산은 기존 `atr_engine.py` 그대로 재사용, 장기 이평선(SMA200 등)은
  이번 범위에 없으므로 daily_bars 저장 경로를 새로 만들 필요 없음
