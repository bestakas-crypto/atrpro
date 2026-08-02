// 기존 ATRsite 데이터 가져오기 (스펙 16절). 파일명은 스펙 4.9의 디렉터리
// 구조를 그대로 따른 것이고, 실제로는 "가져오기(import)" 쪽 로직을 담는다 --
// "내보내기(export)" 버튼 자체는 기존 C:\atrsite 저장소 쪽에 별도로 추가해야
// 하는 기능이라 이 저장소 범위 밖이다. 그 전까지는 브라우저 devtools 콘솔에서
//   copy(JSON.stringify({'stock-portfolio': JSON.parse(localStorage.getItem('stock-portfolio')||'[]'),
//     'cash-deposits': JSON.parse(localStorage.getItem('cash-deposits')||'[]'),
//     'fx-rates': JSON.parse(localStorage.getItem('fx-rates')||'{}')}))
// 로 만든 JSON도 그대로 받아들인다.
import { api } from './api-client.js';

let pendingPayload = null;

export function openImportModal(ctx) {
  const { el } = ctx;
  pendingPayload = null;
  el.importFileInput.value = '';
  el.importReport.hidden = true;
  el.importReport.innerHTML = '';
  el.btnImportConfirm.hidden = true;
  el.modalImport.hidden = false;
}

export function closeImportModal(ctx) {
  ctx.el.modalImport.hidden = true;
  pendingPayload = null;
}

function renderReport(el, report) {
  el.importReport.hidden = false;
  const parts = [];
  parts.push(`<p>종목 ${report.summary.instruments}개 · 거래 ${report.summary.trades}건 · 예금 ${report.summary.deposits}건</p>`);
  if (report.errors.length > 0) {
    parts.push('<p class="import-error"><strong>오류 (가져오기 불가)</strong></p><ul>');
    report.errors.forEach((e) => parts.push(`<li class="import-error">[${e.path}] ${e.message}</li>`));
    parts.push('</ul>');
  }
  if (report.warnings.length > 0) {
    parts.push('<p><strong>참고 (진행은 가능)</strong></p><ul>');
    report.warnings.forEach((w) => parts.push(`<li>[${w.path}] ${w.message}</li>`));
    parts.push('</ul>');
  }
  el.importReport.innerHTML = parts.join('');
}

export async function handleFileSelected(ctx) {
  const { el } = ctx;
  const file = el.importFileInput.files[0];
  if (!file) return;
  let payload;
  try {
    payload = JSON.parse(await file.text());
  } catch (e) {
    ctx.showToast('JSON 파일을 읽을 수 없습니다.');
    return;
  }

  let report;
  try {
    report = await api.importLegacy(payload, { dryRun: true });
  } catch (e) {
    ctx.showToast('검증 요청 실패: ' + e.message);
    return;
  }

  renderReport(el, report);
  if (report.valid) {
    pendingPayload = payload;
    el.btnImportConfirm.hidden = false;
  } else {
    pendingPayload = null;
    el.btnImportConfirm.hidden = true;
  }
}

export async function handleImportConfirm(ctx) {
  if (!pendingPayload) return;
  try {
    await api.importLegacy(pendingPayload, { dryRun: false });
  } catch (e) {
    ctx.showToast('가져오기 실패: ' + e.message);
    return;
  }
  closeImportModal(ctx);
  ctx.showToast('기존 데이터를 가져왔습니다.');
  ctx.refreshCurrentView();
}
