// frontend/js/trade-plans.js -- 매매계획(트리거 감시) 독립 페이지.
// 2026-08-08 개편: 일반 사용자가 대표 종목/트리거 가격/매도 단계만 입력하면
// 저장할 수 있도록 기본화면을 단순화했다(기존 세부 입력항목은 삭제하지
// 않고 상세설정 접기 영역으로 이동, frontend/trade-plans.html 참고).
// 이 화면은 index.html의 SPA 상태와 분리된 독립 페이지이고, 기존 ATR
// 매수/손절/익절 신호와도 완전히 별개 트랙이다 -- 여기서는 사용자가 직접
// 정한 트리거·단계를 등록·조회할 뿐, 프로그램이 타당성을 판단하지 않는다.
import { api } from './api-client.js';

const el = {};
function cacheDom() {
  const ids = [
    'btn-close', 'btn-add-plan', 'filter-status', 'tp-list', 'tp-empty',
    'modal-plan',
    'plan-instrument-picker', 'single-instrument-info', 'info-holding-qty',
    'info-baseline-qty', 'info-current-price',
    'linked-accounts-box', 'linked-total-qty', 'linked-account-count',
    'btn-toggle-linked-accounts', 'linked-accounts-detail',
    'instrument-link-note', 'duplicate-plan-warning', 'plan-form-body',
    'plan-trigger-price', 'trigger-currency-unit', 'trigger-hint',
    'simple-tier-rows', 'btn-add-simple-tier', 'tier-total-summary',
    'btn-toggle-advanced', 'advanced-settings',
    'plan-label', 'plan-trigger-direction', 'plan-confirm-mode', 'plan-reference',
    'instrument-rows', 'btn-add-instrument-row',
    'plan-purpose', 'plan-invalidation', 'plan-review-date', 'plan-reason',
    'btn-plan-cancel', 'btn-plan-save',
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
  instruments: [],     // dashboard에서 펼친 [{id, name, currency, kis_code, kis_market, quantity, price}]
  plans: [],            // 현재 화면 필터에 따른 목록(카드 렌더링용)
  allPlansForDupCheck: [], // 계획 추가 모달을 열 때마다 새로 받는 전체 계획(상태 무관)
  cancelTargetId: null,
  form: null,            // 계획 생성 폼의 단일 진실 공급원(formState) -- 기본/상세 UI가 함께 읽고 쓴다
};

const NON_TERMINAL_STATUSES = new Set(['ARMED', 'ACTIVE', 'PARTIALLY_FIRED']);

// ---------------------------------------------------------------------------
// 유틸
// ---------------------------------------------------------------------------
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

// frontend/trade-plans.html 8절 "기술상태 한글 번역" 요구사항 그대로 반영.
const STATUS_LABEL = {
  ARMED: '감시 준비',
  ACTIVE: '최고가 추적 중',
  PARTIALLY_FIRED: '일부 매도단계 도달',
  COMPLETED: '계획 완료',
  CANCELLED: '계획 취소',
  DATA_STALE: '시세 확인 필요',
};

function fmtQty(n) {
  const rounded = Math.round(n * 100) / 100;
  return `${rounded.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}주`;
}

function fmtPrice(value, currency) {
  if (value == null) return '-';
  if (currency === 'USD') return `${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`;
  if (currency === 'KRW') return `${Math.round(value).toLocaleString()}원`;
  return `${value} ${currency || ''}`;
}

function currencyUnitLabel(currency) {
  if (currency === 'USD') return 'USD';
  if (currency === 'KRW') return '원';
  return currency || '';
}

// ---------------------------------------------------------------------------
// frontend/js/trade-plans.js: 계좌별 매도수량 계산 -- backend/atrsite/services/
// trade_plan_engine.py의 compute_tier_sell_quantities()/is_cumulative_final_tier()
// 를 화면 미리보기용으로 그대로 이식(최종 계산은 여전히 서버가 한다. 이건
// 저장 전 "예상수량" 표시만을 위한 것).
// ---------------------------------------------------------------------------
function isCumulativeFinalTier(tiers, uptoIndex) {
  let cumulative = 0;
  for (let i = 0; i <= uptoIndex; i++) cumulative += tiers[i].sellPct;
  return cumulative >= 100 - 0.01;
}

function computeTierSellQuantities(baselineByInstrument, sellPct, isFinalTier, alreadyRecommended) {
  const already = alreadyRecommended || {};
  const ids = Object.keys(baselineByInstrument);

  if (isFinalTier) {
    const result = {};
    ids.forEach((iid) => {
      result[iid] = Math.round(baselineByInstrument[iid] - (already[iid] || 0));
    });
    return result;
  }

  const raw = {};
  const floors = {};
  let totalBaseline = 0;
  ids.forEach((iid) => {
    raw[iid] = baselineByInstrument[iid] * sellPct / 100;
    floors[iid] = Math.floor(raw[iid]);
    totalBaseline += baselineByInstrument[iid];
  });
  const totalTarget = Math.round(totalBaseline * sellPct / 100);
  const floorSum = ids.reduce((sum, iid) => sum + floors[iid], 0);
  const remainder = totalTarget - floorSum;
  if (remainder > 0) {
    const order = [...ids].sort((a, b) => (raw[b] - floors[b]) - (raw[a] - floors[a]));
    const result = { ...floors };
    for (let i = 0; i < remainder && i < order.length; i++) result[order[i]] += 1;
    return result;
  }
  return floors;
}

function computeAllTierQuantities(baselineByInstrument, tiers) {
  // tiers: [{pullbackPct, sellPct}] 순서대로. 각 단계의 계좌별 매도수량과,
  // 그 단계까지 누적된 already를 반환한다(체이닝 -- Phase 1 백엔드와 동일 원칙).
  let already = {};
  const perTier = [];
  tiers.forEach((tier, idx) => {
    const isFinal = isCumulativeFinalTier(tiers, idx);
    const qty = computeTierSellQuantities(baselineByInstrument, tier.sellPct, isFinal, already);
    perTier.push({ tier, isFinal, quantities: qty });
    already = { ...already };
    Object.entries(qty).forEach(([iid, q]) => { already[iid] = (already[iid] || 0) + q; });
  });
  return perTier;
}

// ---------------------------------------------------------------------------
// 동일 KIS 코드 자동연결(frontend/trade-plans.html "대표 종목 선택" 절)
// ---------------------------------------------------------------------------
function buildLinkedGroup(chosenInstrumentId) {
  const chosen = state.instruments.find((i) => i.id === chosenInstrumentId);
  if (!chosen) return [];
  if (!chosen.kis_code) return [chosen];

  const siblings = state.instruments.filter((i) => (
    i.id !== chosen.id
    && i.kis_code === chosen.kis_code
    && i.kis_market === chosen.kis_market
    && i.currency === chosen.currency
    && i.quantity > 0
  ));
  return [chosen, ...siblings];
}

function hasDuplicateActivePlan(groupInstrumentIds) {
  const idSet = new Set(groupInstrumentIds);
  return state.allPlansForDupCheck.some((plan) => (
    NON_TERMINAL_STATUSES.has(plan.lifecycle_status)
    && plan.instruments.some((pi) => idSet.has(pi.instrument_id))
  ));
}

function autoPlanLabel(chosen) {
  return `${chosen.name} 매매계획`;
}

// ---------------------------------------------------------------------------
// 계획 목록(카드) 렌더링
// ---------------------------------------------------------------------------
async function loadPlans() {
  const status = el.filterStatus.value;
  state.plans = await api.listTradePlans(status ? { status } : {});
  renderPlanList();
}

function renderTierPreviewLines(plan, currency) {
  const baselineByInstrument = {};
  plan.instruments.forEach((i) => { baselineByInstrument[i.instrument_id] = i.baseline_quantity; });
  const tiers = plan.tiers.map((t) => ({ pullbackPct: t.pullback_pct, sellPct: t.sell_pct }));
  const perTier = computeAllTierQuantities(baselineByInstrument, tiers);

  return plan.tiers.map((t, idx) => {
    const totalQty = Object.values(perTier[idx].quantities).reduce((a, b) => a + b, 0);
    const firedMark = t.fired_at ? ' -- 발동됨' : '';
    return `<li>${t.tier_order}차: 최고가 대비 -${t.pullback_pct}% / ${fmtQty(totalQty)}${firedMark}</li>`;
  }).join('');
}

function renderPlanList() {
  el.tpList.innerHTML = '';
  el.tpEmpty.hidden = state.plans.length > 0;

  state.plans.forEach((plan) => {
    const card = document.createElement('div');
    card.className = 'tp-card';

    const refInstrument = plan.instruments.find((i) => i.instrument_id === plan.price_reference_instrument_id);
    const currency = refInstrument ? refInstrument.currency : '';
    const totalBaseline = plan.instruments.reduce((sum, i) => sum + i.baseline_quantity, 0);

    const tierLines = renderTierPreviewLines(plan, currency);
    const peakLine = plan.peak_price_since_trigger != null
      ? `<div>현재 최고가: ${escapeText(fmtPrice(plan.peak_price_since_trigger, currency))}</div>`
      : '<div>현재 최고가: 아직 없음</div>';

    const accountLine = plan.instruments.length > 1
      ? `<div>연결 계좌: ${plan.instruments.length}개</div>`
      : '';

    card.innerHTML = `
      <div class="tp-card-header">
        <span class="tp-status tp-status-${escapeText(plan.lifecycle_status)}">${escapeText(STATUS_LABEL[plan.lifecycle_status] || plan.lifecycle_status)}</span>
        <span class="tp-label">${escapeText(plan.label)}</span>
      </div>
      <div class="tp-card-body">
        <div>트리거: ${escapeText(fmtPrice(plan.trigger_price, currency))}</div>
        ${peakLine}
        ${tierLines ? `<ul class="tp-tier-list">${tierLines}</ul>` : ''}
        <div>기준수량: ${fmtQty(totalBaseline)}</div>
        ${accountLine}
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

// ---------------------------------------------------------------------------
// 계획 생성 모달 -- 기본 화면
// ---------------------------------------------------------------------------
function fillInstrumentPicker() {
  el.planInstrumentPicker.innerHTML = '<option value="">종목을 선택하세요</option>';
  state.instruments.forEach((inst) => {
    const opt = document.createElement('option');
    opt.value = inst.id;
    opt.textContent = `${inst.name} (${inst.currency}${inst.kis_code ? ', ' + inst.kis_code : ''})`;
    el.planInstrumentPicker.appendChild(opt);
  });
}

function fillReferenceSelect(candidateInstruments, selectedId) {
  el.planReference.innerHTML = '';
  candidateInstruments.forEach((inst) => {
    const opt = document.createElement('option');
    opt.value = inst.id;
    opt.textContent = inst.name;
    if (inst.id === selectedId) opt.selected = true;
    el.planReference.appendChild(opt);
  });
}

function renderAdvancedInstrumentRows() {
  el.instrumentRows.innerHTML = '';
  state.form.instruments.forEach((row) => {
    addAdvancedInstrumentRow(row.instrumentId, row.baselineQuantity);
  });
}

function addAdvancedInstrumentRow(selectedId, qtyValue) {
  const row = document.createElement('div');
  row.className = 'tp-instrument-row';
  const selectEl = document.createElement('select');
  selectEl.className = 'tp-row-instrument';
  state.instruments.forEach((inst) => {
    const opt = document.createElement('option');
    opt.value = inst.id;
    opt.textContent = `${inst.name} (${inst.currency}${inst.kis_code ? ', ' + inst.kis_code : ''})`;
    if (inst.id === selectedId) opt.selected = true;
    selectEl.appendChild(opt);
  });
  const qtyEl = document.createElement('input');
  qtyEl.type = 'number';
  qtyEl.min = '0.0001';
  qtyEl.step = 'any';
  qtyEl.placeholder = '기준수량';
  qtyEl.className = 'tp-row-qty';
  if (qtyValue != null) qtyEl.value = String(qtyValue);
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

function updateInstrumentInfoDisplay() {
  const group = state.form.instruments;
  const currency = state.form.currency;

  if (group.length <= 1) {
    el.singleInstrumentInfo.hidden = false;
    el.linkedAccountsBox.hidden = true;
    const only = group[0];
    el.infoHoldingQty.textContent = only ? fmtQty(only.baselineQuantity) : '-';
    el.infoBaselineQty.textContent = only ? fmtQty(only.baselineQuantity) : '-';
    el.infoCurrentPrice.textContent = fmtPrice(state.form.currentPrice, currency);
    el.instrumentLinkNote.textContent = '';
  } else {
    el.singleInstrumentInfo.hidden = true;
    el.linkedAccountsBox.hidden = false;
    const total = group.reduce((sum, r) => sum + r.baselineQuantity, 0);
    el.linkedTotalQty.textContent = fmtQty(total);
    el.linkedAccountCount.textContent = String(group.length);
    el.linkedAccountsDetail.innerHTML = group.map((r) => {
      const inst = state.instruments.find((i) => i.id === r.instrumentId);
      return `<div>${escapeText(inst ? inst.name : r.instrumentId)}: ${fmtQty(r.baselineQuantity)}</div>`;
    }).join('');
    el.instrumentLinkNote.textContent = '동일 종목코드로 등록된 계좌를 자동으로 함께 연결했어요. 실제 매매는 계좌별로 직접 확인하세요.';
  }
}

function refreshTriggerHint() {
  const price = parseFloat(el.planTriggerPrice.value);
  const unit = currencyUnitLabel(state.form.currency);
  const unitGap = unit === '원' ? '' : ' '; // "717 USD" 이지만 "115,000원"은 붙여쓴다
  if (Number.isFinite(price) && price > 0) {
    el.triggerHint.textContent = `${price.toLocaleString()}${unitGap}${unit}에 도달하면 매도하지 않고 최고가 추적을 시작해요.`;
  } else {
    el.triggerHint.textContent = '';
  }
}

function addSimpleTierRow(pullbackPct, sellPct) {
  const idx = state.form.tiers.length;
  state.form.tiers.push({ pullbackPct: pullbackPct ?? null, sellPct: sellPct ?? null });
  renderSimpleTierRows();
}

function renderSimpleTierRows() {
  el.simpleTierRows.innerHTML = '';
  state.form.tiers.forEach((tier, idx) => {
    const row = document.createElement('div');
    row.className = 'tp-simple-tier-row';

    const orderLabel = document.createElement('div');
    orderLabel.className = 'tp-tier-order-label';
    orderLabel.textContent = `${idx + 1}차`;

    const pullbackWrap = document.createElement('div');
    pullbackWrap.className = 'tp-tier-input-wrap';
    const pullbackLabel = document.createElement('label');
    pullbackLabel.textContent = '최고가에서 하락';
    const pullbackInput = document.createElement('input');
    pullbackInput.type = 'number';
    pullbackInput.min = '0.01';
    pullbackInput.step = 'any';
    pullbackInput.className = 'tp-simple-pullback';
    pullbackInput.placeholder = '예: 1.25';
    if (tier.pullbackPct != null) pullbackInput.value = String(tier.pullbackPct);
    pullbackInput.addEventListener('input', () => {
      state.form.tiers[idx].pullbackPct = parseFloat(pullbackInput.value) || null;
      refreshTierPreview();
    });
    const pullbackUnit = document.createElement('span');
    pullbackUnit.className = 'tp-unit';
    pullbackUnit.textContent = '%';
    pullbackWrap.appendChild(pullbackLabel);
    const pullbackInputRow = document.createElement('div');
    pullbackInputRow.className = 'tp-tier-input-row';
    pullbackInputRow.appendChild(pullbackInput);
    pullbackInputRow.appendChild(pullbackUnit);
    pullbackWrap.appendChild(pullbackInputRow);

    const sellWrap = document.createElement('div');
    sellWrap.className = 'tp-tier-input-wrap';
    const sellLabel = document.createElement('label');
    sellLabel.textContent = '기준수량 매도 의견';
    const sellInput = document.createElement('input');
    sellInput.type = 'number';
    sellInput.min = '0.01';
    sellInput.max = '100';
    sellInput.step = 'any';
    sellInput.className = 'tp-simple-sell';
    sellInput.placeholder = '예: 40';
    if (tier.sellPct != null) sellInput.value = String(tier.sellPct);
    sellInput.addEventListener('input', () => {
      state.form.tiers[idx].sellPct = parseFloat(sellInput.value) || null;
      refreshTierPreview();
    });
    const sellUnit = document.createElement('span');
    sellUnit.className = 'tp-unit';
    sellUnit.textContent = '%';
    sellWrap.appendChild(sellLabel);
    const sellInputRow = document.createElement('div');
    sellInputRow.className = 'tp-tier-input-row';
    sellInputRow.appendChild(sellInput);
    sellInputRow.appendChild(sellUnit);
    sellWrap.appendChild(sellInputRow);

    const previewLine = document.createElement('div');
    previewLine.className = 'tp-tier-preview';
    previewLine.id = `tier-preview-${idx}`;

    const rowActions = document.createElement('div');
    rowActions.className = 'tp-tier-row-actions';
    if (state.form.tiers.length > 1) {
      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'btn btn-secondary btn-small';
      removeBtn.textContent = '단계 삭제';
      removeBtn.addEventListener('click', () => {
        state.form.tiers.splice(idx, 1);
        renderSimpleTierRows();
        refreshTierPreview();
      });
      rowActions.appendChild(removeBtn);
    }

    row.appendChild(orderLabel);
    row.appendChild(pullbackWrap);
    row.appendChild(sellWrap);
    row.appendChild(previewLine);
    row.appendChild(rowActions);
    el.simpleTierRows.appendChild(row);
  });
  refreshTierPreview();
}

function refreshTierPreview() {
  const baselineByInstrument = {};
  state.form.instruments.forEach((r) => { baselineByInstrument[r.instrumentId] = r.baselineQuantity; });
  const totalBaseline = state.form.instruments.reduce((s, r) => s + r.baselineQuantity, 0);

  const validTiers = state.form.tiers.filter((t) => t.pullbackPct != null && t.sellPct != null && t.sellPct > 0);
  const perTier = computeAllTierQuantities(baselineByInstrument, validTiers);

  let cumulativeSellPct = 0;
  let validIdx = 0;
  state.form.tiers.forEach((tier, idx) => {
    const previewEl = document.getElementById(`tier-preview-${idx}`);
    if (!previewEl) return;
    if (tier.pullbackPct == null || tier.sellPct == null || tier.sellPct <= 0) {
      previewEl.textContent = '';
      return;
    }
    const result = perTier[validIdx];
    validIdx += 1;
    const soldQty = Object.values(result.quantities).reduce((a, b) => a + b, 0);
    cumulativeSellPct += tier.sellPct;

    if (state.form.instruments.length > 1) {
      const perAccount = Object.entries(result.quantities).map(([iid, q]) => {
        const inst = state.instruments.find((i) => i.id === iid);
        return `${inst ? inst.name : iid} ${fmtQty(q)}`;
      }).join(', ');
      previewEl.textContent = `예상 매도수량 총 ${fmtQty(soldQty)} (${perAccount})`;
    } else {
      previewEl.textContent = `예상 매도수량 ${fmtQty(soldQty)}`;
    }
  });

  // 마지막 유효 단계 이후 예상 잔여수량 + 누적 100% 여부 안내
  let soldSoFar = 0;
  perTier.forEach((t) => {
    soldSoFar += Object.values(t.quantities).reduce((a, b) => a + b, 0);
  });
  const remaining = Math.max(0, Math.round(totalBaseline - soldSoFar));

  if (validTiers.length === 0) {
    el.tierTotalSummary.textContent = '';
  } else if (cumulativeSellPct >= 100 - 0.01) {
    el.tierTotalSummary.textContent = '이 계획은 최종적으로 전량 현금화를 목표로 해요.';
  } else {
    el.tierTotalSummary.textContent = `남은 물량은 계속 보유하며 추후 계획을 추가할 수 있어요. (예상 잔여수량 ${fmtQty(remaining)})`;
  }
}

function applyInstrumentSelection(instrumentId) {
  const chosen = state.instruments.find((i) => i.id === instrumentId);
  el.planFormBody.hidden = true;
  el.duplicatePlanWarning.hidden = true;
  el.singleInstrumentInfo.hidden = true;
  el.linkedAccountsBox.hidden = true;
  el.instrumentLinkNote.textContent = '';

  if (!chosen) {
    state.form = null;
    return;
  }

  const group = buildLinkedGroup(instrumentId);
  const groupIds = group.map((g) => g.id);

  if (hasDuplicateActivePlan(groupIds)) {
    el.duplicatePlanWarning.hidden = false;
    state.form = null;
    return;
  }

  state.form = {
    referenceId: chosen.id,
    currency: chosen.currency,
    currentPrice: chosen.price,
    label: '',
    triggerDirection: 'ABOVE',
    confirmMode: 'CLOSE',
    purpose: '',
    invalidation: '',
    reviewDate: '',
    reason: '',
    instruments: group.map((g) => ({ instrumentId: g.id, baselineQuantity: g.quantity })),
    tiers: [{ pullbackPct: null, sellPct: null }],
  };

  if (group.length > 1) {
    el.instrumentLinkNote.textContent = `${chosen.name} 가격만 감시합니다. 동일 종목코드의 다른 계좌를 자동으로 함께 연결했어요.`;
  } else if (isLikelyEtfGroupCandidate(chosen)) {
    el.instrumentLinkNote.textContent = `${chosen.name} 가격만 감시합니다. 관련 ETF의 실제 매매는 사용자가 직접 판단합니다.`;
  }

  updateInstrumentInfoDisplay();
  fillReferenceSelect(group, chosen.id);
  el.planLabel.value = '';
  el.planLabel.placeholder = autoPlanLabel(chosen);
  el.planTriggerDirection.value = 'ABOVE';
  el.planConfirmMode.value = 'CLOSE';
  el.planPurpose.value = '';
  el.planInvalidation.value = '';
  el.planReviewDate.value = '';
  el.planReason.value = '';
  renderAdvancedInstrumentRows();
  el.planTriggerPrice.value = '';
  refreshTriggerHint();
  renderSimpleTierRows();

  el.planFormBody.hidden = false;
}

function isLikelyEtfGroupCandidate(inst) {
  // 나스닥100 계열처럼 "관련은 있지만 자동연결하면 안 되는" 종목에 안내
  // 문구를 보여주기 위한 느슨한 판정(QQQM/IQQ/ACE/TIGER 등) -- 자동연결
  // 로직 자체와는 무관하고 순수 안내 문구용이다.
  const name = (inst.name || '').toUpperCase();
  return /QQQ|나스닥|NASDAQ/.test(name);
}

function resetPlanModal() {
  fillInstrumentPicker();
  el.planInstrumentPicker.value = '';
  el.planFormBody.hidden = true;
  el.duplicatePlanWarning.hidden = true;
  el.advancedSettings.hidden = true;
  el.btnToggleAdvanced.textContent = '상세설정 펼치기 ▾';
  el.linkedAccountsDetail.hidden = true;
  el.btnToggleLinkedAccounts.textContent = '계좌별 수량 보기 ▾';
  state.form = null;
}

async function openAddModal() {
  try {
    state.allPlansForDupCheck = await api.listTradePlans({});
  } catch (e) {
    state.allPlansForDupCheck = [];
  }
  resetPlanModal();
  el.modalPlan.hidden = false;
}

function closePlanModal() {
  el.modalPlan.hidden = true;
}

// ---------------------------------------------------------------------------
// 저장
// ---------------------------------------------------------------------------
function syncAdvancedIntoForm() {
  // 상세설정을 열어서 직접 수정했다면 그 값을 formState에 반영한다(상세설정을
  // 아예 안 열었으면 기본화면에서 자동 계산된 값을 그대로 쓴다).
  if (el.advancedSettings.hidden) return;

  state.form.label = el.planLabel.value.trim();
  state.form.triggerDirection = el.planTriggerDirection.value;
  state.form.confirmMode = el.planConfirmMode.value;
  state.form.referenceId = el.planReference.value || state.form.referenceId;
  state.form.purpose = el.planPurpose.value.trim();
  state.form.invalidation = el.planInvalidation.value.trim();
  state.form.reviewDate = el.planReviewDate.value;
  state.form.reason = el.planReason.value.trim();

  const rows = [];
  el.instrumentRows.querySelectorAll('.tp-instrument-row').forEach((row) => {
    const instrumentId = row.querySelector('.tp-row-instrument').value;
    const qty = parseFloat(row.querySelector('.tp-row-qty').value);
    if (instrumentId && Number.isFinite(qty) && qty > 0) {
      rows.push({ instrumentId, baselineQuantity: qty });
    }
  });
  if (rows.length > 0) state.form.instruments = rows;
}

function collectPlanPayload() {
  if (!state.form) {
    showToast('대표 종목을 먼저 선택하세요.');
    return null;
  }
  syncAdvancedIntoForm();

  const triggerPrice = parseFloat(el.planTriggerPrice.value);
  if (!Number.isFinite(triggerPrice) || triggerPrice <= 0) {
    showToast('트리거 가격은 0보다 커야 해요.');
    return null;
  }

  const tiers = state.form.tiers
    .filter((t) => t.pullbackPct != null && t.sellPct != null)
    .map((t, idx) => ({ tier_order: idx + 1, pullback_pct: t.pullbackPct, sell_pct: t.sellPct }));

  if (tiers.some((t) => t.pullback_pct <= 0)) {
    showToast('하락률은 0보다 커야 해요.');
    return null;
  }
  if (tiers.some((t) => t.sell_pct <= 0 || t.sell_pct > 100)) {
    showToast('매도비율은 0보다 크고 100 이하여야 해요.');
    return null;
  }
  for (let i = 1; i < tiers.length; i++) {
    if (tiers[i].pullback_pct <= tiers[i - 1].pullback_pct) {
      showToast('단계별 하락률은 앞 단계보다 커야 해요.');
      return null;
    }
  }
  const totalSellPct = tiers.reduce((s, t) => s + t.sell_pct, 0);
  if (totalSellPct > 100 + 1e-9) {
    showToast('모든 단계의 매도비율 합계는 100% 이하여야 해요.');
    return null;
  }

  const totalBaseline = state.form.instruments.reduce((s, r) => s + r.baselineQuantity, 0);
  if (totalBaseline <= 0) {
    showToast('기준수량이 0이면 저장할 수 없어요. 보유수량을 확인하세요.');
    return null;
  }

  const referenceId = state.form.instruments.some((r) => r.instrumentId === state.form.referenceId)
    ? state.form.referenceId
    : state.form.instruments[0].instrumentId;

  const chosen = state.instruments.find((i) => i.id === state.form.instruments[0].instrumentId);
  const label = state.form.label || autoPlanLabel(chosen || { name: '종목' });

  return {
    plan_type: 'TRAIL',
    label,
    trigger_price: triggerPrice,
    trigger_direction: state.form.triggerDirection,
    confirm_mode: state.form.confirmMode,
    price_reference_instrument_id: referenceId,
    instruments: state.form.instruments.map((r) => ({ instrument_id: r.instrumentId, baseline_quantity: r.baselineQuantity })),
    tiers,
    purpose: state.form.purpose || null,
    invalidation_condition: state.form.invalidation || null,
    review_date: state.form.reviewDate || null,
    reason: state.form.reason || null,
  };
}

async function savePlan() {
  const payload = collectPlanPayload();
  if (!payload) return;
  try {
    await api.createTradePlan(payload);
    closePlanModal();
    showToast('매매계획을 등록했어요.');
    await loadPlans();
  } catch (e) {
    showToast(`저장 실패: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// 취소 모달
// ---------------------------------------------------------------------------
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
    showToast('계획을 취소했어요.');
    await loadPlans();
  } catch (e) {
    showToast(`취소 실패: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// 이력 모달
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// 초기화
// ---------------------------------------------------------------------------
async function loadInstrumentsFromDashboard() {
  // frontend/js/trade-plans.js: instruments 목록만 주는 기존 API 대신
  // dashboard API를 쓴다 -- kis_code/kis_market/currency뿐 아니라 실시간
  // 보유수량(position.quantity)과 현재가(quote.price)가 한 번에 오므로,
  // 동일 KIS 코드 자동연결과 baseline 자동입력에 새 API가 필요 없다.
  const dashboard = await api.getDashboard();
  return dashboard.instruments
    .filter((row) => row.instrument.is_active)
    .map((row) => ({
      id: row.instrument.id,
      name: row.instrument.name,
      currency: row.instrument.currency,
      kis_code: row.instrument.kis_code,
      kis_market: row.instrument.kis_market,
      quantity: row.position ? row.position.quantity : 0,
      price: row.quote ? row.quote.price : null,
    }));
}

async function init() {
  cacheDom();

  el.btnClose.addEventListener('click', () => window.close());
  el.btnAddPlan.addEventListener('click', openAddModal);
  el.filterStatus.addEventListener('change', loadPlans);

  el.planInstrumentPicker.addEventListener('change', () => applyInstrumentSelection(el.planInstrumentPicker.value));
  el.planTriggerPrice.addEventListener('input', refreshTriggerHint);
  el.btnAddSimpleTier.addEventListener('click', () => addSimpleTierRow());

  el.btnToggleAdvanced.addEventListener('click', () => {
    const willOpen = el.advancedSettings.hidden;
    el.advancedSettings.hidden = !willOpen;
    el.btnToggleAdvanced.textContent = willOpen ? '상세설정 접기 ▴' : '상세설정 펼치기 ▾';
    if (willOpen && state.form) {
      el.planLabel.value = state.form.label || '';
      el.planLabel.placeholder = state.form.instruments.length
        ? autoPlanLabel(state.instruments.find((i) => i.id === state.form.referenceId) || { name: '종목' })
        : '';
    }
  });
  el.btnToggleLinkedAccounts.addEventListener('click', () => {
    const willOpen = el.linkedAccountsDetail.hidden;
    el.linkedAccountsDetail.hidden = !willOpen;
    el.btnToggleLinkedAccounts.textContent = willOpen ? '계좌별 수량 접기 ▴' : '계좌별 수량 보기 ▾';
  });
  el.btnAddInstrumentRow.addEventListener('click', () => addAdvancedInstrumentRow(null, null));

  el.btnPlanCancel.addEventListener('click', closePlanModal);
  el.btnPlanSave.addEventListener('click', savePlan);
  el.btnCancelClose.addEventListener('click', closeCancelModal);
  el.btnCancelConfirm.addEventListener('click', confirmCancel);
  el.btnHistoryClose.addEventListener('click', () => { el.modalHistory.hidden = true; });

  try {
    state.instruments = await loadInstrumentsFromDashboard();
  } catch (e) {
    showToast(`종목 목록 조회 실패: ${e.message}`);
    state.instruments = [];
  }

  await loadPlans();
}

init();
