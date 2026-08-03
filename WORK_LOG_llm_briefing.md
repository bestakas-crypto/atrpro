# LLM 매크로 브리핑 기능 -- 작업 로그 (커밋 안 함, 검토 대기 중)

시작: 2026-08-03. 사용자 외출 중 로컬 작업, 3시간 후 PC 전원 꺼질 수 있음
-- 각 체크포인트마다 동작 가능한 상태로 저장하며 진행.

**2026-08-03 귀가 후 업데이트**: 아래 "귀가 후 확인해주실 것" 3개 항목에
대한 답을 받아서 전부 반영 완료. 실제 키까지 넣고 라이브로 전체 파이프라인
검증함(더미 아님, 진짜 Claude 웹서치 응답). 이 섹션 아래는 외출 전 작성된
원래 로그이고, 최신 진행 상황은 맨 아래 "2차 작업(귀가 후)" 섹션 참고.

## 범위 (지난 대화에서 합의된 축소판)

1. 버튼 하나("오늘 브리핑") -- 8버튼 아님
2. 객관적 수치: QQQ/Kodex200 현재가(이미 KIS로 보유), 원/달러(이미 fx.rates),
   VXN(신규 -- Yahoo Finance 비공식 quote 엔드포인트, 실패시 UNAVAILABLE)
3. 뉴스: 자체 파이프라인 안 만들고 Claude API의 web_search 도구에 위임
4. "내 규칙 점검": VXN 등급만(채권/크레딧 트리거는 아직 미확정이라 보류)
5. TTS/복수LLM검토/경제캘린더: 전부 이번 범위 밖
6. LLM 클라이언트: C:\mmean\llm\llm_chain.py 패턴 참고해서 새로 작성(직접
   import 안 함, 키/로그/DB 완전히 분리)

## 귀가 후 결정 필요한 것 (내가 임의로 못 정한 것들)

- [ ] **VXN 등급 구간값** -- "VXN이 얼마 이상이면 어느 등급"인지 정확한
      숫자를 모름. 코드에는 자리만 만들어두고 실제 값은 안 채움(config로
      분리해뒀으니 나중에 값만 넣으면 됨).
- [ ] **LLM API 키 소스** -- mmean의 키를 그대로 strpro .env에 옮길지,
      새로 발급받을지. 값은 내가 채우지 않음(이번 세션 내내 지켜온 원칙).
      .env.example에 항목만 추가해둠.
- [ ] **web_search 도구가 실제로 잘 동작하는지** -- 키가 없어서 라이브
      테스트를 못 했음. 더미 모드로 구조만 확인.
- [ ] 채권/크레딧(10년물, HY OAS) 규칙 트리거를 추가할지 -- 지금은 아예
      안 만들어둠.

## 진행 상황

- [x] `backend/atrsite/config.py` -- ANTHROPIC_API_KEY/OPENAI_API_KEY/
      GEMINI_API_KEY/모델명 설정 추가 (값은 안 채움, .env.example에도
      추가 예정)
- [x] `backend/atrsite/adapters/llm_client.py` 신규 -- Claude→GPT→Gemini
      폴백, 더미모드, web_search 도구(Claude만) 지원. mmean 패턴 참고,
      직접 import 안 함
- [x] `tests/test_llm_client.py` -- 더미모드/필드파싱/폴백/전체실패
      5개 테스트, 전부 통과
- [x] `backend/atrsite/adapters/market_index_client.py` -- VXN 등 지수 조회
      (Yahoo Finance 비공식 엔드포인트, 실패시 UNAVAILABLE). **실전으로 VXN
      직접 조회해서 정상 동작 확인함(26.0, 전일대비 -8.4%)**
- [x] `tests/test_market_index_client.py` -- 3개 테스트 통과
- [x] `backend/atrsite/schema.py` -- analysis_results 테이블 추가(신규
      테이블이라 마이그레이션 불필요, CREATE TABLE IF NOT EXISTS로 충분)
- [x] `backend/atrsite/repositories/analysis.py` -- save_result/get_result/
      get_latest_result
- [x] `backend/atrsite/services/analysis_service.py` -- Macro Snapshot 조합
      (보유종목 현재가+등락률/원달러환율/VXN) + LLM 프롬프트(10항목 고정
      구조, "제공된 수치만 써라" 원칙 강제) + 5분 캐시. VXN 등급 판정은
      구간값 미확정이라 이번 버전엔 안 넣음(snapshot.vxn.grade는 항상 None)
- [x] 백엔드 테스트 6개 추가, 전체 157개 통과
- [x] API 엔드포인트 -- `backend/atrsite/api/analysis.py`
      (POST /api/v1/analysis/run?force=, GET /api/v1/analysis/latest),
      main.py에 라우터 등록. 별도 상시 프로세스 없이 동기 처리(개인용
      단일 사용자라 충분).
- [x] API 레벨 테스트 3개 추가, 전체 160개 통과
- [x] 프런트엔드:
      - `frontend/briefing.html` + `js/briefing.js` + `css/briefing.css` --
        report.html처럼 메인 SPA와 분리된 독립 페이지
      - "오늘 브리핑" 큰 버튼을 종목 목록 화면 맨 위(가장 눈에 띄는 자리)에
        추가 -- `<a href="briefing.html" target="_blank">`라 팝업 차단
        걱정 없음(report.html 버튼 만들 때 겪었던 문제를 처음부터 피함)
      - 처음 열면 캐시된 최신 브리핑을 바로 보여주고, 없으면 "오늘 브리핑
        만들기" 버튼만 노출. "다시 만들기"는 강제 재생성(force=true)
      - api-client.js에 runBriefing/getLatestBriefing 추가
- [x] `.env.example`에 ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY
      항목 추가(값은 안 채움)
- [x] service-worker.js 캐시 v8 -> v9

## 브라우저 실측 검증 (로컬, 더미 LLM 모드)

- "오늘 브리핑" 버튼 클릭 -> briefing.html 로드 -> "오늘 브리핑 만들기"
  클릭 -> 실제로 VXN을 라이브로 조회하고(26.0, 실전 확인된 값) 보유종목
  현재가/등락률(kodex 200, QQQ)과 원달러 환율까지 스냅샷에 정확히 담기는 것
  확인
- 캐시 동작(연속 클릭 시 같은 id 재사용) / 강제 재생성(다른 id 생성) /
  새로고침 시 마지막 결과 즉시 표시 -- 전부 실제 HTTP로 검증함
- LLM 키가 없어서 "실제 브리핑 문장"까지는 확인 못 했음 -- 더미 응답
  문구만 확인. **키를 넣으면 바로 실제 브리핑이 나올 구조**임

## 최종 상태

- pytest 160개 전부 통과 (신규 20개: llm_client 5, market_index_client 3,
  analysis_service 6, analysis API 3, 기존 dashboard atr 테스트 등 포함)
- git 커밋/푸시/VPS 배포는 전혀 안 함(지시대로 검토 대기)
- 작업 파일들은 전부 `git status`로 확인 가능한 상태(uncommitted)

## 귀가 후 확인해주실 것 (다시 정리)

1. **VXN 등급 구간값** -- 지금은 VXN 원값만 스냅샷에 넣고 등급 판정은
   전혀 안 함(`snapshot.vxn.grade`는 항상 null). 구간(예: "VXN 20 이하 =
   QQQM, 20~30 = QQQ, 30 이상 = IQQ" 같은 실제 기준)을 알려주시면 바로
   반영 가능.
2. **LLM API 키** -- mmean 것 재사용 vs 새로 발급, 아직 결정 안 됨.
   `.env`에 ANTHROPIC_API_KEY만 채워도 동작(Claude 우선, 나머지는 폴백용
   옵션).
3. **web_search 도구 실제 동작 여부** -- 키가 없어서 라이브로 못 봄. 키
   넣고 "다시 만들기" 한 번 눌러보면 바로 확인 가능.
4. 이번 버전에서 일부러 안 넣은 것: TTS(음성), 복수 LLM 검토, 채권/크레딧
   트리거, 경제일정 캘린더, 뉴스 자체 파이프라인(웹서치로 대체) -- 전부
   "천천히" 방침대로 다음 단계로 미룸.

검토 후 문제 없으면 커밋 메시지는 이런 식으로 준비해뒀다가 지시만 주시면
바로 진행하면 될 것 같음: "LLM 매크로 브리핑 기능 추가(오늘 브리핑 버튼,
VXN/보유종목/환율 스냅샷 + Claude 웹서치 기반 브리핑)".

---

## 2차 작업 (귀가 후, 2026-08-03) -- 답변 3개 반영 + 실키 라이브 검증

### 답변 1: VXN 그리드 매수 규칙 (등급 아니라 실제 매매 규칙이었음)

"등급"이 아니라 사용자의 실제 그리드 매수법 그대로였음: 0~15 IQQ 10주,
15~24 QQQM 1주, 25~30 QQQ 1주, 30~40 상황체크(자동매수 안 함). 24~25 틈과
40 이상은 사용자가 정의 안 했다고 판단해서 임의로 채우지 않고 `UNDEFINED`
상태로 남김.

- [x] `analysis_service.py`에 `VXN_GRID_RULES` + `check_vxn_grid_rule()`
      추가 -- half-open 구간 매칭, 경계값 8개 케이스 전부 테스트로 검증
- [x] `snapshot.vxn.grid_rule`에 반영 (기존 항상 null이던 `grade` 필드 대체)
- [x] briefing.html에 "오늘의 그리드 매수 (VXN 26.0)" 카드 신규 추가 --
      LLM이 쓴 문장과 시각적으로 분리(호박색 박스), Python이 확정한 값임을
      명확히 표시. 브라우저에서 실제 렌더링 확인함.
- [x] SYSTEM_PROMPT에 "이 규칙 결과는 이미 확정됐으니 재계산/재판단하지
      말고 그대로 설명만 하라"고 명시 -- 실제 응답에서도 LLM이 규칙을
      재계산하지 않고 그대로 인용하는 것 확인함(8번 항목)

### 답변 2: 2단계 LLM 파이프라인 (Gemini 객관조회 -> Claude/GPT 판단)

"객관적이고 판단 필요없는 건 제미나이, 판단 필요한 건 GPT나 클로드"라는
답변에 따라 `llm_client.ask()`에 `preferred_provider` 파라미터 추가하고
2단계로 재구성:

- **1단계**: Gemini 우선 + web_search(`google_search` 그라운딩 도구) --
  SOXX/MU/US10Y/US30Y/JP10Y/USD-KRW/JPY-KRW(사용자 지정) + DXY/VIX(내가
  추가) 조회, 순수 객관적 사실만, 해석 금지
- **2단계**: Claude 우선(GPT 폴백) -- 1단계 결과 + Python 스냅샷을 입력으로
  받아 해석/시나리오/무효화조건까지 포함한 최종 브리핑 작성
- [x] `_call_gemini`에 `use_web_search` 지원 추가(google_search 도구)
- [x] 테스트 2개 추가(우선순위 라우팅, google_search 도구 페이로드 검증)
- [x] `run_briefing()`을 2단계 흐름으로 재작성, 더미모드는 중복 호출
      방지용 단일 경로 유지
- [x] 테스트로 라우팅 순서(1단계=gemini+웹서치, 2단계=claude) 검증

### 답변 3: 키 업로드 파일 제공 + 실키 반영

- [x] `llm_keys.env.new` 템플릿 파일 만들어서 메모장으로 열어드림 (기존
      KIS/텔레그램 값 채울 때와 동일한 방식 -- 값은 내가 직접 안 읽음)
- [x] 사용자가 입력 완료 후, 값은 안 읽고 존재여부/길이만 확인하는 스크립트로
      `.env`에 병합(ANTHROPIC/OPENAI/GEMINI 3개 다 채워주심). 임시파일 삭제.

### 실키로 라이브 검증하면서 발견 + 수정한 실제 버그 3건

1. **Gemini 모델명 오래됨** -- 기본값 `gemini-1.5-flash`가 404(신규
   사용자에게 더 이상 제공 안 함). `ListModels`로 실제 사용 가능한 모델
   확인해서 `gemini-flash-latest`로 교체(향후 모델 교체시에도 자동 추적).
2. **[보안] Gemini API 키가 URL 쿼리스트링(`?key=`)에 있어서 요청 실패시
   `uvicorn.log`에 키가 평문으로 남음** -- 실제로 로그에 키 값이 그대로
   찍히는 걸 확인함(내가 tail로 보게 됨). `x-goog-api-key` 헤더 방식으로
   변경해서 키가 URL에 아예 안 들어가게 고침. 이미 로그에 찍힌 키는
   redact 처리함. **키 노출이 있었으니 여유되실 때 Google AI Studio에서
   해당 Gemini 키 재발급(rotate) 권장** -- 로컬 파일에만 남았던 거라
   외부 유출 가능성은 낮지만, 원칙상 한번 평문 노출된 키는 교체하는 게
   안전.
3. **Claude 웹서치 응답이 max_tokens(2000)에 걸려 text 블록 없이 잘림** --
   1단계처럼 여러 번 검색하는 호출은 thinking+tool_use 블록만으로 예산을
   다 쓸 수 있음(실제로 `stop_reason: max_tokens`로 재현됨). 웹서치 켠
   호출만 `WEB_SEARCH_MAX_TOKENS=8000`으로 늘려서 해결.

### 실키 라이브 최종 검증 결과

- Gemini는 무료 티어 쿼터를 테스트 중에 다 써서(`429 RESOURCE_EXHAUSTED`)
  1단계가 매번 Claude로 폴백됨 -- 폴백 자체는 정상 동작 확인. **1단계가
  실제로 Gemini로 도는 것 자체는 아직 라이브로 확인 못 함**(쿼터 리셋
  후 재확인 필요, 또는 유료 플랜 전환 고려).
- Claude가 1+2단계를 전부 처리한 최종 결과물 품질은 좋음: VXN 그리드
  규칙을 재계산 안 하고 그대로 인용, 뉴스는 실제 날짜/출처 명시(CNBC
  2026-07-31 등), 확인 안 된 수치(US10Y/US30Y/JP10Y)는 추측하지 않고
  "확인 안 됨"으로 명시 -- 핵심 원칙("LLM은 수치를 스스로 찾지 않는다")이
  실전에서도 지켜짐.
- pytest 전체 통과(`test_llm_client.py` 7개, `test_analysis_service.py`
  17개 포함) -- 재확인 완료.
- git 커밋/푸시/VPS 배포는 여전히 안 함(검토 대기).

### 3차 작업 (2026-08-03, 폴백 순서 수정)

사용자 지시: "제미나이 토큰 다 쓰면 다음엔 GPT, 다음엔 클로드 순으로" --
`_PROVIDER_ORDER` 단순 재배치 방식으론 이 요구를 못 만족해서(기존 방식은
preferred를 앞으로 당기고 나머지는 기본순서 claude→gpt→gemini 유지라서
gemini 실패시 claude가 먼저 옴), `_FALLBACK_CHAINS` 딕셔너리로 단계별
전용 순서를 명시:
- `gemini`(1단계, 객관조회): gemini → gpt → claude (클로드는 2단계 판단용
  으로 아껴둠)
- `claude`(2단계, 판단): claude → gpt → gemini (기존과 동일, 안 바뀜)

새 테스트(`test_ask_gemini_fails_over_to_gpt_before_claude`)로 순서 검증,
mock이라 실제 쿼터는 안 씀. 전체 174개 통과.

DeepSeek 추가 요청(객관적 수치 조회용, 가격 저렴)에는 이견 제시함: DeepSeek
공식 API는 Gemini의 google_search나 Claude의 web_search_20250305 같은
네이티브 웹서치 도구가 없어서, 1단계(실시간 조회)에 넣으면 학습 데이터
기반으로 추측하게 됨 -- "LLM이 수치를 스스로 찾지 않는다"는 핵심 원칙에
위배. 대신 2단계(이미 수집된 데이터로 해석만 하는 단계, 검색 불필요)의
저렴한 폴백 후보로는 적합하다고 답변, 사용자 확답 대기 중(아직 구현 안 함).

### 4차 작업 (2026-08-03, DeepSeek 자리 추가)

사용자 지시: "일단 딥시크 자리는 만들어놔" -- DeepSeek는 자체 웹서치가
없어서(위에서 확인) 1단계엔 못 넣고, 2단계(판단, 검색 불필요) 체인에
GPT 다음/Gemini 이전 자리로 넣음:
- `config.py`: `DEEPSEEK_API_KEY`, `LLM_MODEL_DEEPSEEK`(기본값
  `deepseek-v4-flash` -- 사용자 제공 가격표 기준 경제형 모델. 최후
  폴백용이라 pro까지는 필요 없다고 판단)
- `llm_client.py`: `_call_deepseek()` 추가(OpenAI 호환 chat completions,
  GPT와 동일 파싱 구조). `is_dummy_mode()`에도 반영(넷 다 비어야 더미모드).
  `_FALLBACK_CHAINS` 갱신:
  - `gemini`(1단계): gemini → gpt → claude (변경 없음, 딥시크 여전히 제외)
  - `claude`(2단계): claude → gpt → **deepseek** → gemini
  - `gpt`: gpt → claude → **deepseek** → gemini
  - `deepseek`: deepseek → gpt → claude → gemini (신규)
- `.env.example`에 `DEEPSEEK_API_KEY=` 항목 추가(값은 안 채움 -- 아직
  실제 키를 안 주셨으니 지금은 계속 더미/폴백 미사용 상태)
- 테스트 2개 추가(딥시크 단독 파싱, 2단계에서 클로드+GPT 둘 다 실패시
  제미나이보다 딥시크가 먼저 시도되는지) -- 전체 176개 통과

**2026-08-03 추가 업데이트**: DEEPSEEK_API_KEY 실키 받아서 `.env`에 병합
완료(기존 방식대로 값은 안 읽고 존재/길이만 확인). `https://api.deepseek.
com/chat/completions`에 `deepseek-v4-flash`로 직접 curl 검증 -- HTTP 200,
응답 스키마(`choices[0].message.content`)도 코드가 파싱하는 필드와 정확히
일치함 확인(참고로 flash 모델도 `reasoning_content` 필드를 같이 주는데,
우리 파서는 `content`만 읽어서 문제없음). 전체 파이프라인(Claude+GPT
둘 다 실패해야 도달)까지는 강제로 안 태워봤지만, API 자체 동작은 검증
완료.

### DeepSeek 가격 문의에 대한 답변 (얼마나 싼지)

사용자가 공유한 가격표 기준 deepseek-v4-flash: 입력 $0.14/캐시히트
$0.0028/출력 $0.28 (평시, 1백만 토큰당). 참고로 이미 쓰고 있는 다른
공급자들과 비교하면(대략적인 시장 시세 기준, 실시간 최신가는 아님):
- Claude Sonnet 계열: 출력 기준 1백만 토큰당 대략 $15 안팎 -- deepseek-v4
  -flash 대비 대략 50배 이상 비쌈
- GPT-4o-mini: 출력 기준 대략 $0.60/1백만 토큰 -- deepseek-v4-flash가
  그것보다도 더 저렴한 축
- deepseek-v4-pro(출력 $0.87)도 Claude Sonnet 대비 여전히 10~20배 저렴

즉 "거의 공짜 수준"이라고 봐도 될 정도로 저렴함. 피크타임(평일 09~12시,
14~18시 KST) 2배 할증돼도 여전히 Claude/GPT보다 훨씬 쌈. 이 앱은 브리핑을
하루 몇 번 누르는 정도의 저빈도 사용이라 피크타임 할증 자체가 체감상
의미 없는 수준일 것으로 판단(딥시크가 최후 폴백이라 자주 호출되지도 않음).

### 5차 작업 (2026-08-03, 폴백 순서 재조정 -- 클로드를 최후 폴백으로)

사용자 지시: "제미나이 - GPT - 클로드 순으로 진행하라.. 클로드가 더
비싸지않나?" -- 2단계(판단)도 이제 클로드를 맨 앞이 아니라 **최후
폴백**으로 바꿈. 그런데 이러면 1단계/2단계가 둘 다 "gemini"로 시작하게
되는데, 기존엔 `_FALLBACK_CHAINS`를 provider 이름으로 키를 잡아서
("gemini" 키 하나에 체인 하나만) 표현이 불가능했음 -- 그래서 provider
이름이 아니라 **단계 이름**으로 키를 바꾸는 리팩터링을 함:

- `llm_client.ask()`의 `preferred_provider` 파라미터 -> `chain`으로 이름
  변경 (값도 provider 이름이 아니라 `"stage1_search"` / `"stage2_judgment"`)
- `_FALLBACK_CHAINS`:
  - `stage1_search`(1단계, 객관조회): gemini → gpt → claude (안 바뀜)
  - `stage2_judgment`(2단계, 판단): **gemini → gpt → deepseek → claude**
    (기존 claude → gpt → deepseek → gemini에서 클로드/제미나이 위치를
    맞바꿈. DeepSeek는 "검증 안 됨/자리만 만들어둔 것"이라 여전히 클로드
    바로 앞자리 유지 -- 이 배치는 내 판단이라 다르게 원하면 알려주면 됨)
- `analysis_service.py`의 두 `llm_client.ask()` 호출도 `chain=` 인자로 갱신
- DeepSeek가 웹서치 없다는 걸 사용자가 물어봐서, api-docs.deepseek.com을
  WebFetch로 직접 확인함 -- Gemini/Claude처럼 서버가 알아서 검색해주는
  내장 도구 없음, OpenAI식 범용 함수호출(Tool Calls)만 있고 실제 검색
  실행은 개발자가 직접 붙여야 함. 그래서 DeepSeek는 여전히 1단계엔 못 씀.
- 테스트 갱신: `preferred_provider=` 쓰던 테스트들을 `chain=`으로 바꾸고,
  2단계 전용 신규 테스트(`test_ask_stage2_tries_gemini_first_and_claude
  _last`)로 gemini→gpt→deepseek→claude 순서 + 클로드가 한 번도 안 불리는
  것까지 검증
- **버그 발견+수정**: `test_analysis_service.py`/`test_api.py`의 더미모드
  강제 픽스처가 anthropic/openai/gemini 키만 비우고 deepseek_api_key는
  안 비웠음 -- DeepSeek 실키를 `.env`에 넣은 이후로 `is_dummy_mode()`가
  더 이상 True를 반환하지 않아 더미모드 테스트 4개+API 테스트 2개가
  깨짐. 픽스처들에 deepseek_api_key 클리어 추가해서 수정
- 전체 176개 통과, 서버 재기동 완료(실제 API로 재확인은 안 함 -- 라우팅
  로직은 mock 테스트로 충분히 검증됐다고 판단해서 실비용 발생하는 호출은
  생략)

### 6차 작업 (2026-08-03, UI 정리 + 실서버 배포)

사용자 지시: "그렇게 크게 하지말고.. 빨간색 칠한 부분에 아이콘 형태로
놔두면 될거 같아.. 그리고 서버 업데이트 진행해." -- 실기기 스크린샷 보고
헤더 우측(보고서 📄 아이콘 옆) 위치를 정확히 짚어줌.

- `frontend/index.html`: 목록 화면 맨 위 큰 버튼(`.btn-briefing-launch`)
  제거, 헤더 `.header-actions`에 🌐 아이콘 링크로 이동(보고서 📄 왼쪽)
- `frontend/css/style.css`: 이제 안 쓰는 `.btn-briefing-launch` 스타일
  삭제
- `service-worker.js` 캐시 v9 -> v10
- 전체 176개 통과 확인 후 커밋(`27aebb2`) + `git push origin main`
  (자동모드 분류기가 최초 1회 push를 차단해서 사용자에게 재확인받고
  진행함) + VPS 배포:
  - `git pull`(strpro 계정, atrsite 소유 repo라 "dubious ownership"
    에러 나서 `safe.directory` 1회 등록 후 `sudo -u atrsite git pull`)
  - LLM 키 4개(ANTHROPIC/OPENAI/GEMINI/DEEPSEEK) 로컬 `.env`에서 값을
    직접 안 읽고 파이프로 원격 `.env`에 append(길이만 확인, 값 확인 안 함)
  - `atrsite-web` 서비스만 재시작(`analysis_results` 테이블은 CREATE
    TABLE IF NOT EXISTS라 자동 마이그레이션, worker는 무관)
  - 실서버(`http://107.173.91.254`)에서 index/briefing.html 200, 헤더에
    🌐 아이콘 실제로 뜨는 것 확인, API 인증 게이트(401)도 정상 동작 확인

**최종 상태**: 로컬/실서버 둘 다 배포 완료. 실서버 `.env`에 LLM 키 4개
전부 채워져 있어서 다음에 "오늘 브리핑" 아이콘 누르면 바로 실제 브리핑이
생성됨(더미 아님).

### 다음에 결정해주실 것

1. **Gemini 무료 쿼터** -- 지금은 테스트로 다 써서 1단계가 매번 Claude로
   폴백 중. 시간 지나면 자동 리셋되는지, 유료 전환할지는 사용자 판단.
   (기능 자체는 폴백 덕분에 지금도 정상 동작함 -- 급한 문제 아님)
2. **Gemini 키 재발급 여부** -- 위 버그 2번 참고. 강제 아니고 권장사항.
3. VXN 24~25 틈, 40 이상 구간은 여전히 `UNDEFINED`로 남겨둠(임의로 안 채움)
   -- 나중에 실제 상황 오면 규칙 정해서 알려주시면 반영.

### 7차 작업 (2026-08-03, 실서버 잘림 버그 긴급 수정)

사용자가 실기기 스크린샷으로 "브리핑 윗부분이 안 나오고 아랫부분만 보인다,
스크롤되게 해달라"고 제보. CSS/스크롤 문제로 짐작하기 전에 실서버에 SSH로
들어가서 DB에 저장된 원문 자체를 직접 확인(`sudo .../venv/bin/python3 -c
"...sqlite3..."`, API 키 없이도 서버 파일시스템 직접 조회) -- 결과:
**저장된 result_text가 161자로 진짜 잘려있었음**(CSS 문제 아니라 생성
단계에서 이미 잘림). "Snapshot 데이터)." 로 시작해서 "6) 하락 요인" 항목
중간까지만 있고 그 이전/이후 다 없음.

원인: 종목탐구 20항목 응답이 잘리던 것과 완전히 같은 버그 클래스 --
웹서치 없는 호출의 기본 예산(MAX_TOKENS=2000)이 10항목 전체를 못 채움.
이번에 처음 실제로 터진 이유는, 이전 세션에서 "클로드가 비싸니 제미나이
-GPT-클로드 순으로" 요청에 따라 2단계 1순위를 제미나이로 바꿨는데, 같은
내용이라도 제미나이 토크나이저가 한국어를 더 많이 토큰으로 소모하는 것
으로 보여 2000 토큰 예산에 더 쉽게 걸림.

수정: analysis_service.py의 2단계 호출에 `max_tokens=8000` 명시(종목탐구
때와 동일 값). 로컬에서 강제 재생성 -> 1)~10) 항목 전체 정상 생성(제미나이,
2229자) 확인.

**배포 방식 관련 중요 사항**: 로컬 git에는 아직 검토 전인 종목탐구 커밋이
이 수정보다 먼저 쌓여있어서, `git push` -> 서버 `git pull`을 하면 검토 안
된 종목탐구까지 같이 배포돼버림(원래 지시 위반). 그래서 이번엔 git을
안 거치고 **수정된 파일(analysis_service.py) 하나만 scp로 직접 서버에
올리고 서비스만 재시작**하는 방식으로 이 버그만 정확히 고침. 서버 git
저장소 상태는 그대로(여전히 종목탐구 이전 커밋 기준) -- 나중에 종목탐구
전체를 배포할 때는 정상적으로 git pull하면 이 파일도 자동으로 같이
반영됨(로컬 git에는 이미 커밋돼 있으므로).

CSS 스크롤 자체는 문제 없었음(로컬 크롬으로 확인, body/html overflow
전부 정상) -- 애초에 콘텐츠가 짧아서 스크롤할 필요가 없었을 뿐, 스크롤
기능 자체를 고칠 필요는 없었음.
