// 매수/매도 체결 등록·수정·삭제 모달 (스펙 13.4 -- "매수 체결 등록"/"매도 체결 등록").
import { api, ApiError } from './api-client.js';
import { nowDatetimeLocalStr, num } from './formatters.js';
import { loadAndRenderDetail } from './instrument-detail.js';

export function openTxModal(ctx, mode, existingTrade) {
  const { el, state } = ctx;
  state.txModalMode = mode;
  state.editingTradeId = existingTrade ? existingTrade.id : null;
  const label = mode === 'buy' ? '매수' : '매도';
  el.modalTxTitle.textContent = existingTrade ? `${label} 체결 수정` : `${label} 체결 등록`;
  el.txExecutedAt.value = existingTrade ? existingTrade.executed_at.slice(0, 16) : nowDatetimeLocalStr();
  el.txPrice.value = existingTrade ? existingTrade.price : '';
  el.txQty.value = existingTrade ? existingTrade.quantity : '';
  el.txFee.value = existingTrade && existingTrade.fee != null ? existingTrade.fee : '';
  el.txTax.value = existingTrade && existingTrade.tax != null ? existingTrade.tax : '';
  el.txMemo.value = existingTrade && existingTrade.memo ? existingTrade.memo : '';
  el.btnTxDelete.hidden = !existingTrade;
  el.modalTx.hidden = false;
}

export function closeTxModal(ctx) {
  const { el, state } = ctx;
  el.modalTx.hidden = true;
  state.txModalMode = null;
  state.editingTradeId = null;
}

export async function handleTxSave(ctx) {
  const { el, state } = ctx;
  const instrumentId = state.currentInstrument.instrument.id;
  const price = num(el.txPrice.value);
  const quantity = num(el.txQty.value);
  const executedAt = el.txExecutedAt.value;

  if (price == null || price <= 0) { ctx.showToast('단가를 올바르게 입력하세요.'); return; }
  if (quantity == null || quantity <= 0) { ctx.showToast('수량을 올바르게 입력하세요.'); return; }
  if (!executedAt) { ctx.showToast('체결일시를 입력하세요.'); return; }

  const body = {
    price, quantity, executed_at: executedAt,
    fee: num(el.txFee.value), tax: num(el.txTax.value),
    memo: el.txMemo.value.trim() || null,
  };

  try {
    if (state.editingTradeId) {
      await api.updateTrade(instrumentId, state.editingTradeId, body);
    } else {
      await api.createTrade(instrumentId, { trade_type: state.txModalMode, ...body });
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      ctx.showToast(`보유수량을 초과하는 매도입니다 (최대 ${e.detail.available_quantity}).`);
    } else {
      ctx.showToast('저장 실패: ' + e.message);
    }
    return;
  }

  closeTxModal(ctx);
  await loadAndRenderDetail(ctx, instrumentId);
  ctx.showToast('거래가 저장되었습니다.');
}

export function handleTxDelete(ctx) {
  const { state } = ctx;
  const instrumentId = state.currentInstrument.instrument.id;
  const tradeId = state.editingTradeId;
  if (!tradeId) return;
  ctx.askConfirm('이 거래 기록을 삭제할까요? 이 작업은 되돌릴 수 없습니다.', async () => {
    try {
      await api.deleteTrade(instrumentId, tradeId);
    } catch (e) {
      ctx.showToast('삭제 실패: ' + e.message);
      return;
    }
    closeTxModal(ctx);
    await loadAndRenderDetail(ctx, instrumentId);
    ctx.showToast('거래 기록이 삭제되었습니다.');
  });
}

export async function handleAddStockSave(ctx) {
  const { el } = ctx;
  const name = el.addStockName.value.trim();
  if (!name) { ctx.showToast('종목명을 입력하세요.'); return; }
  const currency = el.addStockCurrency.value.trim() || 'KRW';
  const kisCode = el.addStockKisCode.value.trim() || null;
  let instrument;
  try {
    instrument = await api.createInstrument({ name, currency, kis_code: kisCode });
  } catch (e) {
    ctx.showToast('종목 추가 실패: ' + e.message);
    return;
  }
  el.modalAddStock.hidden = true;
  ctx.navigateToDetail(instrument.id);
}
