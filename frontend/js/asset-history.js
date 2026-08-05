// frontend/js/asset-history.js -- v1.3 자산추이 페이지 진입점.
// withdrawals.js/investment-schedule.js와 같은 독립 페이지 패턴(모듈 하나, DOM
// 직접 조작). 차트는 vendor/chart.umd.min.js(Chart.js 4.4.4, MIT, 로컬 정적
// 파일 -- CDN 미의존)를 <script> 전역(window.Chart)으로 불러와서 쓴다.
import { api, ApiError } from './api-client.js';

const QUALITY_LABELS = {
  COMPLETE: '완료', PARTIAL: '부분완료', FAILED: '실패', STALE_PRICE: '오래된 시세', MANUAL: '수동',
};

const state = {
  period: '3m',
  startDate: null,
  endDate: null,
  chartType: 'asset', // asset | profit | rate
  rows: [], // 현재 기간의 chart row 배열(원본, 원 단위 그대로)
  chart: null,
  chartFullscreen: null,
};

const el = {};

function cacheDom() {
  const ids = [
    'btn-close', 'btn-recalculate', 'summary-card', 'summary-date', 'summary-quality-badge',
    'summary-total-asset', 'summary-profit', 'summary-rate', 'summary-investment', 'summary-cash',
    'summary-note', 'no-snapshot-empty', 'period-bar', 'btn-toggle-custom', 'custom-range',
    'range-start', 'range-end', 'btn-apply-range', 'chart-tabs', 'chart-wrap', 'chart-canvas',
    'btn-expand', 'chart-empty', 'modal-fullscreen-chart', 'fullscreen-chart-title',
    'btn-close-fullscreen', 'chart-canvas-fullscreen', 'toast',
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

// ---------------------------------------------------------------------------
// 스펙 7절 -- 백만원 단위 표시(절사). DB/응답은 항상 원 단위 그대로, 여기서만 변환.
// ---------------------------------------------------------------------------

function fmtMillion(won) {
  if (won === null || won === undefined) return '-';
  const millions = Math.trunc(won / 1_000_000);
  return `${millions.toLocaleString('ko-KR')}백만원`;
}

function fmtWon(won) {
  if (won === null || won === undefined) return '-';
  return `${Math.round(won).toLocaleString('ko-KR')}원`;
}

function fmtSignedWon(won) {
  if (won === null || won === undefined) return '-';
  const sign = won > 0 ? '+' : '';
  return `${sign}${fmtWon(won)}`;
}

function fmtPct(rate) {
  if (rate === null || rate === undefined) return '-';
  const sign = rate > 0 ? '+' : '';
  return `${sign}${rate.toFixed(2)}%`;
}

function pnlClass(n) {
  if (n === null || n === undefined || n === 0) return 'pnl-zero';
  return n > 0 ? 'pnl-pos' : 'pnl-neg';
}

// ---------------------------------------------------------------------------
// 12.1 최신 현황
// ---------------------------------------------------------------------------

async function loadLatestSummary() {
  try {
    const snap = await api.getLatestSnapshot();
    el.summaryCard.hidden = false;
    el.noSnapshotEmpty.hidden = true;

    el.summaryDate.textContent = `${snap.snapshot_date} 기준 (생성: ${snap.snapshot_at.replace('T', ' ').slice(0, 16)})`;
    el.summaryQualityBadge.hidden = false;
    el.summaryQualityBadge.textContent = QUALITY_LABELS[snap.data_quality_status] || snap.data_quality_status;
    el.summaryQualityBadge.className = `ah-quality-badge status-${snap.data_quality_status}`;

    el.summaryTotalAsset.textContent = fmtMillion(snap.total_asset_value);
    el.summaryProfit.textContent = fmtSignedWon(snap.unrealized_profit);
    el.summaryProfit.className = `value ${pnlClass(snap.unrealized_profit)}`;
    el.summaryRate.textContent = fmtPct(snap.profit_rate);
    el.summaryRate.className = `rate ${pnlClass(snap.profit_rate)}`;
    el.summaryInvestment.textContent = fmtMillion(snap.investment_market_value);
    el.summaryCash.textContent = fmtMillion(snap.cash_deposit_value);

    if (snap.quality_notes) {
      el.summaryNote.hidden = false;
      el.summaryNote.textContent = `⚠ ${snap.quality_notes}`;
    } else {
      el.summaryNote.hidden = true;
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      el.summaryCard.hidden = true;
      el.noSnapshotEmpty.hidden = false;
    } else {
      showToast('최신 자산현황을 불러오지 못했습니다.');
    }
  }
}

// ---------------------------------------------------------------------------
// 12.2/12.3 기간·차트
// ---------------------------------------------------------------------------

async function loadChart() {
  try {
    const query = state.startDate && state.endDate
      ? { start_date: state.startDate, end_date: state.endDate }
      : { period: state.period };
    const { items } = await api.getSnapshotChart(query);
    state.rows = items;
    renderChart();
  } catch (e) {
    showToast('차트 데이터를 불러오지 못했습니다.');
  }
}

function chartDatasetsFor(type, rows) {
  const labels = rows.map((r) => r.snapshot_date);
  if (type === 'asset') {
    return {
      labels,
      datasets: [
        { label: '총자산', data: rows.map((r) => r.total_asset_value), borderColor: '#14181f', backgroundColor: '#14181f', tension: 0.15 },
        { label: '투자자산', data: rows.map((r) => r.investment_market_value), borderColor: '#1a6fc4', backgroundColor: '#1a6fc4', tension: 0.15, borderDash: [4, 3] },
        { label: '현금·예금', data: rows.map((r) => r.cash_deposit_value), borderColor: '#0e8a73', backgroundColor: '#0e8a73', tension: 0.15, borderDash: [4, 3] },
      ],
    };
  }
  if (type === 'profit') {
    return {
      labels,
      datasets: [
        { label: '평가손익', data: rows.map((r) => r.unrealized_profit), borderColor: '#cf3a3a', backgroundColor: '#cf3a3a', tension: 0.15 },
        { label: '실현손익', data: rows.map((r) => r.realized_profit), borderColor: '#a06a0a', backgroundColor: '#a06a0a', tension: 0.15, borderDash: [4, 3] },
      ],
    };
  }
  // rate
  return {
    labels,
    datasets: [
      { label: '평가수익률(%)', data: rows.map((r) => r.profit_rate), borderColor: '#6a3ccf', backgroundColor: '#6a3ccf', tension: 0.15 },
    ],
  };
}

function tooltipLines(row) {
  const lines = [
    `총자산: ${fmtWon(row.total_asset_value)}`,
    `투자자산: ${fmtWon(row.investment_market_value)}`,
    `현금·예금: ${fmtWon(row.cash_deposit_value)}`,
    `평가손익: ${fmtSignedWon(row.unrealized_profit)}`,
    `실현손익: ${fmtSignedWon(row.realized_profit)}`,
    `수익률: ${fmtPct(row.profit_rate)}`,
    `데이터 품질: ${QUALITY_LABELS[row.data_quality_status] || row.data_quality_status}`,
  ];
  if (row.usd_krw_rate) lines.push(`USD/KRW: ${row.usd_krw_rate.toLocaleString('ko-KR')}`);
  if (row.jpy_krw_rate) lines.push(`JPY/KRW: ${row.jpy_krw_rate.toLocaleString('ko-KR')}`);
  return lines;
}

function buildChartConfig(type, rows) {
  const { labels, datasets } = chartDatasetsFor(type, rows);
  const isRateChart = type === 'rate';
  return {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: {
          ticks: {
            callback: (value) => (isRateChart ? `${value}%` : fmtMillion(value)),
          },
        },
      },
      plugins: {
        legend: { display: true, labels: { boxWidth: 10, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: (items) => items[0]?.label ?? '',
            label: () => '', // 커스텀 afterBody로 전체 필드를 한 번에 보여줌(중복 방지로 개별 label은 비움)
            afterBody: (items) => {
              const idx = items[0]?.dataIndex;
              if (idx === undefined || !rows[idx]) return [];
              return tooltipLines(rows[idx]);
            },
          },
        },
      },
    },
  };
}

function renderChart() {
  const empty = state.rows.length === 0;
  el.chartEmpty.hidden = !empty;
  el.chartWrap.hidden = empty;
  if (empty) {
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    return;
  }
  const config = buildChartConfig(state.chartType, state.rows);
  if (state.chart) {
    state.chart.data = config.data;
    state.chart.options = config.options;
    state.chart.update();
  } else {
    state.chart = new window.Chart(el.chartCanvas.getContext('2d'), config);
  }
}

// ---------------------------------------------------------------------------
// 확대 보기(전체화면 API 대신 오버레이 모달 -- 스펙 13절)
// ---------------------------------------------------------------------------

function openFullscreen() {
  if (state.rows.length === 0) return;
  const labelMap = { asset: '총자산', profit: '손익', rate: '수익률' };
  el.fullscreenChartTitle.textContent = `${labelMap[state.chartType]} 추이`;
  el.modalFullscreenChart.hidden = false;
  const config = buildChartConfig(state.chartType, state.rows);
  if (state.chartFullscreen) state.chartFullscreen.destroy();
  state.chartFullscreen = new window.Chart(el.chartCanvasFullscreen.getContext('2d'), config);
}

function closeFullscreen() {
  el.modalFullscreenChart.hidden = true;
  if (state.chartFullscreen) { state.chartFullscreen.destroy(); state.chartFullscreen = null; }
}

// ---------------------------------------------------------------------------
// 이벤트
// ---------------------------------------------------------------------------

function selectPeriod(period) {
  state.period = period;
  state.startDate = null;
  state.endDate = null;
  el.customRange.hidden = true;
  [...el.periodBar.querySelectorAll('.ah-period-btn')].forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.period === period);
  });
  loadChart();
}

function selectChartType(type) {
  state.chartType = type;
  [...el.chartTabs.querySelectorAll('.ah-tab-btn')].forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.chart === type);
  });
  renderChart();
}

function bindEvents() {
  el.btnClose.addEventListener('click', () => window.close());

  el.btnRecalculate.addEventListener('click', async () => {
    try {
      await api.runSnapshotNow();
      showToast('오늘 스냅샷을 다시 계산했습니다.');
      await Promise.all([loadLatestSummary(), loadChart()]);
    } catch (e) {
      showToast('다시 계산하지 못했습니다.');
    }
  });

  el.periodBar.addEventListener('click', (e) => {
    const btn = e.target.closest('.ah-period-btn');
    if (btn) selectPeriod(btn.dataset.period);
  });

  el.btnToggleCustom.addEventListener('click', () => {
    el.customRange.hidden = !el.customRange.hidden;
  });

  el.btnApplyRange.addEventListener('click', () => {
    if (!el.rangeStart.value || !el.rangeEnd.value) { showToast('시작일과 종료일을 모두 입력하세요.'); return; }
    state.startDate = el.rangeStart.value;
    state.endDate = el.rangeEnd.value;
    [...el.periodBar.querySelectorAll('.ah-period-btn')].forEach((btn) => btn.classList.remove('active'));
    loadChart();
  });

  el.chartTabs.addEventListener('click', (e) => {
    const btn = e.target.closest('.ah-tab-btn');
    if (btn) selectChartType(btn.dataset.chart);
  });

  el.btnExpand.addEventListener('click', openFullscreen);
  el.btnCloseFullscreen.addEventListener('click', closeFullscreen);

  window.addEventListener('resize', () => {
    if (state.chart) state.chart.resize();
    if (state.chartFullscreen) state.chartFullscreen.resize();
  });
  window.addEventListener('orientationchange', () => {
    // 회전 직후엔 아직 새 뷰포트 치수가 반영되기 전이라 살짝 지연 후 재계산(스펙 13절).
    setTimeout(() => {
      if (state.chart) state.chart.resize();
      if (state.chartFullscreen) state.chartFullscreen.resize();
    }, 300);
  });
}

async function init() {
  cacheDom();
  bindEvents();
  selectPeriod('3m'); // 스펙 12.2 기본값 3개월(loadChart까지 트리거)
  selectChartType('asset');
  await Promise.all([loadLatestSummary(), loadChart()]);
}

document.addEventListener('DOMContentLoaded', init);
