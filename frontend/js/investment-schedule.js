// frontend/js/investment-schedule.js -- v1.2 투자 일정 페이지 진입점.
// withdrawals.js와 같은 독립 페이지 패턴(모듈 하나, DOM 직접 조작).
import { api, ApiError } from './api-client.js';

const SCHEDULE_TYPE_LABELS = {
  BUY: '매수', SELL: '매도', DEPOSIT: '입금', WITHDRAWAL: '출금', REBALANCE: '리밸런싱',
  RULE_REVIEW: '규칙 점검', MATURITY: '만기', DIVIDEND_CHECK: '배당 확인', INTEREST_CHECK: '이자 확인',
  EXCHANGE: '환전', DISCLOSURE_CHECK: '공시 확인', TAX_CHECK: '세금 확인', GENERAL: '일반',
};
const STATUS_LABELS = {
  SCHEDULED: '예정', NOTIFIED: '알림됨', PARTIALLY_EXECUTED: '부분실행', COMPLETED: '완료',
  SKIPPED: '건너뜀', CANCELLED: '취소', ARCHIVED: '보관',
};
const RECURRING_TYPES = new Set(['DAILY', 'WEEKLY', 'BIWEEKLY', 'MONTHLY', 'YEARLY']);

const state = {
  instruments: [],
  deposits: [],
  itemRowCount: 0,
  editingScheduleId: null, // null이면 신규 등록 모드, 값이 있으면 해당 일정 수정 모드
};

const el = {};

function cacheDom() {
  const ids = [
    'btn-close', 'btn-add-schedule', 'today-list', 'today-empty', 'overdue-list', 'overdue-empty',
    'filter-schedule-type', 'filter-status', 'filter-start-date', 'filter-end-date',
    'btn-apply-filter', 'btn-reset-filter', 'is-list', 'is-empty',
    'modal-schedule', 'sc-modal-title', 'sc-status-group', 'sc-status',
    'sc-title', 'sc-type', 'sc-market', 'sc-recurrence-type', 'sc-interval-row',
    'sc-interval', 'sc-start-date', 'sc-end-mode-row', 'sc-end-mode', 'sc-occurrence-count-group',
    'sc-occurrence-count', 'sc-end-date-group', 'sc-end-date', 'sc-holiday-policy',
    'sc-notify-days-before', 'sc-total-amount', 'sc-currency', 'sc-account', 'sc-items',
    'btn-add-item-row', 'sc-memo', 'sc-preview', 'btn-schedule-cancel', 'btn-schedule-save',
    'modal-occurrence', 'occ-modal-title', 'occ-detail', 'occ-actions', 'btn-occ-close', 'toast',
  ];
  for (const id of ids) {
    el[id.replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase())] = document.getElementById(id);
  }
}

function showToast(msg) {
  el.toast.textContent = msg;
  el.toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { el.toast.hidden = true; }, 2600);
}

function fmtMoney(n, currency) {
  if (n === null || n === undefined) return '-';
  return `${Math.round(n).toLocaleString('ko-KR')} ${currency || ''}`.trim();
}

// ---------------------------------------------------------------------------
// 카드 렌더링
// ---------------------------------------------------------------------------

function occurrenceCardHtml(occ, { overdue = false } = {}) {
  const typeLabel = SCHEDULE_TYPE_LABELS[occ.schedule_type] || occ.schedule_type;
  const statusKey = overdue ? 'OVERDUE' : occ.status;
  const statusLabel = overdue ? '지연됨' : (STATUS_LABELS[occ.status] || occ.status);
  const dateLine = occ.original_scheduled_date !== occ.adjusted_scheduled_date
    ? `${occ.adjusted_scheduled_date} <span class="is-hint">(원래 ${occ.original_scheduled_date})</span>`
    : occ.adjusted_scheduled_date;
  const warn = occ.needs_holiday_confirmation ? '<div class="is-holiday-warn">⚠ 휴장일 확인 필요</div>' : '';
  return `
    <div class="is-card is-agenda-card ${overdue ? 'overdue' : ''}" data-occ-id="${occ.id}">
      <div class="row-top">
        <div class="title">${escapeHtml(occ.schedule_title || '')}</div>
        <div class="amount">${occ.planned_amount != null ? fmtMoney(occ.planned_amount, occ.currency) : ''}</div>
      </div>
      <div class="meta">
        <span class="is-type-badge">${typeLabel}</span>
        <span class="is-status-badge is-status-${statusKey}">${statusLabel}</span>
        <span class="date">${dateLine}</span>
      </div>
      ${warn}
    </div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------------------
// 오늘 / 지연 위젯
// ---------------------------------------------------------------------------

async function loadToday() {
  try {
    const { today, overdue } = await api.getTodayOccurrences();
    el.todayList.innerHTML = today.map((o) => occurrenceCardHtml(o)).join('');
    el.todayEmpty.hidden = today.length > 0;
    el.overdueList.innerHTML = overdue.map((o) => occurrenceCardHtml(o, { overdue: true })).join('');
    el.overdueEmpty.hidden = overdue.length > 0;
    bindCardClicks(el.todayList);
    bindCardClicks(el.overdueList);
  } catch (e) {
    showToast('오늘 일정을 불러오지 못했습니다.');
  }
}

// ---------------------------------------------------------------------------
// 전체 목록 + 필터
// ---------------------------------------------------------------------------

async function loadList() {
  // schedule_type은 occurrence가 아니라 investment_schedules에 있는 필드라
  // /schedule-occurrences 쿼리에는 넣지 않는다(아래에서 별도 처리).
  const query = {};
  if (el.filterStatus.value) query.status = el.filterStatus.value;
  if (el.filterStartDate.value) query.start_date = el.filterStartDate.value;
  if (el.filterEndDate.value) query.end_date = el.filterEndDate.value;

  try {
    let items;
    if (el.filterScheduleType.value) {
      // schedule_type은 investment_schedules에만 있는 필드라 스케줄을 먼저
      // 필터링한 다음 그 스케줄들의 occurrence만 모은다.
      const { items: schedules } = await api.listInvestmentSchedules({ schedule_type: el.filterScheduleType.value });
      const lists = await Promise.all(schedules.map((s) => api.listScheduleOccurrences({ ...query, schedule_id: s.id })));
      items = lists.flatMap((r) => r.items);
      items.sort((a, b) => a.adjusted_scheduled_date.localeCompare(b.adjusted_scheduled_date));
    } else {
      const result = await api.listScheduleOccurrences(query);
      items = result.items;
    }
    el.isList.innerHTML = items.map((o) => occurrenceCardHtml(o)).join('');
    el.isEmpty.hidden = items.length > 0;
    bindCardClicks(el.isList);
  } catch (e) {
    showToast('일정 목록을 불러오지 못했습니다.');
  }
}

function bindCardClicks(container) {
  container.querySelectorAll('[data-occ-id]').forEach((card) => {
    card.addEventListener('click', () => openOccurrenceModal(card.dataset.occId));
  });
}

// ---------------------------------------------------------------------------
// 회차 상세/처리 모달
// ---------------------------------------------------------------------------

async function openOccurrenceModal(occId) {
  let occ;
  try {
    occ = await api.getScheduleOccurrence(occId);
  } catch (e) {
    showToast('불러오지 못했습니다.');
    return;
  }
  el.occModalTitle.textContent = occ.schedule_title || '일정 상세';
  const typeLabel = SCHEDULE_TYPE_LABELS[occ.schedule_type] || occ.schedule_type;
  const itemsHtml = (occ.items || []).map((i) => `<dt>${escapeHtml(i.instrument_name_snapshot)}</dt><dd>${fmtMoney(i.planned_amount, occ.currency)}</dd>`).join('');
  const execHtml = (occ.executions || []).length
    ? `<dt>실행 기록</dt><dd>${occ.executions.map((x) => `${x.execution_type} · ${fmtMoney(x.executed_amount, occ.currency)} · ${x.executed_at}`).join('<br/>')}</dd>`
    : '';
  el.occDetail.innerHTML = `
    <dl>
      <dt>유형</dt><dd>${typeLabel}</dd>
      <dt>예정일</dt><dd>${occ.adjusted_scheduled_date}${occ.original_scheduled_date !== occ.adjusted_scheduled_date ? ` (원래 ${occ.original_scheduled_date})` : ''}</dd>
      ${occ.needs_holiday_confirmation ? '<dt>주의</dt><dd class="is-holiday-warn">이 시장은 휴장일 데이터가 없어 실제 개장 여부를 직접 확인해야 합니다.</dd>' : ''}
      <dt>상태</dt><dd>${STATUS_LABELS[occ.status] || occ.status}${occ.acknowledged ? ' (확인됨)' : ''}</dd>
      <dt>계획 금액</dt><dd>${occ.planned_amount != null ? fmtMoney(occ.planned_amount, occ.currency) : '-'}</dd>
      ${itemsHtml}
      ${execHtml}
    </dl>`;

  const actions = [];
  if (occ.status === 'NOTIFIED' && !occ.acknowledged) {
    actions.push(['확인', () => patchOccurrence(occId, { acknowledged: true })]);
  }
  if (['SCHEDULED', 'NOTIFIED', 'PARTIALLY_EXECUTED'].includes(occ.status)) {
    actions.push(['완료 처리', () => patchOccurrence(occId, { status: 'COMPLETED' })]);
    actions.push(['건너뜀', () => patchOccurrence(occId, { status: 'SKIPPED' })]);
    actions.push(['취소', () => patchOccurrence(occId, { status: 'CANCELLED' })]);
  }

  // 일정 자체(스케줄) 수정/삭제 -- 회차가 아니라 그 회차를 만든 스케줄을 대상으로
  // 동작한다(반복 일정이면 여러 회차가 한꺼번에 영향받을 수 있음, 수정 모달이
  // 실제 허용 범위를 반복유형에 맞게 다시 잠근다).
  let schedule = null;
  try {
    schedule = await api.getInvestmentSchedule(occ.schedule_id);
  } catch (e) {
    // 스케줄 조회 실패해도 회차 처리 자체는 계속 쓸 수 있어야 하니 조용히 무시.
  }
  if (schedule) {
    actions.push(['일정 수정', () => { el.modalOccurrence.hidden = true; openScheduleModal(schedule); }]);
    actions.push(['일정 삭제', () => handleDeleteSchedule(schedule)]);
  }

  el.occActions.innerHTML = actions.map(([label], idx) => `<button class="btn btn-secondary btn-block" data-action-idx="${idx}">${label}</button>`).join('');
  el.occActions.querySelectorAll('[data-action-idx]').forEach((btn, idx) => {
    btn.addEventListener('click', actions[idx][1]);
  });

  el.modalOccurrence.hidden = false;
}

async function handleDeleteSchedule(schedule) {
  const warn = schedule.recurrence_type === 'ONCE'
    ? '이 일정을 삭제할까요? 되돌릴 수 없습니다.'
    : `"${schedule.title}"의 모든 회차(${schedule.occurrences.length}개)를 함께 삭제합니다. 되돌릴 수 없습니다. 계속할까요?`;
  if (!window.confirm(warn)) return;
  try {
    await api.deleteInvestmentSchedule(schedule.id);
    showToast('일정을 삭제했습니다.');
    el.modalOccurrence.hidden = true;
    await Promise.all([loadToday(), loadList()]);
  } catch (e) {
    showToast('삭제하지 못했습니다.');
  }
}

async function patchOccurrence(occId, body) {
  try {
    await api.updateScheduleOccurrence(occId, body);
    showToast('반영했습니다.');
    el.modalOccurrence.hidden = true;
    await Promise.all([loadToday(), loadList(), refreshHeaderBadge()]);
  } catch (e) {
    showToast('처리하지 못했습니다.');
  }
}

async function refreshHeaderBadge() {
  // 이 페이지는 index.html과 별도 탭으로 열려 있을 수 있어 opener의 배지까지
  // 굳이 갱신하려 하지 않는다(단순 새로고침이면 index.html 쪽에서 다시 로드될 때
  // 알아서 최신화됨) -- 여기서는 아무것도 하지 않는다.
}

// ---------------------------------------------------------------------------
// 새 일정 등록 모달
// ---------------------------------------------------------------------------

function addItemRow(instrumentId = '', ratio = '') {
  const idx = state.itemRowCount++;
  const row = document.createElement('div');
  row.className = 'is-item-row';
  row.dataset.rowIdx = idx;
  const options = state.instruments.map((i) => `<option value="${i.id}" ${i.id === instrumentId ? 'selected' : ''}>${escapeHtml(i.name)}</option>`).join('');
  row.innerHTML = `
    <select class="item-instrument"><option value="">종목 선택</option>${options}</select>
    <input class="item-ratio" type="number" min="0" step="0.1" placeholder="비율%" value="${ratio}" />
    <button type="button" class="btn btn-secondary btn-small item-remove">삭제</button>`;
  row.querySelector('.item-remove').addEventListener('click', () => row.remove());
  el.scItems.appendChild(row);
}

function collectItemRows() {
  return [...el.scItems.querySelectorAll('.is-item-row')]
    .map((row) => ({
      instrument_id: row.querySelector('.item-instrument').value,
      ratio_percent: Number(row.querySelector('.item-ratio').value),
    }))
    .filter((it) => it.instrument_id && it.ratio_percent > 0);
}

function updateRecurrenceFieldVisibility() {
  const recType = el.scRecurrenceType.value;
  const isOnce = recType === 'ONCE';
  el.scIntervalRow.hidden = isOnce;
  el.scEndModeRow.hidden = isOnce;
  updateEndModeVisibility();
}

function updateEndModeVisibility() {
  const byCount = el.scEndMode.value === 'count';
  el.scOccurrenceCountGroup.hidden = !byCount;
  el.scEndDateGroup.hidden = byCount;
}

// scheduleToEdit이 없으면 신규 등록 모드, 있으면(GET /investment-schedules/{id}
// 응답 전체) 그 일정을 수정하는 모드로 연다.
function openScheduleModal(scheduleToEdit = null) {
  const isEdit = !!scheduleToEdit;
  const isOnce = isEdit ? scheduleToEdit.recurrence_type === 'ONCE' : true;
  state.editingScheduleId = isEdit ? scheduleToEdit.id : null;

  el.scTitle.value = isEdit ? scheduleToEdit.title : '';
  el.scType.value = isEdit ? scheduleToEdit.schedule_type : 'BUY';
  el.scMarket.value = isEdit ? scheduleToEdit.market : 'NONE';
  el.scRecurrenceType.value = isEdit ? scheduleToEdit.recurrence_type : 'ONCE';
  el.scInterval.value = isEdit ? String(scheduleToEdit.recurrence_interval) : '1';
  el.scStartDate.value = isEdit ? scheduleToEdit.start_date : '';
  el.scEndMode.value = isEdit && scheduleToEdit.end_date ? 'date' : 'count';
  el.scOccurrenceCount.value = isEdit && scheduleToEdit.occurrence_count ? String(scheduleToEdit.occurrence_count) : '1';
  el.scEndDate.value = isEdit && scheduleToEdit.end_date ? scheduleToEdit.end_date : '';
  el.scHolidayPolicy.value = isEdit ? scheduleToEdit.holiday_policy : 'NEXT_BUSINESS_DAY';
  el.scNotifyDaysBefore.value = isEdit ? String(scheduleToEdit.notify_days_before) : '0';
  el.scTotalAmount.value = isEdit && scheduleToEdit.total_amount != null ? String(scheduleToEdit.total_amount) : '';
  el.scCurrency.value = isEdit ? scheduleToEdit.currency : 'KRW';
  el.scAccount.value = isEdit && scheduleToEdit.deposit_account_id ? scheduleToEdit.deposit_account_id : '';
  el.scMemo.value = isEdit ? (scheduleToEdit.memo || '') : '';
  el.scItems.innerHTML = '';
  state.itemRowCount = 0;
  if (isEdit && scheduleToEdit.items) {
    scheduleToEdit.items.forEach((it) => addItemRow(it.instrument_id, it.ratio_percent));
  }
  el.scPreview.hidden = true;

  el.scStatusGroup.hidden = !isEdit;
  if (isEdit) el.scStatus.value = scheduleToEdit.status;

  // 반복 일정(ONCE가 아님)은 이미 여러 회차가 생성돼 있어 날짜/금액/종목배분을
  // 바꾸면 "어느 회차"인지 모호해진다 -- 백엔드가 거부하는 필드를 UI에서도
  // 미리 잠가서 헷갈리지 않게 한다. 반복유형 자체 전환(반복<->ONCE)도 막는다.
  const lockStructural = isEdit && !isOnce;
  [el.scType, el.scInterval, el.scStartDate, el.scEndMode, el.scOccurrenceCount,
   el.scEndDate, el.scTotalAmount, el.scCurrency, el.scAccount, el.btnAddItemRow].forEach((elm) => {
    elm.disabled = lockStructural;
  });
  el.scItems.querySelectorAll('select, input, button').forEach((elm) => { elm.disabled = lockStructural; });
  el.scRecurrenceType.disabled = isEdit;

  el.scModalTitle.textContent = isEdit ? '일정 수정' : '새 투자 일정';
  el.btnScheduleSave.textContent = isEdit ? '저장' : '등록';

  updateRecurrenceFieldVisibility();
  el.modalSchedule.hidden = false;
}

function buildSchedulePayload() {
  const recType = el.scRecurrenceType.value;
  const payload = {
    schedule_type: el.scType.value,
    title: el.scTitle.value.trim(),
    market: el.scMarket.value,
    recurrence_type: recType,
    recurrence_interval: Number(el.scInterval.value) || 1,
    start_date: el.scStartDate.value,
    holiday_policy: el.scHolidayPolicy.value,
    notify_days_before: Number(el.scNotifyDaysBefore.value) || 0,
    currency: el.scCurrency.value,
    deposit_account_id: el.scAccount.value || null,
    memo: el.scMemo.value.trim() || null,
    total_amount: el.scTotalAmount.value ? Number(el.scTotalAmount.value) : null,
    items: collectItemRows(),
  };
  if (RECURRING_TYPES.has(recType)) {
    if (el.scEndMode.value === 'count') {
      payload.occurrence_count = Number(el.scOccurrenceCount.value) || 1;
    } else {
      payload.end_date = el.scEndDate.value;
    }
  }
  return payload;
}

// 수정 모드에서는 백엔드가 실제로 받아들이는 필드만 보낸다(schedule_type/
// recurrence_type/recurrence_interval/occurrence_count/end_date는 수정 API에
// 아예 없음 -- 반복 규칙은 취소 후 재등록해야 함). ONCE가 아니면 날짜/금액/
// 종목배분도 뺀다(보내봐야 400 거부됨, UI에서도 이미 잠가둔 값).
function buildScheduleEditPayload(isOnce) {
  const payload = {
    title: el.scTitle.value.trim(),
    market: el.scMarket.value,
    holiday_policy: el.scHolidayPolicy.value,
    notify_days_before: Number(el.scNotifyDaysBefore.value) || 0,
    memo: el.scMemo.value.trim() || null,
    status: el.scStatus.value,
    deposit_account_id: el.scAccount.value || null,
  };
  if (isOnce) {
    payload.start_date = el.scStartDate.value;
    payload.total_amount = el.scTotalAmount.value ? Number(el.scTotalAmount.value) : null;
    payload.currency = el.scCurrency.value;
    payload.items = collectItemRows();
  }
  return payload;
}

async function handleScheduleSave() {
  const isEdit = !!state.editingScheduleId;

  if (isEdit) {
    const isOnce = el.scRecurrenceType.value === 'ONCE';
    const payload = buildScheduleEditPayload(isOnce);
    if (!payload.title) { showToast('제목을 입력하세요.'); return; }
    try {
      await api.updateInvestmentSchedule(state.editingScheduleId, payload);
      showToast('일정을 수정했습니다.');
      el.modalSchedule.hidden = true;
      await Promise.all([loadToday(), loadList()]);
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : String(e);
      showToast(`수정 실패: ${detail}`);
    }
    return;
  }

  const payload = buildSchedulePayload();
  if (!payload.title) { showToast('제목을 입력하세요.'); return; }
  if (!payload.start_date) { showToast('시작일을 입력하세요.'); return; }
  try {
    await api.createInvestmentSchedule(payload);
    showToast('일정을 등록했습니다.');
    el.modalSchedule.hidden = true;
    await Promise.all([loadToday(), loadList()]);
  } catch (e) {
    const detail = e instanceof ApiError ? e.detail : String(e);
    showToast(`등록 실패: ${detail}`);
  }
}

// ---------------------------------------------------------------------------
// 초기화
// ---------------------------------------------------------------------------

async function loadReferenceData() {
  try {
    const [instruments, deposits] = await Promise.all([api.listInstruments(), api.listDeposits()]);
    state.instruments = instruments;
    state.deposits = deposits;
    el.scAccount.innerHTML = '<option value="">선택 안 함</option>' + deposits.map((d) => `<option value="${d.id}">${escapeHtml(d.account_name)}</option>`).join('');
  } catch (e) {
    showToast('종목/계좌 목록을 불러오지 못했습니다.');
  }
}

function bindEvents() {
  el.btnClose.addEventListener('click', () => window.close());
  // openScheduleModal(scheduleToEdit)에 클릭 이벤트 객체가 그대로 넘어가면 안 되므로
  // (없으면 신규 등록 모드) 반드시 인자 없이 호출하는 래퍼를 통해 연결한다.
  el.btnAddSchedule.addEventListener('click', () => openScheduleModal());
  el.btnScheduleCancel.addEventListener('click', () => { el.modalSchedule.hidden = true; });
  el.modalSchedule.addEventListener('click', (e) => { if (e.target === el.modalSchedule) el.modalSchedule.hidden = true; });
  el.btnScheduleSave.addEventListener('click', handleScheduleSave);
  el.btnAddItemRow.addEventListener('click', () => addItemRow());
  el.scRecurrenceType.addEventListener('change', updateRecurrenceFieldVisibility);
  el.scEndMode.addEventListener('change', updateEndModeVisibility);

  el.btnOccClose.addEventListener('click', () => { el.modalOccurrence.hidden = true; });
  el.modalOccurrence.addEventListener('click', (e) => { if (e.target === el.modalOccurrence) el.modalOccurrence.hidden = true; });

  el.btnApplyFilter.addEventListener('click', loadList);
  el.btnResetFilter.addEventListener('click', () => {
    el.filterScheduleType.value = '';
    el.filterStatus.value = '';
    el.filterStartDate.value = '';
    el.filterEndDate.value = '';
    loadList();
  });
}

async function init() {
  cacheDom();
  bindEvents();
  await loadReferenceData();
  await Promise.all([loadToday(), loadList()]);
}

document.addEventListener('DOMContentLoaded', init);
