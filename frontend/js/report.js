// 인쇄/PDF 저장용 보고서 -- 메인 SPA(main.js)와 완전히 분리된 독립 페이지다.
// 같은 오리진이라 localStorage의 API 키를 api-client.js가 그대로 재사용한다.
import { api } from './api-client.js';
import { computeNativeTotals } from './dashboard.js';
import { formatMoney, formatPrice, formatPercent, pnlClass } from './formatters.js';

const TRIGGER_STATUSES = new Set([
  'BUY_TRIGGERED', 'TAKE_PROFIT_TRIGGERED', 'STOP_TRIGGERED', 'SELL_BOTH_TRIGGERED',
]);

function signalBadge(status) {
  const span = document.createElement('span');
  span.className = 'signal-badge';
  const labels = {
    BUY_TRIGGERED: ['매수 신호', 'buy'],
    TAKE_PROFIT_TRIGGERED: ['익절 신호', 'profit'],
    STOP_TRIGGERED: ['손절 신호', 'stop'],
    SELL_BOTH_TRIGGERED: ['익절+손절 동시', 'both'],
    NEUTRAL: ['관망', 'neutral'],
    NO_POSITION: ['미보유', 'neutral'],
  };
  const [label, cls] = labels[status] || ['-', 'neutral'];
  span.classList.add(cls);
  span.textContent = label;
  return span;
}

function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.text != null) node.textContent = opts.text;
  children.forEach((c) => node.appendChild(c));
  return node;
}

function renderHeader() {
  const now = new Date().toLocaleString('ko-KR');
  return el('div', { className: 'report-header' }, [
    el('h1', { text: 'ATRsite-pro 보고서' }),
    el('p', { className: 'report-meta', text: `생성 일시: ${now}` }),
    el('p', { className: 'report-disclaimer', text: '이 보고서는 생성 시점의 스냅샷이며, 매매 판단용으로 사용하지 마십시오.' }),
  ]);
}

function summaryCard(title, totals) {
  const card = el('div', { className: 'report-summary-card' }, [
    el('div', { className: 'card-title', text: title }),
  ]);
  const rows = [
    ['매입금액', formatMoney(totals.cost_basis)],
    ['평가금액', totals.eval_value != null ? formatMoney(totals.eval_value) : '-'],
    ['예금총액', totals.deposit_total != null ? formatMoney(totals.deposit_total) : '-'],
    ['총자산금액', formatMoney(totals.asset_total)],
    ['손익', totals.pnl_amount != null ? `${formatMoney(totals.pnl_amount)} (${formatPercent(totals.pnl_pct)})` : '-'],
  ];
  rows.forEach(([label, value]) => {
    const row = el('div', { className: 'card-row' }, [
      el('span', { className: 'label', text: label }),
      el('span', { className: 'value', text: value }),
    ]);
    card.appendChild(row);
  });
  return card;
}

function renderSummarySection(dashboard) {
  const section = el('div', { className: 'report-section' }, [
    el('h2', { text: '종합 자산 요약' }),
  ]);
  const grid = el('div', { className: 'report-summary-grid' }, [
    summaryCard('TOTAL (원화 환산)', dashboard.totals),
    summaryCard('KRW', computeNativeTotals(dashboard, 'KRW').totals),
    summaryCard('USD', computeNativeTotals(dashboard, 'USD').totals),
  ]);
  section.appendChild(grid);
  return section;
}

function renderHoldingsTable(dashboard) {
  const section = el('div', { className: 'report-section' }, [
    el('h2', { text: '보유 종목 요약' }),
  ]);
  if (dashboard.instruments.length === 0) {
    section.appendChild(el('p', { className: 'report-empty-note', text: '등록된 종목이 없습니다.' }));
    return section;
  }

  const table = el('table', { className: 'report-table' });
  const thead = el('thead', {}, [el('tr', {}, [
    el('th', { text: '종목명' }),
    el('th', { text: '통화' }),
    el('th', { text: '현재가' }),
    el('th', { text: '전일 대비' }),
    el('th', { text: '보유수량' }),
    el('th', { text: '평균단가' }),
    el('th', { text: '매입금액' }),
    el('th', { text: '평가금액' }),
    el('th', { text: '손익률' }),
    el('th', { text: '신호' }),
  ])]);
  table.appendChild(thead);

  const tbody = el('tbody');
  dashboard.instruments.forEach((row) => {
    const { instrument, position, quote, signal, cost_basis: cost, eval_value: evalValue } = row;
    const pct = evalValue != null && position.avg_price > 0
      ? (evalValue / position.quantity - position.avg_price) / position.avg_price * 100
      : null;
    const pctCell = el('td', { className: `mono ${pnlClass(pct)}`, text: formatPercent(pct) });
    const changePct = quote ? quote.change_pct : null;
    const changeCell = el('td', { className: `mono ${pnlClass(changePct)}`, text: changePct != null ? formatPercent(changePct) : '-' });
    tbody.appendChild(el('tr', {}, [
      el('td', { text: instrument.name }),
      el('td', { text: instrument.currency }),
      el('td', { className: 'mono', text: quote ? formatPrice(quote.price) : '-' }),
      changeCell,
      el('td', { className: 'mono', text: formatPrice(position ? position.quantity : 0) }),
      el('td', { className: 'mono', text: formatPrice(position ? position.avg_price : 0) }),
      el('td', { className: 'mono', text: formatMoney(cost) }),
      el('td', { className: 'mono', text: evalValue != null ? formatMoney(evalValue) : '-' }),
      pctCell,
      el('td', {}, [signalBadge(signal ? signal.status : null)]),
    ]));
  });
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}

function renderSignalsSection(dashboard) {
  const section = el('div', { className: 'report-section' }, [
    el('h2', { text: '주의가 필요한 신호' }),
  ]);
  const triggered = dashboard.instruments.filter((row) => row.signal && TRIGGER_STATUSES.has(row.signal.status));
  if (triggered.length === 0) {
    section.appendChild(el('p', { className: 'report-empty-note', text: '현재 주의가 필요한 신호가 없습니다.' }));
    return section;
  }
  const table = el('table', { className: 'report-table' });
  table.appendChild(el('thead', {}, [el('tr', {}, [
    el('th', { text: '종목명' }),
    el('th', { text: '신호' }),
    el('th', { text: '사유' }),
  ])]));
  const tbody = el('tbody');
  triggered.forEach((row) => {
    tbody.appendChild(el('tr', {}, [
      el('td', { text: row.instrument.name }),
      el('td', {}, [signalBadge(row.signal.status)]),
      el('td', { text: row.signal.reason || '-' }),
    ]));
  });
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}

function renderThresholdsSection(dashboard) {
  const section = el('div', { className: 'report-section' }, [
    el('h2', { text: '종목별 매매 기준' }),
  ]);
  if (dashboard.instruments.length === 0) {
    section.appendChild(el('p', { className: 'report-empty-note', text: '등록된 종목이 없습니다.' }));
    return section;
  }
  const table = el('table', { className: 'report-table' });
  table.appendChild(el('thead', {}, [el('tr', {}, [
    el('th', { text: '종목명' }),
    el('th', { text: 'ATR(14)' }),
    el('th', { text: '다음 매수가' }),
    el('th', { text: '익절가' }),
    el('th', { text: '추적 손절선' }),
    el('th', { text: '보유 후 최고가' }),
  ])]));
  const tbody = el('tbody');
  dashboard.instruments.forEach((row) => {
    const signal = row.signal || {};
    const atrValue = row.atr ? row.atr.atr : null;
    tbody.appendChild(el('tr', {}, [
      el('td', { text: row.instrument.name }),
      el('td', { className: 'mono', text: atrValue != null ? formatPrice(atrValue) : '-' }),
      el('td', { className: 'mono', text: signal.next_buy_price != null ? formatPrice(signal.next_buy_price) : '-' }),
      el('td', { className: 'mono', text: signal.take_profit_price != null ? formatPrice(signal.take_profit_price) : '-' }),
      el('td', { className: 'mono', text: signal.trailing_stop_price != null ? formatPrice(signal.trailing_stop_price) : '-' }),
      el('td', { className: 'mono', text: row.instrument.post_entry_high_price != null ? formatPrice(row.instrument.post_entry_high_price) : '-' }),
    ]));
  });
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}

function renderDepositsSection(dashboard) {
  const section = el('div', { className: 'report-section' }, [
    el('h2', { text: '예금 현황' }),
  ]);
  if (dashboard.deposits.length === 0) {
    section.appendChild(el('p', { className: 'report-empty-note', text: '등록된 예금이 없습니다.' }));
    return section;
  }
  const table = el('table', { className: 'report-table' });
  table.appendChild(el('thead', {}, [el('tr', {}, [
    el('th', { text: '계좌명' }),
    el('th', { text: '통화' }),
    el('th', { text: '금액' }),
  ])]));
  const tbody = el('tbody');
  dashboard.deposits.forEach((d) => {
    tbody.appendChild(el('tr', {}, [
      el('td', { text: d.account_name }),
      el('td', { text: d.currency }),
      el('td', { className: 'mono', text: formatMoney(d.amount) }),
    ]));
  });
  table.appendChild(tbody);
  section.appendChild(table);
  return section;
}

function renderFooter() {
  return el('div', { className: 'report-footer' }, [
    el('p', { text: 'ATRsite-pro -- ATR 기반 분할매수/분할매도 의사결정 보조 도구' }),
  ]);
}

async function main() {
  const root = document.getElementById('report-root');
  let dashboard;
  try {
    dashboard = await api.getDashboard();
  } catch (e) {
    root.innerHTML = '';
    root.appendChild(el('p', { className: 'report-empty-note', text: '보고서를 불러오지 못했습니다: ' + e.message }));
    return;
  }

  root.innerHTML = '';
  root.appendChild(renderHeader());
  root.appendChild(renderSummarySection(dashboard));
  root.appendChild(renderHoldingsTable(dashboard));
  root.appendChild(renderSignalsSection(dashboard));
  root.appendChild(renderThresholdsSection(dashboard));
  root.appendChild(renderDepositsSection(dashboard));
  root.appendChild(renderFooter());
}

document.getElementById('btn-print').addEventListener('click', () => window.print());
document.getElementById('btn-close').addEventListener('click', () => window.close());

main();
