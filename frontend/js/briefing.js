// 오늘의 브리핑 -- 메인 SPA와 분리된 독립 페이지 (report.html과 동일한 패턴).
// 같은 오리진이라 api-client.js가 localStorage의 API 키를 그대로 재사용한다.
import { api } from './api-client.js';

const el = {
  btnClose: document.getElementById('btn-close'),
  btnPrint: document.getElementById('btn-print'),
  btnSpeak: document.getElementById('btn-speak'),
  btnGenerate: document.getElementById('btn-generate'),
  btnRegenerate: document.getElementById('btn-regenerate'),
  status: document.getElementById('briefing-status'),
  result: document.getElementById('briefing-result'),
  gridRule: document.getElementById('briefing-grid-rule'),
  time: document.getElementById('briefing-time'),
  provider: document.getElementById('briefing-provider'),
  text: document.getElementById('briefing-text'),
};

let currentResultText = '';

// 브라우저 내장 TTS로 읽어준다(2026-08-03 추가 -- "목소리는 나중에 LLM으로,
// 일단 나오기만 하면 됨" 요청에 따라 서버 TTS 없이 무료 Web Speech API만
// 사용). 마크다운 기호/이모지를 그대로 읽으면 어색해서 미리 걸러낸다.
function cleanTextForSpeech(text) {
  return text
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}]/gu, '')
    .replace(/#{1,6}\s*/g, '')
    .replace(/\*\*/g, '')
    .replace(/\*/g, '')
    .replace(/^-{3,}$/gm, '')
    .replace(/^[-*]\s+/gm, '')
    .replace(/`/g, '')
    .trim();
}

function toggleSpeak() {
  if (!('speechSynthesis' in window)) {
    alert('이 브라우저는 음성 읽기를 지원하지 않습니다.');
    return;
  }
  if (window.speechSynthesis.speaking) {
    window.speechSynthesis.cancel();
    el.btnSpeak.textContent = '🔊 듣기';
    return;
  }
  const utterance = new SpeechSynthesisUtterance(cleanTextForSpeech(currentResultText));
  utterance.lang = 'ko-KR';
  utterance.onend = () => { el.btnSpeak.textContent = '🔊 듣기'; };
  utterance.onerror = () => { el.btnSpeak.textContent = '🔊 듣기'; };
  window.speechSynthesis.speak(utterance);
  el.btnSpeak.textContent = '⏹ 정지';
}

// VXN 그리드 판정은 Python이 확정한 값이라, LLM이 쓴 문장과 분리해서
// 눈에 띄게 먼저 보여준다(2026-08-03 추가).
function renderGridRule(snapshotJson) {
  let snapshot;
  try {
    snapshot = JSON.parse(snapshotJson);
  } catch (e) {
    el.gridRule.hidden = true;
    return;
  }
  const vxn = snapshot.vxn;
  if (!vxn) { el.gridRule.hidden = true; return; }

  el.gridRule.hidden = false;
  const rule = vxn.grid_rule || {};
  const vxnLine = vxn.status === 'OK' ? `VXN ${vxn.value.toFixed(1)}` : 'VXN 조회 실패';
  el.gridRule.innerHTML = '';
  const title = document.createElement('div');
  title.className = 'briefing-grid-rule-title';
  title.textContent = '오늘의 그리드 매수 (' + vxnLine + ')';
  el.gridRule.appendChild(title);
  const msg = document.createElement('div');
  msg.className = 'briefing-grid-rule-msg';
  msg.textContent = rule.message || '판정 불가';
  el.gridRule.appendChild(msg);
}

function showStatus(msg) {
  el.status.hidden = false;
  el.status.textContent = msg;
}

function hideStatus() {
  el.status.hidden = true;
}

function renderResult(briefing) {
  const created = new Date(briefing.created_at);
  el.time.textContent = '생성: ' + created.toLocaleString('ko-KR');
  el.provider.textContent = briefing.provider;
  el.text.textContent = briefing.result_text;
  currentResultText = briefing.result_text;
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  el.btnSpeak.textContent = '🔊 듣기';
  renderGridRule(briefing.snapshot_json);
  el.result.hidden = false;
  el.btnGenerate.hidden = true;
}

async function generate(force) {
  el.btnGenerate.disabled = true;
  el.btnRegenerate.disabled = true;
  showStatus(force ? '최신 데이터로 다시 만드는 중... (몇십 초 걸릴 수 있습니다)' : '브리핑을 만드는 중... (몇십 초 걸릴 수 있습니다)');
  try {
    const result = await api.runBriefing(force);
    hideStatus();
    renderResult(result);
  } catch (e) {
    showStatus('브리핑 생성 실패: ' + e.message);
  } finally {
    el.btnGenerate.disabled = false;
    el.btnRegenerate.disabled = false;
  }
}

el.btnGenerate.addEventListener('click', () => generate(false));
el.btnRegenerate.addEventListener('click', () => generate(true));
el.btnPrint.addEventListener('click', () => window.print());
el.btnSpeak.addEventListener('click', toggleSpeak);
el.btnClose.addEventListener('click', () => {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  if (window.history.length > 1) window.history.back();
  else window.location.href = 'index.html';
});

// 처음 열었을 때 이미 만들어진 브리핑이 있으면 바로 보여준다(재호출 없이).
(async () => {
  try {
    const latest = await api.getLatestBriefing();
    renderResult(latest);
  } catch (e) {
    // 404 등 -- 아직 브리핑이 없으면 "오늘 브리핑 만들기" 버튼만 보이는 초기 상태 그대로 둔다.
  }
})();

// index.html을 거치지 않고 헤더 아이콘으로 바로 이 페이지가 새 탭에서 열릴
// 수 있어서(target=_blank), 이 페이지 자체도 서비스워커 갱신을 확인해야
// 한다 -- 안 그러면 배포 직후에도 예전 캐시된 api-client.js가 계속 쓰여서
// 새로 추가된 API 함수가 "is not a function"으로 실패할 수 있다(2026-08-03
// 실제 발생: 종목탐구 배포 직후 이 문제로 검색이 깨짐).
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('service-worker.js').then((reg) => {
    reg.update().catch(() => {});
  }).catch(() => {});
  let swRefreshed = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (swRefreshed) return;
    swRefreshed = true;
    location.reload();
  });
}
