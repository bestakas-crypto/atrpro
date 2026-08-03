// 앱 진입점 -- DOM 캐싱, 라우팅(해시 기반), 전역 모달/토스트, 이벤트 바인딩.
// 스펙 4.9 모듈 분리안의 나머지 파일들(dashboard/instrument-detail/trade-form/
// deposits/legacy-export)을 여기서 하나로 묶는다.
import { api } from './api-client.js';
import { latestSyncedAt } from './offline-snapshot.js';
import { loadAndRenderList, autoFetchFxRates, refreshAllQuotes } from './dashboard.js';
import {
  loadAndRenderDetail, renderDetail, updateCustomPctResult, commitCurrentPrice, commitAtr,
  commitPostHigh, commitAutoUpdateHigh, handleSaveSettings, handleResetStock, handleDeleteStock,
  handleQuickDeleteStock, acknowledgeSignal, refreshQuoteNow,
} from './instrument-detail.js';
import { openTxModal, closeTxModal, handleTxSave, handleTxDelete, handleAddStockSave } from './trade-form.js';
import { openAddDepositModal, openEditDepositModal, closeDepositModal, handleDepositSave, handleDepositDelete } from './deposits.js';
import { openImportModal, closeImportModal, handleFileSelected, handleImportConfirm } from './legacy-export.js';
import { num } from './formatters.js';

const el = {};
function cacheDom() {
  const ids = [
    'btn-nav-back', 'btn-nav-forward', 'btn-nav-refresh', 'header-title',
    'view-list', 'btn-add-stock', 'btn-add-deposit', 'btn-open-import',
    'btn-refresh-all-quotes', 'refresh-all-quotes-hint',
    'btn-toggle-total', 'total-cost-sum', 'total-eval-sum', 'total-deposit-sum', 'total-asset-sum',
    'total-pnl-sum', 'total-chevron', 'total-summary-detail', 'total-detail-rows', 'total-deposit-detail-rows',
    'total-missing-note', 'total-eval-missing-note', 'total-deposit-missing-note',
    'currency-switch', 'fx-status', 'fx-rate-rows', 'btn-fx-refresh', 'btn-fx-save',
    'stock-list', 'stock-list-empty', 'deposit-list', 'deposit-list-empty',
    'view-detail', 'd-avg-price', 'd-qty', 'd-cost-basis', 'd-pnl',
    'signal-banner', 'signal-text', 'btn-signal-acknowledge', 'signal-acknowledged-note',
    'input-current-price', 'input-atr', 'input-post-high', 'input-auto-update-high',
    'btn-refresh-quote', 'refresh-quote-hint',
    'd-next-buy', 'd-take-profit', 'd-trailing-stop',
    'drawdown-current-line', 'btn-toggle-drawdown', 'drawdown-table-wrap', 'drawdown-body',
    'partial-sell-body', 'input-custom-pct', 'custom-pct-result',
    'tx-body', 'tx-empty', 'btn-toggle-tx-all',
    'settings-details', 'settings-name', 'settings-currency', 'settings-buy-mult', 'settings-sell-mult',
    'settings-stop-mult', 'settings-tranche-amount', 'settings-kis-code', 'settings-kis-market',
    'btn-save-settings', 'btn-reset-stock', 'btn-delete-stock',
    'sticky-actions', 'btn-open-buy', 'btn-open-sell',
    'modal-tx', 'modal-tx-title', 'tx-executed-at', 'tx-price', 'tx-qty', 'tx-fee', 'tx-tax', 'tx-memo',
    'btn-tx-delete', 'btn-tx-cancel', 'btn-tx-save',
    'modal-add-stock', 'add-stock-name', 'add-stock-kis-code', 'add-stock-kis-market', 'add-stock-currency',
    'btn-add-stock-cancel', 'btn-add-stock-save',
    'modal-deposit', 'modal-deposit-title', 'deposit-account-name', 'deposit-amount', 'deposit-currency',
    'btn-deposit-delete', 'btn-deposit-cancel', 'btn-deposit-save',
    'modal-confirm', 'confirm-message', 'confirm-cancel', 'confirm-ok',
    'modal-import', 'import-file-input', 'import-report', 'btn-import-cancel', 'btn-import-confirm',
    'offline-banner', 'offline-banner-synced-at',
    'toast',
  ];
  ids.forEach((id) => {
    const camel = id.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    el[camel] = document.getElementById(id);
  });
}

const state = {
  currentInstrumentId: null,
  currentInstrument: null,
  showAllTx: false,
  showDrawdownTable: false,
  showTotalDetail: false,
  txModalMode: null,
  editingTradeId: null,
  editingDepositId: null,
  confirmCallback: null,
  // TOTAL: 전체 통화를 원화로 환산한 합계(서버 계산). KRW/USD: 그 통화로만
  // 보유한 종목/예금만 걸러서 환산 없이 보여줌(클라이언트에서 계산) --
  // 2026-08-02 사용자 요청으로 3버튼(TOTAL/KRW/USD) 체계로 변경, JPY는
  // 별도 버튼 없이 TOTAL에서만 환산 포함.
  currencyMode: 'TOTAL',
};

let toastTimer = null;
function showToast(msg) {
  el.toast.textContent = msg;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, 2400);
}

function askConfirm(message, onConfirm) {
  el.confirmMessage.textContent = message;
  state.confirmCallback = onConfirm;
  el.modalConfirm.hidden = false;
}

function closeConfirm() {
  el.modalConfirm.hidden = true;
  state.confirmCallback = null;
}

function setOfflineState(offline, syncedAt) {
  el.offlineBanner.hidden = !offline;
  if (offline) {
    const stamp = syncedAt || latestSyncedAt();
    el.offlineBannerSyncedAt.textContent = stamp ? `마지막 동기화: ${stamp.replace('T', ' ').slice(0, 16)}` : '';
  }
}

function refreshCurrentView() {
  if (state.currentInstrumentId) {
    return loadAndRenderDetail(ctx, state.currentInstrumentId);
  }
  return loadAndRenderList(ctx);
}

// ---------- 라우팅 ----------
function renderListView() {
  state.currentInstrumentId = null;
  el.viewList.hidden = false;
  el.viewDetail.hidden = true;
  el.stickyActions.hidden = true;
  el.headerTitle.textContent = 'ATRsite-pro';
  loadAndRenderList(ctx);
}

function renderDetailView(instrumentId) {
  state.currentInstrumentId = instrumentId;
  el.viewList.hidden = true;
  el.viewDetail.hidden = false;
  el.stickyActions.hidden = false;
  loadAndRenderDetail(ctx, instrumentId);
}

function navigateToList() {
  history.pushState({ view: 'list' }, '', '#list');
  renderListView();
}

function navigateToDetail(instrumentId) {
  history.pushState({ view: 'detail', id: instrumentId }, '', '#detail/' + instrumentId);
  renderDetailView(instrumentId);
}

function navigateBack() {
  history.back();
}

const ctx = {
  el, state, showToast, askConfirm, setOfflineState, refreshCurrentView,
  navigateToList, navigateToDetail, navigateBack,
};

// ---------- 이벤트 바인딩 ----------
function bindEvents() {
  el.btnNavBack.addEventListener('click', () => history.back());
  el.btnNavForward.addEventListener('click', () => history.forward());
  // 종목목록/종목상세는 현재 뷰에 맞게 다시 조회(스펙: 전체 새로고침이 아니라
  // 화면별 재조회). refreshCurrentView()가 이미 state.currentInstrumentId로
  // 목록/상세를 구분해서 다시 그려준다(2026-08-03, 이전엔 미연결 상태였음).
  el.btnNavRefresh.addEventListener('click', () => refreshCurrentView());

  el.btnAddStock.addEventListener('click', () => {
    el.addStockName.value = '';
    el.addStockKisCode.value = '';
    el.addStockKisMarket.value = 'KRX';
    el.addStockCurrency.value = 'KRW';
    el.modalAddStock.hidden = false;
  });
  el.btnAddStockCancel.addEventListener('click', () => { el.modalAddStock.hidden = true; });
  el.btnAddStockSave.addEventListener('click', () => handleAddStockSave(ctx));

  el.btnAddDeposit.addEventListener('click', () => openAddDepositModal(ctx));
  el.btnDepositCancel.addEventListener('click', () => closeDepositModal(ctx));
  el.btnDepositSave.addEventListener('click', () => handleDepositSave(ctx));
  el.btnDepositDelete.addEventListener('click', () => handleDepositDelete(ctx));
  el.modalDeposit.addEventListener('click', (e) => { if (e.target === el.modalDeposit) closeDepositModal(ctx); });

  el.depositList.addEventListener('click', (e) => {
    const menuBtn = e.target.closest('.deposit-card-menu');
    if (!menuBtn) return;
    api.listDeposits().then((deposits) => {
      const dep = deposits.find((d) => d.id === menuBtn.dataset.id);
      if (dep) openEditDepositModal(ctx, dep);
    });
  });

  el.btnOpenImport.addEventListener('click', () => openImportModal(ctx));
  el.btnRefreshAllQuotes.addEventListener('click', () => refreshAllQuotes(ctx));
  el.btnImportCancel.addEventListener('click', () => closeImportModal(ctx));
  el.importFileInput.addEventListener('change', () => handleFileSelected(ctx));
  el.btnImportConfirm.addEventListener('click', () => handleImportConfirm(ctx));
  el.modalImport.addEventListener('click', (e) => { if (e.target === el.modalImport) closeImportModal(ctx); });

  el.btnToggleTotal.addEventListener('click', () => {
    state.showTotalDetail = !state.showTotalDetail;
    loadAndRenderList(ctx);
  });

  el.currencySwitch.addEventListener('click', async (e) => {
    const btn = e.target.closest('.currency-pill');
    if (!btn) return;
    state.currencyMode = btn.dataset.currency;
    if (state.currencyMode === 'TOTAL') {
      // TOTAL은 항상 원화 환산 합계 -- 서버가 KRW 기준으로 계산하게 한다.
      await api.putFx({ display_currency: 'KRW' });
    }
    // KRW/USD는 환산 없이 그 통화 종목만 걸러 보여주는 클라이언트 계산이라
    // 서버 호출 없이 다시 그리기만 하면 된다.
    loadAndRenderList(ctx);
  });

  el.btnFxRefresh.addEventListener('click', async () => {
    el.fxStatus.textContent = '환율을 조회하는 중...';
    const ok = await autoFetchFxRates(ctx);
    loadAndRenderList(ctx);
    showToast(ok ? '환율을 새로 조회했습니다.' : '환율 자동 조회에 실패했습니다. 직접 입력해주세요.');
  });

  el.btnFxSave.addEventListener('click', async () => {
    const inputs = el.fxRateRows.querySelectorAll('.fx-rate-input');
    const rates = {};
    inputs.forEach((input) => {
      const val = num(input.value);
      if (val != null && val > 0) rates[input.dataset.currency] = val;
    });
    await api.putFx({ rates });
    loadAndRenderList(ctx);
    showToast('환율이 저장되었습니다.');
  });

  el.stockList.addEventListener('click', (e) => {
    const menuBtn = e.target.closest('.stock-card-menu');
    if (menuBtn) {
      e.stopPropagation();
      const card = menuBtn.closest('.stock-card');
      const name = card ? card.querySelector('.stock-card-name').textContent : '';
      handleQuickDeleteStock(ctx, menuBtn.dataset.id, name);
      return;
    }
    const card = e.target.closest('.stock-card');
    if (card) navigateToDetail(card.dataset.id);
  });

  el.inputCurrentPrice.addEventListener('input', () => renderDetail(ctx));
  el.inputCurrentPrice.addEventListener('change', () => commitCurrentPrice(ctx));
  el.btnRefreshQuote.addEventListener('click', () => refreshQuoteNow(ctx));
  el.inputAtr.addEventListener('input', () => renderDetail(ctx));
  el.inputAtr.addEventListener('change', () => commitAtr(ctx));
  el.inputPostHigh.addEventListener('change', () => commitPostHigh(ctx));
  el.btnSignalAcknowledge.addEventListener('click', () => acknowledgeSignal(ctx));
  el.inputAutoUpdateHigh.addEventListener('change', () => commitAutoUpdateHigh(ctx));

  el.btnToggleDrawdown.addEventListener('click', () => {
    state.showDrawdownTable = !state.showDrawdownTable;
    renderDetail(ctx);
  });

  el.inputCustomPct.addEventListener('input', () => {
    if (!state.currentInstrument) return;
    const currentPrice = num(el.inputCurrentPrice.value);
    updateCustomPctResult(ctx, state.currentInstrument.position, currentPrice);
  });

  el.btnToggleTxAll.addEventListener('click', () => {
    state.showAllTx = !state.showAllTx;
    renderDetail(ctx);
  });

  el.txBody.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-tx-id]');
    if (!row || !state.currentInstrument) return;
    const trade = state.currentInstrument.trades.find((t) => t.id === row.dataset.txId);
    if (trade) openTxModal(ctx, trade.trade_type, trade);
  });

  el.btnOpenBuy.addEventListener('click', () => openTxModal(ctx, 'buy'));
  el.btnOpenSell.addEventListener('click', () => {
    if (el.btnOpenSell.disabled) return;
    openTxModal(ctx, 'sell');
  });
  el.btnTxCancel.addEventListener('click', () => closeTxModal(ctx));
  el.btnTxSave.addEventListener('click', () => handleTxSave(ctx));
  el.btnTxDelete.addEventListener('click', () => handleTxDelete(ctx));

  el.btnSaveSettings.addEventListener('click', () => handleSaveSettings(ctx));
  el.btnResetStock.addEventListener('click', () => handleResetStock(ctx));
  el.btnDeleteStock.addEventListener('click', () => handleDeleteStock(ctx));

  el.confirmOk.addEventListener('click', () => {
    const cb = state.confirmCallback;
    closeConfirm();
    if (cb) cb();
  });
  el.confirmCancel.addEventListener('click', closeConfirm);
  el.modalConfirm.addEventListener('click', (e) => { if (e.target === el.modalConfirm) closeConfirm(); });
  el.modalTx.addEventListener('click', (e) => { if (e.target === el.modalTx) closeTxModal(ctx); });
  el.modalAddStock.addEventListener('click', (e) => { if (e.target === el.modalAddStock) el.modalAddStock.hidden = true; });
}

function init() {
  cacheDom();
  bindEvents();

  window.addEventListener('popstate', (e) => {
    const s = e.state;
    if (s && s.view === 'detail') renderDetailView(s.id);
    else renderListView();
  });
  history.replaceState({ view: 'list' }, '', '#list');
  renderListView();

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('service-worker.js').then((reg) => {
        reg.update().catch(() => {});
      }).catch(() => {});
    });
    // 배경에서 새 서비스워커가 활성화되면(reg.update()가 주기적으로 검사함)
    // 지금 당장 리로드하면 사용자가 입력 중인 모달(예: 종목 추가창에 통화를
    // 타이핑하던 중)이 통째로 사라진다 -- 2026-08-02 실사용 중 재현된 버그.
    // 열려 있는 모달이 없을 때만 즉시 반영하고, 모달이 열려 있으면 닫힐 때까지
    // 미뤘다가 반영한다.
    let swRefreshed = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (swRefreshed) return;
      swRefreshed = true;
      const reloadWhenIdle = () => {
        if (document.querySelector('.modal-overlay:not([hidden])')) {
          setTimeout(reloadWhenIdle, 1000);
          return;
        }
        location.reload();
      };
      reloadWhenIdle();
    });
  }
}

document.addEventListener('DOMContentLoaded', init);
