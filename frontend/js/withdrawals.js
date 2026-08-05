// frontend/js/withdrawals.js -- v1.1 현금 출금기록(독립 페이지).
// report.html/company-explorer.html/briefing.html과 같은 패턴: index.html의
// SPA 상태(main.js)와 완전히 분리된 별도 페이지. 모든 데이터는 REST API로만
// 저장하고(스펙 12) localStorage에 원본을 저장하지 않는다.
import { api } from './api-client.js';
import { nowDatetimeLocalStr } from './formatters.js';

const el = {};
function cacheDom() {
  const ids = [
    'btn-close', 'btn-export-csv', 'btn-add-withdrawal',
    'search-purpose', 'recent-purposes',
    'filter-start-date', 'filter-end-date', 'filter-account', 'filter-currency',
    'btn-apply-filter', 'btn-reset-filter',
    'result-count', 'result-sum', 'btn-toggle-account-breakdown', 'account-breakdown',
    'wd-list', 'wd-empty', 'btn-load-more',
    'modal-withdrawal', 'modal-withdrawal-title', 'no-account-hint',
    'wd-account', 'wd-withdrawn-at', 'wd-purpose', 'wd-amount', 'wd-currency', 'wd-memo',
    'btn-withdrawal-cancel', 'btn-withdrawal-save',
    'modal-delete-confirm', 'delete-confirm-detail', 'btn-delete-cancel', 'btn-delete-confirm',
    'modal-confirm', 'confirm-message', 'btn-confirm-cancel', 'btn-confirm-ok',
    'toast',
  ];
  ids.forEach((id) => {
    const camel = id.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    el[camel] = document.getElementById(id);
  });
}

const state = {
  filters: { start_date: '', end_date: '', purpose: '', deposit_account_id: '', currency: '' },
  offset: 0,
  limit: 20,
  items: [],
  total: 0,
  deposits: [],
  editingId: null,
  deletingId: null,
  confirmCallback: null,
};

const CURRENCY_LABEL = { KRW: '원', USD: '달러', JPY: '엔' };

// ---------- 유틸 ----------
let toastTimer = null;
function showToast(msg) {
  el.toast.textContent = msg;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, 2600);
}

function askConfirm(message, onConfirm) {
  el.confirmMessage.textContent = message;
  state.confirmCallback = onConfirm;
  el.modalConfirm.hidden = false;
}

function formatAmount(amount, currency) {
  const n = Number(amount);
  if (!Number.isFinite(n)) return '-';
  const decimals = currency === 'KRW' || currency === 'JPY' ? 0 : 2;
  return n.toLocaleString('ko-KR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function formatAmountsByCurrency(byCurrency) {
  const entries = Object.entries(byCurrency || {}).filter(([, v]) => v);
  if (entries.length === 0) return '<span class="empty">기록 없음</span>';
  return entries.map(([cur, amt]) => `${cur} ${formatAmount(amt, cur)}`).join('<br>');
}

function formatWithdrawnAt(s) {
  // 'YYYY-MM-DDTHH:MM:SS' -> 'YYYY-MM-DD HH:mm' (스펙 3.1 화면 표시 형식).
  return (s || '').replace('T', ' ').slice(0, 16);
}

// ---------- 예금계좌 목록 (선택 목록 + 필터용) ----------
async function loadDeposits() {
  state.deposits = await api.listDeposits();
  const accountLabel = (d) => `${d.account_name} · ${d.currency}`;

  el.wdAccount.innerHTML = '';
  if (state.deposits.length === 0) {
    el.noAccountHint.hidden = false;
    el.wdAccount.hidden = true;
  } else {
    el.noAccountHint.hidden = true;
    el.wdAccount.hidden = false;
    state.deposits.forEach((d) => {
      const opt = document.createElement('option');
      opt.value = d.id;
      opt.textContent = accountLabel(d);
      opt.dataset.currency = d.currency;
      el.wdAccount.appendChild(opt);
    });
  }

  el.filterAccount.innerHTML = '<option value="">전체</option>';
  state.deposits.forEach((d) => {
    const opt = document.createElement('option');
    opt.value = d.id;
    opt.textContent = accountLabel(d);
    el.filterAccount.appendChild(opt);
  });
}

// 계좌 선택 시 통화 자동 반영(스펙 4: "계좌를 선택할 때 그 통화를 기본값으로
// 자동 선택"). 계좌는 하나의 통화만 쓰는 구조라 그대로 고정한다.
function syncCurrencyToSelectedAccount() {
  const opt = el.wdAccount.selectedOptions[0];
  if (opt && opt.dataset.currency) el.wdCurrency.value = opt.dataset.currency;
}

// ---------- 요약 카드 ----------
// cacheDom()의 kebab-case->camelCase 변환은 하이픈만 처리하고 밑줄은 그대로
// 두므로(예: 'summary-this_week' -> el.summaryThis_week) 여기서는 대신
// getElementById로 HTML의 id 문자열(summary-today 등)을 직접 써서 헷갈릴
// 여지를 없앤다.
async function loadSummary() {
  const summary = await api.getWithdrawalsSummary();
  for (const key of ['today', 'this_week', 'this_month', 'ytd']) {
    document.getElementById(`summary-${key}`).innerHTML = formatAmountsByCurrency(summary[key]);
  }
}

// ---------- 최근 용도 추천(스펙 9.1) ----------
async function loadRecentPurposes() {
  const { purposes } = await api.getRecentPurposes();
  el.recentPurposes.innerHTML = '';
  if (!purposes || purposes.length === 0) return;
  const label = document.createElement('span');
  label.className = 'wd-hint';
  label.textContent = '최근 용도: ';
  el.recentPurposes.appendChild(label);
  purposes.forEach((p) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'wd-purpose-chip';
    chip.textContent = p;
    chip.addEventListener('click', () => {
      el.searchPurpose.value = p;
      applyFilters();
    });
    el.recentPurposes.appendChild(chip);
  });
}

// ---------- 목록 조회 ----------
function currentQuery(offset) {
  const q = {};
  if (state.filters.start_date) q.start_date = state.filters.start_date;
  if (state.filters.end_date) q.end_date = state.filters.end_date;
  if (state.filters.purpose) q.purpose = state.filters.purpose;
  if (state.filters.deposit_account_id) q.deposit_account_id = state.filters.deposit_account_id;
  if (state.filters.currency) q.currency = state.filters.currency;
  q.limit = state.limit;
  q.offset = offset;
  return q;
}

async function loadList({ append = false } = {}) {
  const offset = append ? state.items.length : 0;
  const resp = await api.listWithdrawals(currentQuery(offset));
  state.total = resp.total;
  state.items = append ? state.items.concat(resp.items) : resp.items;

  renderResultSummary(resp);
  renderList();
}

function renderResultSummary(resp) {
  const hasFilter = Object.values(state.filters).some((v) => v);
  el.resultCount.textContent = hasFilter
    ? `검색 결과: 총 ${resp.total}건`
    : `전체 ${resp.total}건`;
  el.resultSum.innerHTML = formatAmountsByCurrency(resp.sum_by_currency);

  const accounts = resp.sum_by_account || [];
  if (accounts.length === 0) {
    el.btnToggleAccountBreakdown.hidden = true;
    el.accountBreakdown.classList.remove('open');
  } else {
    el.btnToggleAccountBreakdown.hidden = false;
    el.accountBreakdown.innerHTML = accounts
      .map((a) => `<div>${a.account_name_snapshot} · ${a.currency} ${formatAmount(a.total, a.currency)} (${a.count}건)</div>`)
      .join('');
  }
}

function renderList() {
  el.wdList.innerHTML = '';
  el.wdEmpty.hidden = state.items.length > 0;
  state.items.forEach((w) => el.wdList.appendChild(renderCard(w)));
  el.btnLoadMore.hidden = state.items.length >= state.total;
}

function renderCard(w) {
  const card = document.createElement('div');
  card.className = 'wd-card';
  card.dataset.id = w.id;

  const top = document.createElement('div');
  top.className = 'wd-card-top';
  const purposeEl = document.createElement('div');
  purposeEl.className = 'wd-card-purpose';
  purposeEl.textContent = w.purpose; // textContent만 사용 -- innerHTML에 사용자 입력 직접 삽입 금지(스펙 13 XSS 방지)
  if (w.is_edited) {
    const badge = document.createElement('span');
    badge.className = 'wd-edited-badge';
    badge.textContent = '수정됨';
    purposeEl.appendChild(badge);
  }
  const amountEl = document.createElement('div');
  amountEl.className = 'wd-card-amount';
  amountEl.textContent = `- ${w.currency} ${formatAmount(w.amount, w.currency)}`;
  top.appendChild(purposeEl);
  top.appendChild(amountEl);

  const meta = document.createElement('div');
  meta.className = 'wd-card-meta';
  meta.innerHTML = `<span>${formatWithdrawnAt(w.withdrawn_at)}</span><span>${escapeText(w.account_name_snapshot)}</span>`;

  card.appendChild(top);
  card.appendChild(meta);

  if (w.memo) {
    const memo = document.createElement('div');
    memo.className = 'wd-card-memo';
    memo.textContent = w.memo;
    card.appendChild(memo);
  }

  const actions = document.createElement('div');
  actions.className = 'wd-card-actions';
  const editBtn = document.createElement('button');
  editBtn.className = 'btn btn-secondary btn-small';
  editBtn.textContent = '수정';
  editBtn.addEventListener('click', () => openEditModal(w));
  const delBtn = document.createElement('button');
  delBtn.className = 'btn btn-danger btn-small';
  delBtn.textContent = '삭제';
  delBtn.addEventListener('click', () => openDeleteConfirm(w));
  actions.appendChild(editBtn);
  actions.appendChild(delBtn);
  card.appendChild(actions);

  return card;
}

// meta 영역은 innerHTML을 쓰므로(레이아웃 편의) 텍스트만 안전하게 이스케이프한다.
function escapeText(s) {
  const div = document.createElement('div');
  div.textContent = s == null ? '' : String(s);
  return div.innerHTML;
}

// ---------- 필터 ----------
function applyFilters() {
  state.filters = {
    start_date: el.filterStartDate.value,
    end_date: el.filterEndDate.value,
    purpose: el.searchPurpose.value.trim(),
    deposit_account_id: el.filterAccount.value,
    currency: el.filterCurrency.value,
  };
  loadList();
}

function resetFilters() {
  el.filterStartDate.value = '';
  el.filterEndDate.value = '';
  el.searchPurpose.value = '';
  el.filterAccount.value = '';
  el.filterCurrency.value = '';
  applyFilters();
}

// ---------- 추가/수정 모달 ----------
function openAddModal() {
  if (state.deposits.length === 0) {
    showToast('등록된 예금계좌가 없습니다. 먼저 예금관리에서 계좌를 등록해 주세요.');
  }
  state.editingId = null;
  el.modalWithdrawalTitle.textContent = '출금 추가';
  el.wdWithdrawnAt.value = nowDatetimeLocalStr();
  el.wdPurpose.value = '';
  el.wdAmount.value = '';
  el.wdMemo.value = '';
  if (el.wdAccount.options.length > 0) {
    el.wdAccount.selectedIndex = 0;
    syncCurrencyToSelectedAccount();
  }
  el.modalWithdrawal.hidden = false;
}

function openEditModal(w) {
  state.editingId = w.id;
  el.modalWithdrawalTitle.textContent = '출금 수정';
  el.wdWithdrawnAt.value = w.withdrawn_at.slice(0, 16);
  el.wdPurpose.value = w.purpose;
  el.wdAmount.value = w.amount;
  el.wdCurrency.value = w.currency;
  el.wdMemo.value = w.memo || '';
  // 계좌가 비활성/삭제됐어도(스펙 4 권장 정책) 수정 화면에선 현재 선택값을
  // 그대로 보여줘야 하므로, 목록에 없는 계좌면 임시 옵션을 추가해 선택해둔다.
  if (w.deposit_account_id && ![...el.wdAccount.options].some((o) => o.value === w.deposit_account_id)) {
    const opt = document.createElement('option');
    opt.value = w.deposit_account_id;
    opt.textContent = `${w.account_name_snapshot} (현재 목록에 없음)`;
    el.wdAccount.appendChild(opt);
  }
  if (w.deposit_account_id) el.wdAccount.value = w.deposit_account_id;
  el.modalWithdrawal.hidden = false;
}

function closeWithdrawalModal() {
  el.modalWithdrawal.hidden = true;
  state.editingId = null;
}

function currentFormPayload() {
  return {
    withdrawn_at: el.wdWithdrawnAt.value,
    deposit_account_id: el.wdAccount.value,
    purpose: el.wdPurpose.value.trim(),
    amount: parseFloat(el.wdAmount.value),
    currency: el.wdCurrency.value,
    memo: el.wdMemo.value.trim(),
  };
}

async function handleSave() {
  const payload = currentFormPayload();
  if (!payload.deposit_account_id) { showToast('출금계좌를 선택하세요.'); return; }
  if (!payload.withdrawn_at) { showToast('출금 일시를 입력하세요.'); return; }
  if (!payload.purpose) { showToast('용도를 입력하세요.'); return; }
  if (!(payload.amount > 0)) { showToast('출금액은 0보다 커야 합니다.'); return; }

  // 스펙 9.2 중복입력 경고 -- 신규 등록일 때만 검사(수정 중엔 자기 자신과
  // 비교돼서 항상 "중복"으로 뜨는 걸 피함).
  if (!state.editingId) {
    try {
      const { duplicate } = await api.checkWithdrawalDuplicate(payload);
      if (duplicate) {
        askConfirm('비슷한 출금기록이 이미 있습니다. 그래도 저장하시겠습니까?', () => submitSave(payload));
        return;
      }
    } catch (e) { /* 중복검사 실패는 저장을 막지 않는다 */ }
  }
  await submitSave(payload);
}

async function submitSave(payload) {
  el.btnWithdrawalSave.disabled = true; // 스펙 12 -- 중복 제출 방지
  try {
    if (state.editingId) {
      await api.updateWithdrawal(state.editingId, payload);
    } else {
      await api.createWithdrawal(payload);
    }
  } catch (e) {
    showToast('저장 실패: ' + e.message);
    return;
  } finally {
    el.btnWithdrawalSave.disabled = false;
  }
  closeWithdrawalModal();
  showToast('저장되었습니다.');
  await Promise.all([loadList(), loadSummary(), loadRecentPurposes()]);
}

// ---------- 삭제 ----------
function openDeleteConfirm(w) {
  state.deletingId = w.id;
  el.deleteConfirmDetail.innerHTML = `
    <dt>일시</dt><dd>${escapeText(formatWithdrawnAt(w.withdrawn_at))}</dd>
    <dt>계좌</dt><dd>${escapeText(w.account_name_snapshot)}</dd>
    <dt>용도</dt><dd>${escapeText(w.purpose)}</dd>
    <dt>금액</dt><dd>${escapeText(w.currency + ' ' + formatAmount(w.amount, w.currency))}</dd>
  `;
  el.modalDeleteConfirm.hidden = false;
}

async function handleDelete() {
  const id = state.deletingId;
  if (!id) return;
  try {
    await api.deleteWithdrawal(id);
  } catch (e) {
    showToast('삭제 실패: ' + e.message);
    return;
  }
  el.modalDeleteConfirm.hidden = true;
  state.deletingId = null;
  showToast('삭제되었습니다.');
  // 스펙 5.4 -- 삭제 성공 후 목록과 모든 합계를 다시 조회.
  await Promise.all([loadList(), loadSummary()]);
}

// ---------- CSV 내보내기 ----------
async function handleExportCsv() {
  el.btnExportCsv.disabled = true;
  try {
    const blob = await api.exportWithdrawalsCsv(currentQuery(0));
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'cash_withdrawals.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    showToast('CSV 내보내기 실패: ' + e.message);
  } finally {
    el.btnExportCsv.disabled = false;
  }
}

// ---------- 이벤트 바인딩 ----------
function bindEvents() {
  el.btnClose.addEventListener('click', () => {
    if (window.history.length > 1) window.history.back();
    else window.location.href = 'index.html';
  });

  el.btnAddWithdrawal.addEventListener('click', openAddModal);
  el.btnWithdrawalCancel.addEventListener('click', closeWithdrawalModal);
  el.btnWithdrawalSave.addEventListener('click', handleSave);
  el.wdAccount.addEventListener('change', syncCurrencyToSelectedAccount);
  el.modalWithdrawal.addEventListener('click', (e) => { if (e.target === el.modalWithdrawal) closeWithdrawalModal(); });

  el.btnDeleteCancel.addEventListener('click', () => { el.modalDeleteConfirm.hidden = true; state.deletingId = null; });
  el.btnDeleteConfirm.addEventListener('click', handleDelete);
  el.modalDeleteConfirm.addEventListener('click', (e) => { if (e.target === el.modalDeleteConfirm) { el.modalDeleteConfirm.hidden = true; state.deletingId = null; } });

  el.btnConfirmCancel.addEventListener('click', () => { el.modalConfirm.hidden = true; state.confirmCallback = null; });
  el.btnConfirmOk.addEventListener('click', () => {
    const cb = state.confirmCallback;
    el.modalConfirm.hidden = true;
    state.confirmCallback = null;
    if (cb) cb();
  });
  el.modalConfirm.addEventListener('click', (e) => { if (e.target === el.modalConfirm) { el.modalConfirm.hidden = true; state.confirmCallback = null; } });

  el.btnApplyFilter.addEventListener('click', applyFilters);
  el.btnResetFilter.addEventListener('click', resetFilters);
  el.searchPurpose.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyFilters(); });

  el.btnToggleAccountBreakdown.addEventListener('click', () => {
    const open = el.accountBreakdown.classList.toggle('open');
    el.btnToggleAccountBreakdown.textContent = open ? '계좌별 합계 숨기기 ▴' : '계좌별 합계 보기 ▾';
  });

  el.btnLoadMore.addEventListener('click', () => loadList({ append: true }));
  el.btnExportCsv.addEventListener('click', handleExportCsv);
}

// ---------- 초기화 ----------
async function init() {
  cacheDom();
  bindEvents();
  try {
    await loadDeposits();
    await Promise.all([loadSummary(), loadRecentPurposes(), loadList()]);
  } catch (e) {
    showToast('불러오기 실패: ' + e.message);
  }
}

document.addEventListener('DOMContentLoaded', init);

// index.html을 거치지 않고 헤더 아이콘으로 이 페이지가 바로 새 탭에서 열릴
// 수 있어서(target=_blank), 이 페이지 자체도 서비스워커 갱신을 확인해야
// 한다(2026-08-03 종목탐구에서 실제로 겪은 문제와 동일한 이유 -- briefing.js/
// company-explorer.js와 같은 패턴).
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
