// frontend/js/withdrawals.js -- v1.5(2026-08-12) 입출금 통합 장부.
// 옛 출금기록(cash_withdrawals) 전용 페이지였는데, 카드/소비 상세 분석은
// card-kunoh/Kunoh's Sheet로 옮겨가서(사용자, 2026-08-12) 입금까지 합친
// 간단한 통합 장부로 재작성. report.html/company-explorer.html 등과 같은
// 패턴: index.html의 SPA 상태(main.js)와 완전히 분리된 별도 페이지. 모든
// 데이터는 REST API로만 저장하고(스펙 12) localStorage에 원본을 저장하지
// 않는다.
import { api } from './api-client.js';
import { nowDatetimeLocalStr } from './formatters.js';

const el = {};
function cacheDom() {
  const ids = [
    'btn-close', 'btn-export-csv', 'btn-add-entry',
    'filter-start-date', 'filter-end-date', 'filter-account', 'filter-currency', 'filter-entry-type',
    'btn-apply-filter', 'btn-reset-filter',
    'result-count', 'result-sum',
    'wd-list', 'wd-empty', 'btn-load-more',
    'modal-entry', 'modal-entry-title', 'no-account-hint',
    'wd-account', 'wd-occurred-at', 'wd-entry-type', 'wd-amount', 'wd-currency', 'wd-memo',
    'btn-entry-cancel', 'btn-entry-save',
    'modal-delete-confirm', 'delete-confirm-detail', 'btn-delete-cancel', 'btn-delete-confirm',
    'toast',
  ];
  ids.forEach((id) => {
    const camel = id.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    el[camel] = document.getElementById(id);
  });
}

const state = {
  filters: { start_date: '', end_date: '', deposit_account_id: '', currency: '', entry_type: '' },
  offset: 0,
  limit: 20,
  items: [],
  total: 0,
  deposits: [],
  editingId: null,
  deletingId: null,
};

const ENTRY_TYPE_LABEL = {
  EXTERNAL_IN: '외부입금',
  EXTERNAL_OUT: '소비출금',
  INTERNAL_IN: '내부이체입금',
  INTERNAL_OUT: '내부이체출금',
};
const IN_TYPES = new Set(['EXTERNAL_IN', 'INTERNAL_IN']);

// ---------- 유틸 ----------
let toastTimer = null;
function showToast(msg) {
  el.toast.textContent = msg;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, 2600);
}

function formatAmount(amount, currency) {
  const n = Number(amount);
  if (!Number.isFinite(n)) return '-';
  const decimals = currency === 'KRW' || currency === 'JPY' ? 0 : 2;
  return n.toLocaleString('ko-KR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

// {KRW: {in, out, net}, ...} -> "KRW 순 +1,000,000 (입 3,000,000 / 출 2,000,000)"
function formatNetByCurrency(byCurrency) {
  const entries = Object.entries(byCurrency || {}).filter(([, v]) => v.in || v.out);
  if (entries.length === 0) return '<span class="empty">기록 없음</span>';
  return entries.map(([cur, v]) => {
    const sign = v.net > 0 ? '+' : '';
    return `${cur} 순 ${sign}${formatAmount(v.net, cur)}`
      + `<br><span class="wd-hint">입 ${formatAmount(v.in, cur)} / 출 ${formatAmount(v.out, cur)}</span>`;
  }).join('<br>');
}

function formatOccurredAt(s) {
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

function syncCurrencyToSelectedAccount() {
  const opt = el.wdAccount.selectedOptions[0];
  if (opt && opt.dataset.currency) el.wdCurrency.value = opt.dataset.currency;
}

// ---------- 요약 카드 ----------
async function loadSummary() {
  const summary = await api.getCashLedgerSummary();
  for (const key of ['today', 'this_week', 'this_month', 'ytd']) {
    document.getElementById(`summary-${key}`).innerHTML = formatNetByCurrency(summary[key]);
  }
}

// ---------- 목록 조회 ----------
function currentQuery(offset) {
  const q = {};
  if (state.filters.start_date) q.start_date = state.filters.start_date;
  if (state.filters.end_date) q.end_date = state.filters.end_date;
  if (state.filters.deposit_account_id) q.deposit_account_id = state.filters.deposit_account_id;
  if (state.filters.currency) q.currency = state.filters.currency;
  if (state.filters.entry_type) q.entry_type = state.filters.entry_type;
  q.limit = state.limit;
  q.offset = offset;
  return q;
}

async function loadList({ append = false } = {}) {
  const offset = append ? state.items.length : 0;
  const resp = await api.listCashLedger(currentQuery(offset));
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
  el.resultSum.innerHTML = formatNetByCurrency(resp.sum_by_currency);
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
  const typeEl = document.createElement('div');
  typeEl.className = 'wd-card-purpose';
  typeEl.textContent = ENTRY_TYPE_LABEL[w.entry_type] || w.entry_type;
  if (w.is_edited) {
    const badge = document.createElement('span');
    badge.className = 'wd-edited-badge';
    badge.textContent = '수정됨';
    typeEl.appendChild(badge);
  }
  const amountEl = document.createElement('div');
  amountEl.className = 'wd-card-amount';
  const isIn = IN_TYPES.has(w.entry_type);
  amountEl.textContent = `${isIn ? '+' : '-'} ${w.currency} ${formatAmount(w.amount, w.currency)}`;
  amountEl.classList.add(isIn ? 'wd-amount-in' : 'wd-amount-out');
  top.appendChild(typeEl);
  top.appendChild(amountEl);

  const meta = document.createElement('div');
  meta.className = 'wd-card-meta';
  meta.innerHTML = `<span>${escapeText(formatOccurredAt(w.occurred_at))}</span><span>${escapeText(w.account_name_snapshot)}</span>`;

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
    deposit_account_id: el.filterAccount.value,
    currency: el.filterCurrency.value,
    entry_type: el.filterEntryType.value,
  };
  loadList();
}

function resetFilters() {
  el.filterStartDate.value = '';
  el.filterEndDate.value = '';
  el.filterAccount.value = '';
  el.filterCurrency.value = '';
  el.filterEntryType.value = '';
  applyFilters();
}

// ---------- 추가/수정 모달 ----------
function openAddModal() {
  if (state.deposits.length === 0) {
    showToast('등록된 예금계좌가 없습니다. 먼저 예금관리에서 계좌를 등록해 주세요.');
  }
  state.editingId = null;
  el.modalEntryTitle.textContent = '기록 추가';
  el.wdOccurredAt.value = nowDatetimeLocalStr();
  el.wdEntryType.value = 'EXTERNAL_OUT';
  el.wdAmount.value = '';
  el.wdMemo.value = '';
  if (el.wdAccount.options.length > 0) {
    el.wdAccount.selectedIndex = 0;
    syncCurrencyToSelectedAccount();
  }
  el.modalEntry.hidden = false;
}

function openEditModal(w) {
  state.editingId = w.id;
  el.modalEntryTitle.textContent = '기록 수정';
  el.wdOccurredAt.value = w.occurred_at.slice(0, 16);
  el.wdEntryType.value = w.entry_type;
  el.wdAmount.value = w.amount;
  el.wdCurrency.value = w.currency;
  el.wdMemo.value = w.memo || '';
  if (w.deposit_account_id && ![...el.wdAccount.options].some((o) => o.value === w.deposit_account_id)) {
    const opt = document.createElement('option');
    opt.value = w.deposit_account_id;
    opt.textContent = `${w.account_name_snapshot} (현재 목록에 없음)`;
    el.wdAccount.appendChild(opt);
  }
  if (w.deposit_account_id) el.wdAccount.value = w.deposit_account_id;
  el.modalEntry.hidden = false;
}

function closeEntryModal() {
  el.modalEntry.hidden = true;
  state.editingId = null;
}

function currentFormPayload() {
  return {
    occurred_at: el.wdOccurredAt.value,
    deposit_account_id: el.wdAccount.value,
    entry_type: el.wdEntryType.value,
    amount: parseFloat(el.wdAmount.value),
    currency: el.wdCurrency.value,
    memo: el.wdMemo.value.trim(),
  };
}

async function handleSave() {
  const payload = currentFormPayload();
  if (!payload.deposit_account_id) { showToast('계좌를 선택하세요.'); return; }
  if (!payload.occurred_at) { showToast('일시를 입력하세요.'); return; }
  if (!(payload.amount > 0)) { showToast('금액은 0보다 커야 합니다.'); return; }

  el.btnEntrySave.disabled = true; // 스펙 12 -- 중복 제출 방지
  try {
    if (state.editingId) {
      await api.updateCashLedgerEntry(state.editingId, payload);
    } else {
      await api.createCashLedgerEntry(payload);
    }
  } catch (e) {
    showToast('저장 실패: ' + e.message);
    return;
  } finally {
    el.btnEntrySave.disabled = false;
  }
  closeEntryModal();
  showToast('저장되었습니다.');
  await Promise.all([loadList(), loadSummary()]);
}

// ---------- 삭제 ----------
function openDeleteConfirm(w) {
  state.deletingId = w.id;
  el.deleteConfirmDetail.innerHTML = `
    <dt>일시</dt><dd>${escapeText(formatOccurredAt(w.occurred_at))}</dd>
    <dt>계좌</dt><dd>${escapeText(w.account_name_snapshot)}</dd>
    <dt>구분</dt><dd>${escapeText(ENTRY_TYPE_LABEL[w.entry_type] || w.entry_type)}</dd>
    <dt>금액</dt><dd>${escapeText(w.currency + ' ' + formatAmount(w.amount, w.currency))}</dd>
  `;
  el.modalDeleteConfirm.hidden = false;
}

async function handleDelete() {
  const id = state.deletingId;
  if (!id) return;
  try {
    await api.deleteCashLedgerEntry(id);
  } catch (e) {
    showToast('삭제 실패: ' + e.message);
    return;
  }
  el.modalDeleteConfirm.hidden = true;
  state.deletingId = null;
  showToast('삭제되었습니다.');
  await Promise.all([loadList(), loadSummary()]);
}

// ---------- CSV 내보내기 ----------
async function handleExportCsv() {
  el.btnExportCsv.disabled = true;
  try {
    const blob = await api.exportCashLedgerCsv(currentQuery(0));
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'cash_ledger.csv';
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

  el.btnAddEntry.addEventListener('click', openAddModal);
  el.btnEntryCancel.addEventListener('click', closeEntryModal);
  el.btnEntrySave.addEventListener('click', handleSave);
  el.wdAccount.addEventListener('change', syncCurrencyToSelectedAccount);
  el.modalEntry.addEventListener('click', (e) => { if (e.target === el.modalEntry) closeEntryModal(); });

  el.btnDeleteCancel.addEventListener('click', () => { el.modalDeleteConfirm.hidden = true; state.deletingId = null; });
  el.btnDeleteConfirm.addEventListener('click', handleDelete);
  el.modalDeleteConfirm.addEventListener('click', (e) => { if (e.target === el.modalDeleteConfirm) { el.modalDeleteConfirm.hidden = true; state.deletingId = null; } });

  el.btnApplyFilter.addEventListener('click', applyFilters);
  el.btnResetFilter.addEventListener('click', resetFilters);

  el.btnLoadMore.addEventListener('click', () => loadList({ append: true }));
  el.btnExportCsv.addEventListener('click', handleExportCsv);
}

// ---------- 초기화 ----------
async function init() {
  cacheDom();
  bindEvents();
  try {
    await loadDeposits();
    await Promise.all([loadSummary(), loadList()]);
  } catch (e) {
    showToast('불러오기 실패: ' + e.message);
  }
}

document.addEventListener('DOMContentLoaded', init);

// index.html을 거치지 않고 헤더 아이콘으로 이 페이지가 바로 새 탭에서 열릴
// 수 있어서(target=_blank), 이 페이지 자체도 서비스워커 갱신을 확인해야
// 한다(2026-08-03 종목탐구에서 실제로 겪은 문제와 동일한 이유).
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
