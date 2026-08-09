import { api } from './api-client.js';
import { formatMoney, formatPrice, formatQty } from './formatters.js';

const el = {};

function cacheDom() {
  [
    'btn-close', 'start-date', 'end-date', 'base-currency', 'btn-load',
    'total-pnl', 'summary-meta', 'items', 'empty', 'toast',
  ].forEach((id) => {
    el[id.replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase())] = document.getElementById(id);
  });
}

function showToast(message) {
  el.toast.textContent = message;
  el.toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { el.toast.hidden = true; }, 2400);
}

function dateStr(d) {
  const pad = (v) => String(v).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function monthStart(d) {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function monthEnd(d) {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}

function setRange(kind) {
  const now = new Date();
  if (kind === 'last-month') {
    const last = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    el.startDate.value = dateStr(monthStart(last));
    el.endDate.value = dateStr(monthEnd(last));
  } else if (kind === 'this-year') {
    el.startDate.value = `${now.getFullYear()}-01-01`;
    el.endDate.value = dateStr(now);
  } else {
    el.startDate.value = dateStr(monthStart(now));
    el.endDate.value = dateStr(now);
  }
}

function moneyWithCurrency(value, currency) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '-';
  const sign = value > 0 ? '+' : '';
  return `${sign}${formatMoney(value)} ${currency}`;
}

function nativeMoney(value, currency) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '-';
  const sign = value > 0 ? '+' : '';
  return `${sign}${formatPrice(value)} ${currency}`;
}

function pnlClass(value) {
  if (!value) return 'pnl-zero';
  return value > 0 ? 'pnl-pos' : 'pnl-neg';
}

async function loadRealizedPnl() {
  if (!el.startDate.value || !el.endDate.value) {
    showToast('시작일과 종료일을 모두 입력하세요.');
    return;
  }
  try {
    const query = { start_date: el.startDate.value, end_date: el.endDate.value };
    const currency = el.baseCurrency.value.trim().toUpperCase();
    if (currency) query.currency = currency;
    const data = await api.getRealizedPnl(query);
    render(data);
  } catch (error) {
    showToast('확정손익을 불러오지 못했습니다.');
  }
}

function render(data) {
  el.totalPnl.textContent = moneyWithCurrency(data.total_realized_pnl, data.base_currency);
  el.totalPnl.className = `value big ${pnlClass(data.total_realized_pnl)}`;
  const sellCount = data.items.reduce((sum, item) => sum + item.sell_count, 0);
  const fxNote = data.missing_fx_count ? ` · 환산불가 ${data.missing_fx_count}종목` : '';
  el.summaryMeta.textContent = `${data.start_date} ~ ${data.end_date} · 매도 ${sellCount}건 · ${data.base_currency}${fxNote}`;

  el.items.replaceChildren();
  el.empty.hidden = data.items.length > 0;
  data.items.forEach((item) => el.items.appendChild(renderInstrument(item, data.base_currency)));
}

function renderInstrument(item, baseCurrency) {
  const card = document.createElement('div');
  card.className = 'instrument-card';
  const head = document.createElement('div');
  head.className = 'instrument-head';
  head.append(
    cell(item.instrument_name, '종목명', 'instrument-name'),
    cell(item.currency, '통화'),
    cell(nativeMoney(item.realized_pnl_native, item.currency), '원래통화', pnlClass(item.realized_pnl_native)),
    cell(moneyWithCurrency(item.realized_pnl_converted, baseCurrency), '환산금액', pnlClass(item.realized_pnl_converted)),
    cell(`${item.sell_count}건`, '매도'),
  );
  card.appendChild(head);

  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = '개별 매도 내역';
  details.appendChild(summary);
  details.appendChild(renderSellTable(item.sells, item.currency));
  card.appendChild(details);
  return card;
}

function cell(value, label, extraClass = '') {
  const div = document.createElement('div');
  const lab = document.createElement('span');
  lab.className = 'cell-label';
  lab.textContent = label;
  const val = document.createElement('span');
  val.className = `mono ${extraClass}`;
  val.textContent = value;
  div.append(lab, val);
  return div;
}

function renderSellTable(sells, currency) {
  const table = document.createElement('table');
  table.className = 'sell-table';
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  ['날짜', '수량', '가격', '손익'].forEach((label) => {
    const th = document.createElement('th');
    th.textContent = label;
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  const tbody = document.createElement('tbody');
  sells.forEach((sell) => {
    const tr = document.createElement('tr');
    [
      sell.executed_at.replace('T', ' ').slice(0, 16),
      formatQty(sell.quantity),
      nativeMoney(sell.price, currency),
      nativeMoney(sell.realized_pnl_native, currency),
    ].forEach((value, idx) => {
      const td = document.createElement('td');
      td.className = idx === 3 ? pnlClass(sell.realized_pnl_native) : '';
      td.textContent = value;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.append(thead, tbody);
  return table;
}

function bindEvents() {
  el.btnClose.addEventListener('click', () => window.close());
  el.btnLoad.addEventListener('click', loadRealizedPnl);
  document.querySelectorAll('.rp-chip').forEach((btn) => {
    btn.addEventListener('click', () => {
      setRange(btn.dataset.range);
      loadRealizedPnl();
    });
  });
}

async function init() {
  cacheDom();
  bindEvents();
  setRange('this-month');
  await loadRealizedPnl();
}

document.addEventListener('DOMContentLoaded', init);
