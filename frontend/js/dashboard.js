// 화면 1: 종목 목록 + 예금 목록 + 총계. 옛 app.js의 renderList()/renderDepositList()/
// renderFxSettings()에 대응하지만, 총계·평가금액은 서버(/api/v1/dashboard)가
// 계산해서 내려준다 -- 계산 권한이 서버로 이동한다는 스펙 1절 방침 반영.
import { api } from './api-client.js';
import { formatMoney, formatPercent, formatPrice, formatQty, pnlClass } from './formatters.js';

function cardItem(label, value) {
  const wrap = document.createElement('div');
  wrap.className = 'stock-card-item';
  const l = document.createElement('span');
  l.className = 'stock-card-label';
  l.textContent = label;
  const v = document.createElement('span');
  v.className = 'stock-card-value mono';
  v.textContent = value;
  wrap.appendChild(l);
  wrap.appendChild(v);
  return wrap;
}

function signalEmoji(status) {
  switch (status) {
    case 'SELL_BOTH_TRIGGERED':
    case 'STOP_TRIGGERED':
      return '🔴';
    case 'TAKE_PROFIT_TRIGGERED':
      return '🟠';
    case 'BUY_TRIGGERED':
      return '🟢';
    case 'NEUTRAL':
      return '🟡';
    default:
      return '⚪';
  }
}

export async function loadAndRenderList(ctx) {
  const { el } = ctx;
  let dashboard;
  try {
    dashboard = await api.getDashboard();
  } catch (e) {
    ctx.showToast('대시보드를 불러오지 못했습니다: ' + e.message);
    return;
  }
  ctx.setOfflineState(!!dashboard.__offline, dashboard.__syncedAt);
  renderList(ctx, dashboard);
}

function renderList(ctx, dashboard) {
  const { el } = ctx;
  const rows = dashboard.instruments;

  el.stockList.innerHTML = '';
  el.stockListEmpty.hidden = rows.length > 0;

  rows.forEach((row) => {
    const { instrument, position, signal, cost_basis: cost, eval_value: evalValue } = row;
    const card = document.createElement('div');
    card.className = 'stock-card';
    card.dataset.id = instrument.id;

    const top = document.createElement('div');
    top.className = 'stock-card-top';

    const signalSpan = document.createElement('span');
    signalSpan.className = 'stock-card-signal';
    signalSpan.textContent = signalEmoji(signal ? signal.status : null);
    top.appendChild(signalSpan);

    const nameSpan = document.createElement('span');
    nameSpan.className = 'stock-card-name';
    nameSpan.textContent = instrument.name;
    top.appendChild(nameSpan);

    const returnSpan = document.createElement('span');
    returnSpan.className = 'stock-card-return mono';
    if (evalValue != null && position.avg_price > 0) {
      const pct = (evalValue / position.quantity - position.avg_price) / position.avg_price * 100;
      returnSpan.textContent = formatPercent(pct);
      returnSpan.classList.add(pnlClass(pct));
    } else {
      returnSpan.textContent = '현재가 미입력';
      returnSpan.style.color = 'var(--text-faint)';
      returnSpan.style.fontSize = '12px';
    }
    top.appendChild(returnSpan);

    const menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'stock-card-menu';
    menuBtn.dataset.id = instrument.id;
    menuBtn.setAttribute('aria-label', '종목 삭제');
    menuBtn.textContent = '⋮';
    top.appendChild(menuBtn);

    card.appendChild(top);

    const grid = document.createElement('div');
    grid.className = 'stock-card-grid';
    grid.appendChild(cardItem('평균단가', formatPrice(position.avg_price)));
    grid.appendChild(cardItem('보유수량', formatQty(position.quantity)));
    grid.appendChild(cardItem('매입금액', formatMoney(cost)));
    card.appendChild(grid);

    el.stockList.appendChild(card);
  });

  renderDepositList(ctx, dashboard.deposits);
  renderTotals(ctx, dashboard);
}

function renderDepositList(ctx, deposits) {
  const { el } = ctx;
  el.depositList.innerHTML = '';
  el.depositListEmpty.hidden = deposits.length > 0;

  deposits.forEach((d) => {
    const card = document.createElement('div');
    card.className = 'deposit-card';
    card.dataset.id = d.id;

    const top = document.createElement('div');
    top.className = 'deposit-card-top';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'deposit-card-name';
    nameSpan.textContent = d.account_name;
    top.appendChild(nameSpan);

    const badge = document.createElement('span');
    badge.className = 'currency-badge';
    badge.textContent = d.currency;
    top.appendChild(badge);

    const menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'deposit-card-menu';
    menuBtn.dataset.id = d.id;
    menuBtn.setAttribute('aria-label', '예금 수정');
    menuBtn.textContent = '⋮';
    top.appendChild(menuBtn);

    card.appendChild(top);

    const amountDiv = document.createElement('div');
    amountDiv.className = 'deposit-card-amount mono';
    amountDiv.textContent = formatMoney(d.amount);
    card.appendChild(amountDiv);

    el.depositList.appendChild(card);
  });
}

function renderTotals(ctx, dashboard) {
  const { el } = ctx;
  const { totals, fx } = dashboard;
  const displayCurrency = fx.display_currency;

  el.totalCostSum.textContent = formatMoney(totals.cost_basis);
  el.totalEvalSum.textContent = totals.eval_value != null ? formatMoney(totals.eval_value) : '-';

  el.totalMissingNote.hidden = totals.missing_cost_count === 0;
  if (totals.missing_cost_count > 0) {
    el.totalMissingNote.textContent = `환율 정보가 없는 ${totals.missing_cost_count}개 종목은 매입금액 합계에서 제외되었습니다. 아래에서 환율을 입력해주세요.`;
  }

  const evalNoteParts = [];
  if (totals.missing_eval_price_count > 0) evalNoteParts.push(`현재가 미입력 ${totals.missing_eval_price_count}개`);
  if (totals.missing_eval_fx_count > 0) evalNoteParts.push(`환율 정보 없음 ${totals.missing_eval_fx_count}개`);
  el.totalEvalMissingNote.hidden = evalNoteParts.length === 0;
  if (evalNoteParts.length > 0) {
    el.totalEvalMissingNote.textContent = `${evalNoteParts.join(', ')} 종목은 평가금액 합계에서 제외되었습니다.`;
  }

  el.totalDepositSum.textContent = totals.deposit_total != null ? formatMoney(totals.deposit_total) : '-';
  el.totalDepositMissingNote.hidden = totals.missing_deposit_fx_count === 0;
  if (totals.missing_deposit_fx_count > 0) {
    el.totalDepositMissingNote.textContent = `환율 정보가 없는 예금 ${totals.missing_deposit_fx_count}건은 예금총액 합계에서 제외되었습니다.`;
  }

  el.totalAssetSum.textContent = formatMoney(totals.asset_total);

  if (totals.pnl_amount != null) {
    el.totalPnlSum.textContent = `${formatMoney(totals.pnl_amount)} (${formatPercent(totals.pnl_pct)})`;
    el.totalPnlSum.className = 'mono ' + pnlClass(totals.pnl_amount);
  } else {
    el.totalPnlSum.textContent = '-';
    el.totalPnlSum.className = 'mono';
  }

  document.querySelectorAll('.currency-pill').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.currency === displayCurrency);
  });

  el.totalSummaryDetail.hidden = !ctx.state.showTotalDetail;
  el.totalChevron.classList.toggle('open', ctx.state.showTotalDetail);
  if (ctx.state.showTotalDetail) {
    renderTotalDetailRows(ctx, dashboard);
    renderFxSettings(ctx, fx);
  }
}

function renderTotalDetailRows(ctx, dashboard) {
  const { el } = ctx;
  el.totalDetailRows.innerHTML = '';
  dashboard.instruments.forEach((row) => {
    const item = document.createElement('div');
    item.className = 'total-detail-item';

    const rowDiv = document.createElement('div');
    rowDiv.className = 'total-detail-row';
    const nameSpan = document.createElement('span');
    nameSpan.textContent = row.instrument.name;
    const costSpan = document.createElement('span');
    costSpan.className = 'mono';
    costSpan.textContent = formatMoney(row.cost_basis);
    rowDiv.appendChild(nameSpan);
    rowDiv.appendChild(costSpan);
    item.appendChild(rowDiv);

    const evalRow = document.createElement('div');
    evalRow.className = 'total-detail-eval-row';
    if (row.eval_value == null) {
      evalRow.textContent = '평가금액 현재가 미입력';
    } else {
      evalRow.appendChild(document.createTextNode('평가금액 '));
      const evalSpan = document.createElement('span');
      evalSpan.className = 'mono ' + pnlClass(row.eval_value - row.cost_basis);
      evalSpan.textContent = formatMoney(row.eval_value);
      evalRow.appendChild(evalSpan);
    }
    item.appendChild(evalRow);
    el.totalDetailRows.appendChild(item);
  });
  if (dashboard.instruments.length === 0) {
    el.totalDetailRows.textContent = '등록된 종목이 없습니다.';
  }

  el.totalDepositDetailRows.innerHTML = '';
  dashboard.deposits.forEach((d) => {
    const item = document.createElement('div');
    item.className = 'total-detail-item';

    const rowDiv = document.createElement('div');
    rowDiv.className = 'total-detail-row';
    const nameSpan = document.createElement('span');
    nameSpan.textContent = d.account_name;
    const amountWrap = document.createElement('span');
    const badge = document.createElement('span');
    badge.className = 'currency-badge';
    badge.textContent = d.currency;
    const amountSpan = document.createElement('span');
    amountSpan.className = 'mono';
    amountSpan.textContent = ' ' + formatMoney(d.amount);
    amountWrap.appendChild(badge);
    amountWrap.appendChild(amountSpan);
    rowDiv.appendChild(nameSpan);
    rowDiv.appendChild(amountWrap);
    item.appendChild(rowDiv);
    el.totalDepositDetailRows.appendChild(item);
  });
}

function renderFxSettings(ctx, fx) {
  const { el } = ctx;
  const rates = fx.rates;
  el.fxStatus.textContent = '저장된 환율 (1 단위 통화의 원화 환산값)';

  el.fxRateRows.innerHTML = '';
  const currencies = new Set(['USD', 'JPY', ...Object.keys(rates)]);
  currencies.delete('KRW');
  currencies.forEach((currency) => {
    const row = document.createElement('div');
    row.className = 'fx-rate-row';
    const label = document.createElement('span');
    label.textContent = `1 ${currency} =`;
    row.appendChild(label);

    const input = document.createElement('input');
    input.type = 'number';
    input.className = 'fx-rate-input';
    input.step = '0.01';
    input.min = '0';
    input.dataset.currency = currency;
    const rate = rates[currency] ? rates[currency].rate_to_krw : null;
    input.value = rate != null ? Math.round(rate * 100) / 100 : '';
    row.appendChild(input);

    const unit = document.createElement('span');
    unit.textContent = '원';
    row.appendChild(unit);
    el.fxRateRows.appendChild(row);
  });
}

export async function autoFetchFxRates(ctx) {
  const FX_API_URL = 'https://api.frankfurter.dev/v1/latest';
  const need = ['USD', 'JPY'];
  try {
    const url = `${FX_API_URL}?from=KRW&to=${encodeURIComponent(need.join(','))}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error('fx http error');
    const data = await res.json();
    const rates = {};
    need.forEach((c) => {
      const perKrw = data.rates && data.rates[c];
      if (perKrw) rates[c] = 1 / perKrw;
    });
    if (Object.keys(rates).length === 0) return false;
    await api.putFx({ rates });
    return true;
  } catch (e) {
    return false;
  }
}

export async function refreshAllQuotes(ctx) {
  const { el } = ctx;
  el.btnRefreshAllQuotes.disabled = true;
  el.btnRefreshAllQuotes.textContent = '갱신 중...';
  el.refreshAllQuotesHint.hidden = true;
  let result;
  try {
    result = await api.refreshAllQuotes();
  } catch (e) {
    ctx.showToast('주가 갱신 실패: ' + e.message);
    return;
  } finally {
    el.btnRefreshAllQuotes.disabled = false;
    el.btnRefreshAllQuotes.textContent = '주가 갱신';
  }

  await loadAndRenderList(ctx);

  const parts = [];
  if (result.updated.length) parts.push(`${result.updated.length}개 갱신됨`);
  if (result.failed.length) parts.push(`${result.failed.length}개 실패`);
  if (result.skipped.length) parts.push(`KIS 코드 없음 ${result.skipped.length}개는 건너뜀`);
  if (parts.length === 0) {
    ctx.showToast('KIS 종목코드가 설정된 종목이 없습니다.');
    return;
  }
  el.refreshAllQuotesHint.hidden = false;
  el.refreshAllQuotesHint.textContent = parts.join(' · ');
  ctx.showToast('주가를 갱신했습니다.');
}
