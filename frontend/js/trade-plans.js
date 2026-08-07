// frontend/js/trade-plans.js -- 매매계획(트리거 감시) Phase 1, 독립 페이지.
// withdrawals.js/investment-schedule.js와 같은 패턴: index.html의 SPA
// 상태와 분리, 모든 데이터는 REST API로만 저장한다. 기존 ATR 매수/손절/
// 익절 신호(index.html)와 이 화면은 완전히 별개 트랙이다 -- 여기서는
// 사용자가 직접 정한 트리거·단계를 등록·조회할 뿐, 프로그램이 타당성을
// 판단하지 않는다.
import { api } from './api-client.js';

const el = {};
function cacheDom() {
  const ids = [
    'btn-close', 'btn-add-plan', 'filter-status', 'tp-list', 'tp-empty',
    'modal-plan', 'plan-label', 'plan-trigger-price', 'plan-trigger-direction',
    'plan-confirm-mode', 'plan-reference', 'instrument-rows', 'btn-add-instrument-row',
    'tier-rows', 'btn-add-tier-row', 'plan-purpose', 'plan-invalidation',
    'plan-review-date', 'plan-reason', 'btn-plan-cancel', 'btn-plan-save',
    'modal-cancel-confirm', 'cancel-reason', 'btn-cancel-close', 'btn-cancel-confirm',
    'modal-history', 'history-list', 'btn-history-close',
    'toast',
  ];
  ids.forEach((id) => {
    const camel = id.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    el[camel] = document.getElementById(id);
  });
}

const state = {
  instruments: [],
  plans: [],
  cancelTargetId: null,
};

// ---------- 유틸 ----------
function escapeText(s) {
  const div = document.createElement('div');
  div.textContent = s == null ? '' : String(s);
  return div.innerHTML;
}

let toastTimer = null;
function showToast(msg) {
  el.toast.textContent = msg;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, 2600);
}

const STATUS_LABEL = {
  ARMED: '감시중', ACTIVE: '활성', PARTIALLY_FIRED: '일부발동',
  COMPLETED: '완료', CANCELLED: '취소됨',
};

function fmtPrice(value, currency) {
  if (value == null) return '-';
  if (currency === 'USD') return `${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}달러`;
  if (currency === 'KRW') return `${Math.round(value).toLocaleString()}원`;
  return `${value} ${currency || ''}`;
}

function instrumentCurrency(instrumentId) {
  const inst = state.instruments.find((i) => i.id === instrumentId);
  return inst ? inst.currency : '';
}

// ---------- 종목 select 채우기 ----------
function fillInstrumentSelect(selectEl, selectedId) {
  selectEl.innerHTML = '';
  state.instruments.forEach((inst) => {
    const opt = document.createElement('option');
    opt.value = inst.id;
    opt.textContent = `${inst.name} (${inst.currency}${inst.kis_code ? ', ' + inst.kis_code : ''})`;
    if (inst.id === selectedId) opt.selected = true;
    selectEl.appendChild(opt);
  });
}

// ---------- 계획 목록 ----------
async function loadPlans() {
  const status = el.filterStatus.value;
  state.plans = await api.listTradePlans(status ? { status } : {});
  renderPlanList();
}

function renderPlanList() {
  el.tpList.innerHTML = '';
  el.tpEmpty.hidden = state.plans.length > 0;

  state.plans.forEach((plan) => {
    const card = document.createElement('div');
    card.className = 'tp-card';

    const refInstrument = plan.instruments.find((i) => i.instrument_id === plan.price_reference_instrument_id);
    const currency = refInstrument ? refInstrument.currency : '';

    const instrumentNames = plan.instruments.map((i) => `${escapeText(i.instrument_name)}(${i.baseline_quantity}주)`).join(', ');
    const tierLines = plan.tiers.map((t) => {
      const firedMark = t.fired_at ? ' ✅발동' : '';
      return `<li>${t.tier_order}차: 최고가 대비 -${t.pullback_pct}%, ${t.sell_pct}%${firedMark}</li>`;
    }).join('');

    card.innerHTML = `
      <div class="tp-card-header">
        <span class="tp-status tp-status-${escapeText(plan.lifecycle_status)}">${escapeText(STATUS_LABEL[plan.lifecycle_status] || plan.lifecycle_status)}</span>
        <span class="tp-label">${escapeText(plan.label)}</span>
      </div>
      <div class="tp-card-body">
        <div>트리거: ${escapeText(fmtPrice(plan.trigger_price, currency))} (${plan.trigger_direction === 'ABOVE' ? '상향' : '하향'})</div>
        ${plan.peak_price_since_trigger != null ? `<div>활성화 이후 최고가: ${escapeText(fmtPrice(plan.peak_price_since_trigger, currency))}</div>` : ''}
        <div>연결 종목: ${instrumentNames}</div>
        ${tierLines ? `<ul class="tp-tier-list">${tierLines}</ul>` : ''}
        ${plan.reason ? `<div class="tp-reason">설정 이유: ${escapeText(plan.reason)}</div>` : ''}
      </div>
      <div class="tp-card-actions">
        <button class="btn btn-secondary btn-small" data-action="history">이력</button>
        ${plan.lifecycle_status !== 'CANCELLED' && plan.lifecycle_status !== 'COMPLETED'
          ? '<button class="btn btn-danger btn-small" data-action="cancel">계획 취소</button>' : ''}
      </div>
    `;

    card.querySelector('[data-action="history"]').addEventListener('click', () => openHistoryModal(plan.id));
    const cancelBtn = card.querySelector('[data-action="cancel"]');
    if (cancelBtn) cancelBtn.addEventListener('click', () => openCancelModal(plan.id));

    el.tpList.appendChild(card);
  });
}

// ---------- 계획 생성 모달 ----------
function addInstrumentRow(selectedId) {
  const row = document.createElement('div');
  row.className = 'tp-instrument-row';
  const selectEl = document.createElement('select');
  selectEl.className = 'tp-row-instrument';
  fillInstrumentSelect(selectEl, selectedId);
  const qtyEl = document.createElement('input');
  qtyEl.type = 'number';
  qtyEl.min = '0.0001';
  qtyEl.step = 'any';
  qtyEl.placeholder = '수량(baseline)';
  qtyEl.className = 'tp-row-qty';
  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'btn btn-secondary btn-small';
  removeBtn.textContent = '삭제';
  removeBtn.addEventListener('click', () => row.remove());

  row.appendChild(selectEl);
  row.appendChild(qtyEl);
  row.appendChild(removeBtn);
  el.instrumentRows.appendChild(row);
}

function addTierRow(order) {
  const row = document.createElement('div');
  row.className = 'tp-tier-row';
  const orderEl = document.createElement('input');
  orderEl.type = 'number';
  orderEl.min = '1';
  orderEl.value = String(order);
  orderEl.className = 'tp-row-order';
  orderEl.title = '단계 순서';
  const pullbackEl = document.createElement('input');
  pullbackEl.type = 'number';
  pullbackEl.min = '0.01';
  pullbackEl.step = 'any';
  pullbackEl.placeholder = '최고가 대비 하락 %';
  pullbackEl.className = 'tp-row-pullback';
  const sellEl = document.createElement('input');
  sellEl.type = 'number';
  sellEl.min = '0.01';
  sellEl.max = '100';
  sellEl.step = 'any';
  sellEl.placeholder = '매도 %';
  sellEl.className = 'tp-row-sell';
  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'btn btn-secondary btn-small';
  removeBtn.textContent = '삭제';
  removeBtn.addEventListener('click', () => row.remove());

  row.appendChild(orderEl);
  row.appendChild(pullbackEl);
  row.appendChild(sellEl);
  row.appendChild(removeBtn);
  el.tierRows.appendChild(row);
}

function resetPlanForm() {
  el.planLabel.value = '';
  el.planTriggerPrice.value = '';
  el.planTriggerDirection.value = 'ABOVE';
  el.planConfirmMode.value = 'CLOSE';
  fillInstrumentSelect(el.planReference, null);
  el.instrumentRows.innerHTML = '';
  el.tierRows.innerHTML = '';
  addInstrumentRow(null);
  addTierRow(1);
  el.planPurpose.value = '';
  el.planInvalidation.value = '';
  el.planReviewDate.value = '';
  el.planReason.value = '';
}

function openAddModal() {
  resetPlanForm();
  el.modalPlan.hidden = false;
}

function closePlanModal() {
  el.modalPlan.hidden = true;
}

function collectPlanPayload() {
  const label = el.planLabel.value.trim();
  const triggerPrice = parseFloat(el.planTriggerPrice.value);
  if (!label || !Number.isFinite(triggerPrice) || triggerPrice <= 0) {
    showToast('계획 이름과 트리거 가격을 정확히 입력하세요.');
    return null;
  }
  const referenceId = el.planReference.value;
  if (!referenceId) {
    showToast('대표 가격 종목을 선택하세요.');
    return null;
  }

  const instruments = [];
  el.instrumentRows.querySelectorAll('.tp-instrument-row').forEach((row) => {
    const instrumentId = row.querySelector('.tp-row-instrument').value;
    const qty = parseFloat(row.querySelector('.tp-row-qty').value);
    if (instrumentId && Number.isFinite(qty) && qty > 0) {
      instruments.push({ instrument_id: instrumentId, baseline_quantity: qty });
    }
  });
  if (instruments.length === 0) {
    showToast('연결 종목과 수량을 최소 1개 입력하세요.');
    return null;
  }
  if (!instruments.some((i) => i.instrument_id === referenceId)) {
    showToast('대표 가격 종목은 연결 종목 중 하나여야 합니다.');
    return null;
  }

  const tiers = [];
  el.tierRows.querySelectorAll('.tp-tier-row').forEach((row) => {
    const tierOrder = parseInt(row.querySelector('.tp-row-order').value, 10);
    const pullbackPct = parseFloat(row.querySelector('.tp-row-pullback').value);
    const sellPct = parseFloat(row.querySelector('.tp-row-sell').value);
    if (Number.isFinite(tierOrder) && Number.isFinite(pullbackPct) && Number.isFinite(sellPct)) {
      tiers.push({ tier_order: tierOrder, pullback_pct: pullbackPct, sell_pct: sellPct });
    }
  });

  return {
    plan_type: 'TRAIL',
    label,
    trigger_price: triggerPrice,
    trigger_direction: el.planTriggerDirection.value,
    confirm_mode: el.planConfirmMode.value,
    price_reference_instrument_id: referenceId,
    instruments,
    tiers,
    purpose: el.planPurpose.value.trim() || null,
    invalidation_condition: el.planInvalidation.value.trim() || null,
    review_date: el.planReviewDate.value || null,
    reason: el.planReason.value.trim() || null,
  };
}

async function savePlan() {
  const payload = collectPlanPayload();
  if (!payload) return;
  try {
    await api.createTradePlan(payload);
    closePlanModal();
    showToast('매매계획을 등록했습니다.');
    await loadPlans();
  } catch (e) {
    showToast(`저장 실패: ${e.message}`);
  }
}

// ---------- 취소 모달 ----------
function openCancelModal(planId) {
  state.cancelTargetId = planId;
  el.cancelReason.value = '';
  el.modalCancelConfirm.hidden = false;
}

function closeCancelModal() {
  el.modalCancelConfirm.hidden = true;
  state.cancelTargetId = null;
}

async function confirmCancel() {
  const reason = el.cancelReason.value.trim();
  if (!reason) {
    showToast('취소 이유를 입력하세요.');
    return;
  }
  try {
    await api.cancelTradePlan(state.cancelTargetId, reason);
    closeCancelModal();
    showToast('계획을 취소했습니다.');
    await loadPlans();
  } catch (e) {
    showToast(`취소 실패: ${e.message}`);
  }
}

// ---------- 이력 모달 ----------
async function openHistoryModal(planId) {
  try {
    const history = await api.getTradePlanHistory(planId);
    el.historyList.innerHTML = history.length
      ? history.map((h) => `
        <div class="tp-history-item">
          <div class="tp-history-version">v${h.version} -- ${escapeText(h.changed_at)}</div>
          <div class="tp-history-reason">${escapeText(h.change_reason || '(사유 없음)')}</div>
        </div>
      `).join('')
      : '<p>변경 이력이 없습니다.</p>';
    el.modalHistory.hidden = false;
  } catch (e) {
    showToast(`이력 조회 실패: ${e.message}`);
  }
}

// ---------- 초기화 ----------
async function init() {
  cacheDom();

  el.btnClose.addEventListener('click', () => window.close());
  el.btnAddPlan.addEventListener('click', openAddModal);
  el.filterStatus.addEventListener('change', loadPlans);
  el.btnAddInstrumentRow.addEventListener('click', () => addInstrumentRow(null));
  el.btnAddTierRow.addEventListener('click', () => addTierRow(el.tierRows.children.length + 1));
  el.btnPlanCancel.addEventListener('click', closePlanModal);
  el.btnPlanSave.addEventListener('click', savePlan);
  el.btnCancelClose.addEventListener('click', closeCancelModal);
  el.btnCancelConfirm.addEventListener('click', confirmCancel);
  el.btnHistoryClose.addEventListener('click', () => { el.modalHistory.hidden = true; });

  try {
    state.instruments = await api.listInstruments();
  } catch (e) {
    showToast(`종목 목록 조회 실패: ${e.message}`);
    state.instruments = [];
  }

  await loadPlans();
}

init();
