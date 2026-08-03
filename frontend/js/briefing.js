// 오늘의 브리핑 -- 메인 SPA와 분리된 독립 페이지 (report.html과 동일한 패턴).
// 같은 오리진이라 api-client.js가 localStorage의 API 키를 그대로 재사용한다.
import { api } from './api-client.js';

const el = {
  btnClose: document.getElementById('btn-close'),
  btnGenerate: document.getElementById('btn-generate'),
  btnRegenerate: document.getElementById('btn-regenerate'),
  status: document.getElementById('briefing-status'),
  result: document.getElementById('briefing-result'),
  gridRule: document.getElementById('briefing-grid-rule'),
  time: document.getElementById('briefing-time'),
  provider: document.getElementById('briefing-provider'),
  text: document.getElementById('briefing-text'),
};

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
el.btnClose.addEventListener('click', () => {
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
