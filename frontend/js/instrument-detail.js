// 화면 2: 종목 상세. 옛 app.js의 renderDetail()/renderDrawdown()/renderPartialSell()/
// renderTxHistory()에 대응. 평균단가·다음매수가·익절가·손절선·신호는 이제
// 서버가 계산해서 내려주므로 여기서는 그 값을 그대로 표시만 한다.
import { api } from './api-client.js';
import {
  formatMoney, formatPercent, formatPrice, formatQty, num, pnlClass,
} from './formatters.js';

const DRAWDOWN_THRESHOLDS = [5, 10, 15, 20, 25, 30];
const PARTIAL_SELL_PRESETS = [10, 20, 30, 50];

const SIGNAL_TEXT = {
  SELL_BOTH_TRIGGERED: { cls: 'signal-red', text: '🔴 매도 신호 도달 — 추적 손절선과 익절 조건이 동시에 충족되었습니다' },
  STOP_TRIGGERED: { cls: 'signal-red', text: '🔴 방어 매도 기준 도달 — 매도를 고려하세요' },
  TAKE_PROFIT_TRIGGERED: { cls: 'signal-orange', text: '🟠 익절 기준 도달 — 분할 매도를 고려하세요' },
  BUY_TRIGGERED: { cls: 'signal-green', text: '🟢 매수 기준 도달 — 추가 매수를 고려하세요' },
  NEUTRAL: { cls: 'signal-yellow', text: '🟡 관망 구간' },
};

// 이 상태들은 "확인" 없이 가격이 반등한다고 조용히 배너가 사라지면 안 된다
// (사용자 결정, 2026-08-02) -- 사람이 직접 확인 버튼을 눌러야 꺼진다.
const TRIGGER_STATUSES = new Set([
  'BUY_TRIGGERED', 'TAKE_PROFIT_TRIGGERED', 'STOP_TRIGGERED', 'SELL_BOTH_TRIGGERED',
]);

function computeDrawdownLevels(postEntryHigh) {
  if (postEntryHigh == null) return [];
  return DRAWDOWN_THRESHOLDS.map((pct) => ({ pct, price: postEntryHigh * (1 - pct / 100) }));
}

function computeDrawdownBracketLabel(dropPct) {
  if (dropPct <= 0) return '고점권 (하락 없음)';
  for (const t of DRAWDOWN_THRESHOLDS) {
    if (dropPct <= t) return `-${t}% 이내`;
  }
  return '-30% 초과';
}

function computePartialSell(position, currentPrice, pct) {
  const qty = position.quantity * (pct / 100);
  const realizedPnl = currentPrice == null ? null : (currentPrice - position.avg_price) * qty;
  return { qty, realizedPnl };
}

export async function loadAndRenderDetail(ctx, instrumentId) {
  const { el } = ctx;
  let detail;
  try {
    detail = await api.getInstrument(instrumentId);
  } catch (e) {
    ctx.showToast('종목 정보를 불러오지 못했습니다: ' + e.message);
    ctx.navigateToList();
    return;
  }
  ctx.setOfflineState(!!detail.__offline, detail.__syncedAt);
  ctx.state.currentInstrument = detail;
  ctx.state.showAllTx = false;
  ctx.state.showDrawdownTable = false;

  el.inputCurrentPrice.value = detail.quote ? detail.quote.price : '';
  el.inputAtr.value = detail.atr ? detail.atr.atr : '';
  el.inputPostHigh.value = detail.instrument.post_entry_high_price != null ? detail.instrument.post_entry_high_price : '';
  el.inputAutoUpdateHigh.checked = detail.instrument.auto_update_high;
  el.settingsDetails.open = false;
  el.headerTitle.textContent = detail.instrument.name;

  renderDetail(ctx);
}

export function renderDetail(ctx) {
  const { el, state } = ctx;
  const detail = state.currentInstrument;
  if (!detail) return;
  const { instrument, position, signal } = detail;

  el.dAvgPrice.textContent = formatPrice(position.avg_price);
  el.dQty.textContent = formatQty(position.quantity);
  el.dCostBasis.textContent = formatMoney(position.cost_basis);

  const currentPrice = num(el.inputCurrentPrice.value);

  if (currentPrice != null && position.quantity > 0) {
    const evalValue = currentPrice * position.quantity;
    const pnl = evalValue - position.cost_basis;
    const pct = position.avg_price > 0 ? (currentPrice - position.avg_price) / position.avg_price * 100 : null;
    el.dPnl.textContent = `${formatMoney(pnl)} (${formatPercent(pct)})`;
    el.dPnl.className = 'summary-value mono ' + pnlClass(pnl);
  } else {
    el.dPnl.textContent = '현재가 미입력';
    el.dPnl.className = 'summary-value mono';
  }

  el.dNextBuy.textContent = signal && signal.next_buy_price != null ? formatPrice(signal.next_buy_price) : '-';
  el.dTakeProfit.textContent = signal && signal.take_profit_price != null ? formatPrice(signal.take_profit_price) : '-';
  el.dTrailingStop.textContent = instrument.trailing_stop_price != null ? formatPrice(instrument.trailing_stop_price) : '-';

  const signalInfo = signal ? SIGNAL_TEXT[signal.status] : null;
  const isTrigger = !!signal && TRIGGER_STATUSES.has(signal.status);
  const isAcknowledged = isTrigger && signal.latest_event_id != null
    && signal.acknowledged_event_id === signal.latest_event_id;

  if (signalInfo && !isAcknowledged) {
    el.signalBanner.hidden = false;
    el.signalBanner.className = 'signal-banner ' + signalInfo.cls;
    el.signalText.textContent = signalInfo.text;
    el.btnSignalAcknowledge.hidden = !isTrigger; // 관망 신호는 확인할 게 없음
    el.signalAcknowledgedNote.hidden = true;
  } else {
    el.signalBanner.hidden = true;
    el.signalAcknowledgedNote.hidden = !isAcknowledged;
  }

  renderDrawdown(ctx, instrument, currentPrice);
  renderPartialSell(ctx, position, currentPrice);
  renderTxHistory(ctx, detail.trades);
  renderSettingsFields(ctx, instrument);

  el.btnOpenSell.disabled = position.quantity <= 0;
}

function renderDrawdown(ctx, instrument, currentPrice) {
  const { el, state } = ctx;
  const postHigh = instrument.post_entry_high_price;
  if (postHigh == null) {
    el.drawdownCurrentLine.textContent = '보유 후 최고가 정보가 없습니다.';
  } else if (currentPrice == null) {
    el.drawdownCurrentLine.textContent = `보유 후 최고가 ${formatPrice(postHigh)} — 현재가를 입력하면 하락 구간을 표시합니다.`;
  } else {
    const dropPct = (postHigh - currentPrice) / postHigh * 100;
    el.drawdownCurrentLine.textContent = `보유 후 최고가 대비 ${formatPercent(-dropPct)} (${computeDrawdownBracketLabel(dropPct)})`;
  }

  const levels = computeDrawdownLevels(postHigh);
  el.drawdownBody.innerHTML = '';
  levels.forEach((lvl) => {
    const tr = document.createElement('tr');
    const tdPct = document.createElement('td');
    tdPct.textContent = `-${lvl.pct}%`;
    const tdPrice = document.createElement('td');
    tdPrice.textContent = formatPrice(lvl.price);
    tr.appendChild(tdPct);
    tr.appendChild(tdPrice);
    el.drawdownBody.appendChild(tr);
  });

  el.btnToggleDrawdown.textContent = state.showDrawdownTable ? '하락 구간 접기 ▴' : '하락 구간 더보기 ▾';
  el.drawdownTableWrap.hidden = !state.showDrawdownTable;
}

function renderPartialSell(ctx, position, currentPrice) {
  const { el } = ctx;
  el.partialSellBody.innerHTML = '';
  PARTIAL_SELL_PRESETS.forEach((pct) => {
    const { qty, realizedPnl } = computePartialSell(position, currentPrice, pct);
    const tr = document.createElement('tr');
    const tdPct = document.createElement('td');
    tdPct.textContent = `${pct}%`;
    const tdQty = document.createElement('td');
    tdQty.textContent = formatQty(qty);
    const tdPnl = document.createElement('td');
    if (realizedPnl == null) {
      tdPnl.textContent = '현재가 미입력';
    } else {
      tdPnl.textContent = (realizedPnl >= 0 ? '+' : '') + formatMoney(realizedPnl);
      tdPnl.className = pnlClass(realizedPnl);
    }
    tr.appendChild(tdPct);
    tr.appendChild(tdQty);
    tr.appendChild(tdPnl);
    el.partialSellBody.appendChild(tr);
  });
  updateCustomPctResult(ctx, position, currentPrice);
}

export function updateCustomPctResult(ctx, position, currentPrice) {
  const { el } = ctx;
  const pct = num(el.inputCustomPct.value);
  if (pct == null || pct < 0 || pct > 100) {
    el.customPctResult.textContent = '';
    return;
  }
  const { qty, realizedPnl } = computePartialSell(position, currentPrice, pct);
  if (realizedPnl == null) {
    el.customPctResult.textContent = `${pct}% 매도 시 수량 ${formatQty(qty)} — 현재가 미입력`;
  } else {
    el.customPctResult.innerHTML =
      `${pct}% 매도 시 수량 ${formatQty(qty)} · 실현손익 <span class="${pnlClass(realizedPnl)}">${(realizedPnl >= 0 ? '+' : '')}${formatMoney(realizedPnl)}</span>`;
  }
}

function realizedPnlForTrade(trades, index) {
  // 매도 거래의 실현손익 = (매도가 - 그 시점 평균단가) * 매도수량.
  // 서버가 position_state 최종값만 주므로, 화면 표시용으로 거래 목록을
  // 앞에서부터 재생해 매 시점 평균단가를 로컬에서 다시 계산한다(이동평균 원가법).
  let runQty = 0;
  let runCost = 0;
  for (let i = 0; i <= index; i++) {
    const t = trades[i];
    if (t.trade_type === 'buy') {
      runQty += t.quantity;
      runCost += t.quantity * t.price;
    } else {
      const avgAtSale = runQty > 0 ? runCost / runQty : 0;
      if (i === index) return (t.price - avgAtSale) * t.quantity;
      runCost -= avgAtSale * t.quantity;
      runQty -= t.quantity;
    }
  }
  return null;
}

function renderTxHistory(ctx, trades) {
  const { el, state } = ctx;
  const withPnl = trades.map((t, i) => ({ ...t, realizedPnl: t.trade_type === 'sell' ? realizedPnlForTrade(trades, i) : null }));
  const sortedDesc = [...withPnl].sort((a, b) => (a.executed_at < b.executed_at ? 1 : a.executed_at > b.executed_at ? -1 : 0));
  const list = state.showAllTx ? sortedDesc : sortedDesc.slice(0, 5);

  el.txBody.innerHTML = '';
  el.txEmpty.hidden = trades.length > 0;

  list.forEach((rec) => {
    const tr = document.createElement('tr');
    tr.dataset.txId = rec.id;
    tr.setAttribute('data-clickable', '1');

    const tdType = document.createElement('td');
    const span = document.createElement('span');
    span.className = rec.trade_type === 'buy' ? 'badge-buy' : 'badge-sell';
    span.textContent = rec.trade_type === 'buy' ? '매수' : '매도';
    tdType.appendChild(span);
    tr.appendChild(tdType);

    const tdDate = document.createElement('td');
    tdDate.textContent = rec.executed_at.replace('T', ' ').slice(0, 16);
    tr.appendChild(tdDate);

    const tdPrice = document.createElement('td');
    tdPrice.textContent = formatPrice(rec.price);
    tr.appendChild(tdPrice);

    const tdQty = document.createElement('td');
    tdQty.textContent = formatQty(rec.quantity);
    tr.appendChild(tdQty);

    const tdPnl = document.createElement('td');
    if (rec.realizedPnl == null) {
      tdPnl.textContent = '-';
    } else {
      tdPnl.textContent = (rec.realizedPnl >= 0 ? '+' : '') + formatMoney(rec.realizedPnl);
      tdPnl.className = pnlClass(rec.realizedPnl);
    }
    tr.appendChild(tdPnl);

    const tdEdit = document.createElement('td');
    tdEdit.className = 'tx-edit-hint';
    tdEdit.textContent = '✎';
    tr.appendChild(tdEdit);

    el.txBody.appendChild(tr);
  });

  el.btnToggleTxAll.hidden = trades.length <= 5;
  el.btnToggleTxAll.textContent = state.showAllTx ? '최근 5건만 보기 ▴' : '전체 보기 ▾';
}

function renderSettingsFields(ctx, instrument) {
  const { el } = ctx;
  el.settingsName.value = instrument.name;
  el.settingsCurrency.value = instrument.currency;
  el.settingsBuyMult.value = instrument.buy_multiple;
  el.settingsSellMult.value = instrument.sell_multiple;
  el.settingsStopMult.value = instrument.stop_multiple;
  el.settingsTrancheAmount.value = instrument.tranche_amount != null ? instrument.tranche_amount : '';
  el.settingsKisCode.value = instrument.kis_code || '';
  el.btnRefreshQuote.disabled = !instrument.kis_code;
  el.refreshQuoteHint.textContent = instrument.kis_code
    ? ''
    : '종목 설정에서 KIS 종목코드를 먼저 입력하세요.';
}

// ---------- 커밋 핸들러 (현재가/ATR/최고가 입력 -> 서버 저장 -> 재조회) ----------

export async function refreshQuoteNow(ctx) {
  const { el, state } = ctx;
  if (!state.currentInstrument) return;
  const instrumentId = state.currentInstrument.instrument.id;
  el.btnRefreshQuote.disabled = true;
  el.btnRefreshQuote.textContent = '가져오는 중...';
  try {
    await api.refreshQuote(instrumentId);
  } catch (e) {
    ctx.showToast('시세 가져오기 실패: ' + e.message);
    return;
  } finally {
    el.btnRefreshQuote.textContent = '지금 시세 가져오기';
  }
  await loadAndRenderDetail(ctx, instrumentId);
  ctx.showToast('시세를 가져왔습니다.');
}

export async function commitCurrentPrice(ctx) {
  const { el, state } = ctx;
  const price = num(el.inputCurrentPrice.value);
  if (price == null) return;
  try {
    await api.commitQuote(state.currentInstrument.instrument.id, price);
  } catch (e) {
    ctx.showToast('현재가 저장 실패: ' + e.message);
    return;
  }
  await loadAndRenderDetail(ctx, state.currentInstrument.instrument.id);
}

export async function commitAtr(ctx) {
  const { el, state } = ctx;
  const atr = num(el.inputAtr.value);
  if (atr == null) return;
  try {
    await api.commitAtr(state.currentInstrument.instrument.id, atr);
  } catch (e) {
    ctx.showToast('ATR 저장 실패: ' + e.message);
    return;
  }
  await loadAndRenderDetail(ctx, state.currentInstrument.instrument.id);
}

export async function commitPostHigh(ctx) {
  const { el, state } = ctx;
  const val = num(el.inputPostHigh.value);
  try {
    await api.updateInstrumentManual(state.currentInstrument.instrument.id, { post_entry_high_price: val });
  } catch (e) {
    ctx.showToast('최고가 저장 실패: ' + e.message);
    return;
  }
  await loadAndRenderDetail(ctx, state.currentInstrument.instrument.id);
}

export async function commitAutoUpdateHigh(ctx) {
  const { el, state } = ctx;
  try {
    await api.updateInstrumentManual(state.currentInstrument.instrument.id, { auto_update_high: el.inputAutoUpdateHigh.checked });
  } catch (e) {
    ctx.showToast('설정 저장 실패: ' + e.message);
  }
}

export async function acknowledgeSignal(ctx) {
  const { state } = ctx;
  if (!state.currentInstrument) return;
  try {
    await api.acknowledgeSignal(state.currentInstrument.instrument.id);
  } catch (e) {
    ctx.showToast('확인 처리 실패: ' + e.message);
    return;
  }
  await loadAndRenderDetail(ctx, state.currentInstrument.instrument.id);
  ctx.showToast('확인했습니다.');
}

export async function handleSaveSettings(ctx) {
  const { el, state } = ctx;
  const name = el.settingsName.value.trim();
  if (!name) { ctx.showToast('종목명을 입력하세요.'); return; }
  const body = {
    name,
    currency: el.settingsCurrency.value.trim() || 'KRW',
    buy_multiple: num(el.settingsBuyMult.value),
    sell_multiple: num(el.settingsSellMult.value),
    stop_multiple: num(el.settingsStopMult.value),
    tranche_amount: num(el.settingsTrancheAmount.value),
    kis_code: el.settingsKisCode.value.trim() || null,
  };
  try {
    await api.updateInstrumentSettings(state.currentInstrument.instrument.id, body);
  } catch (e) {
    ctx.showToast('설정 저장 실패: ' + e.message);
    return;
  }
  await loadAndRenderDetail(ctx, state.currentInstrument.instrument.id);
  ctx.showToast('설정이 저장되었습니다.');
}

export function handleResetStock(ctx) {
  const { state } = ctx;
  const instrument = state.currentInstrument.instrument;
  ctx.askConfirm(`'${instrument.name}' 종목의 거래 이력을 전부 삭제하고 초기화합니다. 이 작업은 되돌릴 수 없습니다.`, async () => {
    try {
      await api.resetInstrument(instrument.id);
    } catch (e) {
      ctx.showToast('초기화 실패: ' + e.message);
      return;
    }
    await loadAndRenderDetail(ctx, instrument.id);
    ctx.showToast('종목이 초기화되었습니다.');
  });
}

export function handleDeleteStock(ctx) {
  const { state } = ctx;
  const instrument = state.currentInstrument.instrument;
  ctx.askConfirm(`'${instrument.name}' 종목을 삭제합니다. 이 작업은 되돌릴 수 없습니다.`, async () => {
    try {
      await api.deleteInstrument(instrument.id);
    } catch (e) {
      ctx.showToast('삭제 실패: ' + e.message);
      return;
    }
    ctx.navigateBack();
    ctx.showToast('종목이 삭제되었습니다.');
  });
}

export function handleQuickDeleteStock(ctx, instrumentId, instrumentName) {
  ctx.askConfirm(`'${instrumentName}' 종목을 삭제합니다. 이 작업은 되돌릴 수 없습니다.`, async () => {
    try {
      await api.deleteInstrument(instrumentId);
    } catch (e) {
      ctx.showToast('삭제 실패: ' + e.message);
      return;
    }
    ctx.showToast('종목이 삭제되었습니다.');
    ctx.refreshCurrentView();
  });
}
