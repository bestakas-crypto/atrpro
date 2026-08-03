// 종목탐구 -- 메인 SPA와 분리된 독립 페이지(briefing.html과 동일한 패턴).
// 검색 -> 기업 확인(동명이인/ETF 오인 방지) -> 분석 진행상태 폴링 -> 결과.
import { api } from './api-client.js';

const el = {};
function cacheDom() {
  const ids = [
    'btn-close', 'search-input', 'btn-search', 'search-status', 'search-results', 'search-notices',
    'recent-section', 'recent-list',
    'view-search', 'view-confirm', 'view-progress', 'view-result',
    'confirm-card', 'btn-confirm-analyze', 'btn-confirm-back',
    'progress-status',
    'result-glance', 'result-warnings', 'result-meta', 'result-text',
    'btn-reanalyze', 'btn-back-to-search',
  ];
  ids.forEach((id) => {
    const camel = id.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    el[camel] = document.getElementById(id);
  });
}

const STATUS_LABELS = {
  PENDING: '분석 대기 중...',
  COLLECTING_DATA: '재무 데이터를 수집하는 중...',
  COMPUTING_METRICS: '재무 지표를 계산하는 중...',
  SEARCHING_NEWS: '최신 공시·뉴스를 조회하는 중...',
  ANALYZING: '분석 결과를 작성하는 중...',
};

const VERDICT_LABELS = {
  IMPROVING: '개선', STABLE: '중립', DETERIORATING: '악화',
  CAUTION: '주의', INSUFFICIENT_DATA: '자료부족',
};

const GLANCE_METRICS = [
  { key: 'revenue', label: '성장(매출)' },
  { key: 'operating_margin', label: '수익성(영업이익률)' },
  { key: 'fcf', label: '현금흐름(FCF)' },
  { key: 'net_debt', label: '재무건전성(순차입금)' },
];

const state = { selectedMatch: null, company: null, pollTimer: null };

function showView(name) {
  ['search', 'confirm', 'progress', 'result'].forEach((v) => {
    el[`view${v[0].toUpperCase()}${v.slice(1)}`].hidden = v !== name;
  });
}

function stopPolling() {
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

// ---------- 1. 검색 ----------
async function doSearch() {
  const q = el.searchInput.value.trim();
  if (!q) return;
  el.searchStatus.hidden = false;
  el.searchStatus.textContent = '검색 중...';
  el.searchResults.innerHTML = '';
  el.searchNotices.hidden = true;
  try {
    const { results, notices } = await api.searchCompany(q);
    el.searchStatus.hidden = true;
    renderSearchResults(results);
    if (notices && notices.length) {
      el.searchNotices.hidden = false;
      el.searchNotices.textContent = notices.join(' ');
    }
  } catch (e) {
    el.searchStatus.textContent = '검색 실패: ' + e.message;
  }
}

function renderSearchResults(results) {
  el.searchResults.innerHTML = '';
  if (!results || results.length === 0) {
    const p = document.createElement('p');
    p.className = 'ce-hint';
    p.textContent = '검색 결과가 없습니다.';
    el.searchResults.appendChild(p);
    return;
  }
  results.forEach((match) => el.searchResults.appendChild(buildResultItem(match)));
}

function buildResultItem(match) {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = 'ce-result-item';
  const main = document.createElement('div');
  main.className = 'ce-result-main';
  const name = document.createElement('span');
  name.className = 'ce-result-name';
  name.textContent = match.name;
  const sub = document.createElement('span');
  sub.className = 'ce-result-sub';
  sub.textContent = match.country === 'US' ? match.ticker : (match.stock_code || match.corp_code);
  main.appendChild(name);
  main.appendChild(sub);
  const badge = document.createElement('span');
  badge.className = 'ce-result-country';
  badge.textContent = match.country === 'US' ? '미국' : '한국';
  item.appendChild(main);
  item.appendChild(badge);
  item.addEventListener('click', () => selectMatch(match));
  return item;
}

async function loadRecent() {
  try {
    const recent = await api.listRecentCompanyAnalyses();
    if (!recent || recent.length === 0) return;
    el.recentSection.hidden = false;
    el.recentList.innerHTML = '';
    recent.forEach((c) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'ce-result-item';
      const main = document.createElement('div');
      main.className = 'ce-result-main';
      const name = document.createElement('span');
      name.className = 'ce-result-name';
      name.textContent = c.name;
      const sub = document.createElement('span');
      sub.className = 'ce-result-sub';
      sub.textContent = c.primary_ticker;
      main.appendChild(name);
      main.appendChild(sub);
      item.appendChild(main);
      item.addEventListener('click', () => {
        state.company = c;
        state.company.id = c.id;
        runAnalysis();
      });
      el.recentList.appendChild(item);
    });
  } catch (e) { /* 최근 분석 로드 실패는 조용히 무시(핵심 기능 아님) */ }
}

// ---------- 2. 기업 확인 ----------
async function selectMatch(match) {
  state.selectedMatch = match;
  try {
    const company = match.country === 'US'
      ? await api.resolveCompanyUS({ cik: match.cik, ticker: match.ticker, name: match.name })
      : await api.resolveCompanyKR({ corp_code: match.corp_code, corp_name: match.name, stock_code: match.stock_code });
    state.company = company;
    renderConfirm(company);
    showView('confirm');
  } catch (e) {
    el.searchNotices.hidden = false;
    el.searchNotices.textContent = '기업 확인 실패: ' + e.message;
  }
}

function renderConfirm(company) {
  el.confirmCard.innerHTML = '';
  const name = document.createElement('div');
  name.className = 'ce-confirm-name';
  name.textContent = company.name;
  el.confirmCard.appendChild(name);

  const rows = [
    ['티커', company.primary_ticker],
    ['국가', company.country === 'US' ? '미국' : '한국'],
    ['통화', company.currency],
    ['구분', company.security_type === 'ETF' ? 'ETF' : '개별 기업'],
  ];
  if (company.industry_template) rows.push(['업종', company.industry_template === 'semiconductor' ? '반도체' : company.industry_template]);
  rows.forEach(([label, value]) => {
    const row = document.createElement('div');
    row.className = 'ce-confirm-row';
    row.innerHTML = `<span>${label}</span><strong></strong>`;
    row.querySelector('strong').textContent = value;
    el.confirmCard.appendChild(row);
  });

  if (company.security_type === 'ETF') {
    const notice = document.createElement('p');
    notice.className = 'ce-notice';
    notice.style.marginTop = '12px';
    notice.textContent = 'ETF는 기업 재무제표 분석 대상이 아니라서, 이번 버전에서는 분석을 지원하지 않습니다.';
    el.confirmCard.appendChild(notice);
    el.btnConfirmAnalyze.disabled = true;
  } else {
    el.btnConfirmAnalyze.disabled = false;
  }
}

// ---------- 3. 분석 진행 ----------
async function runAnalysis() {
  showView('progress');
  el.progressStatus.textContent = '분석을 시작하는 중...';
  try {
    const request = await api.runCompanyAnalysis(state.company.id);
    pollAnalysis(request.id);
  } catch (e) {
    el.progressStatus.textContent = '분석 시작 실패: ' + e.message;
  }
}

function pollAnalysis(requestId) {
  stopPolling();
  const tick = async () => {
    try {
      const polled = await api.getCompanyAnalysis(requestId);
      if (polled.status === 'COMPLETED') {
        renderResult(polled);
        showView('result');
        return;
      }
      if (polled.status === 'FAILED') {
        el.progressStatus.textContent = '분석 실패: ' + (polled.error_message || '알 수 없는 오류');
        return;
      }
      el.progressStatus.textContent = STATUS_LABELS[polled.status] || polled.status;
      state.pollTimer = setTimeout(tick, 3000);
    } catch (e) {
      el.progressStatus.textContent = '상태 확인 실패: ' + e.message;
    }
  };
  tick();
}

// ---------- 4. 결과 ----------
function renderResult(polled) {
  const snapshot = JSON.parse(polled.result.snapshot_json);
  renderGlance(snapshot);
  renderWarnings(snapshot.warnings);

  const created = new Date(polled.result.created_at);
  el.resultMeta.innerHTML = '';
  const time = document.createElement('span');
  time.textContent = '생성: ' + created.toLocaleString('ko-KR');
  const badge = document.createElement('span');
  badge.className = 'ce-provider-badge';
  badge.textContent = polled.result.provider;
  el.resultMeta.appendChild(time);
  el.resultMeta.appendChild(badge);

  el.resultText.textContent = polled.result.result_text;
}

function renderGlance(snapshot) {
  el.resultGlance.innerHTML = '';
  const periods = snapshot.quarterly_periods || [];
  const latest = periods[periods.length - 1];
  if (!latest) return;
  GLANCE_METRICS.forEach(({ key, label }) => {
    const comp = latest.comparisons ? latest.comparisons[key] : null;
    const verdict = comp ? (comp.yoy_verdict !== 'INSUFFICIENT_DATA' ? comp.yoy_verdict : comp.qoq_verdict) : 'INSUFFICIENT_DATA';
    const item = document.createElement('div');
    item.className = 'ce-glance-item';
    const l = document.createElement('div');
    l.className = 'ce-glance-label';
    l.textContent = label;
    const v = document.createElement('div');
    v.className = 'ce-glance-value ce-verdict-' + verdict;
    v.textContent = VERDICT_LABELS[verdict] || verdict;
    item.appendChild(l);
    item.appendChild(v);
    el.resultGlance.appendChild(item);
  });
}

function renderWarnings(warnings) {
  if (!warnings || warnings.length === 0) {
    el.resultWarnings.hidden = true;
    return;
  }
  el.resultWarnings.hidden = false;
  el.resultWarnings.innerHTML = '<div class="ce-warnings-title">유의사항</div>';
  warnings.forEach((w) => {
    const p = document.createElement('div');
    p.className = 'ce-warning-item';
    p.textContent = '• ' + w.message;
    el.resultWarnings.appendChild(p);
  });
}

// ---------- 이벤트 ----------
function bindEvents() {
  el.btnClose.addEventListener('click', () => {
    if (window.history.length > 1) window.history.back();
    else window.location.href = 'index.html';
  });
  el.btnSearch.addEventListener('click', doSearch);
  el.searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

  el.btnConfirmAnalyze.addEventListener('click', runAnalysis);
  el.btnConfirmBack.addEventListener('click', () => showView('search'));

  el.btnReanalyze.addEventListener('click', runAnalysis);
  el.btnBackToSearch.addEventListener('click', () => {
    stopPolling();
    el.searchInput.value = '';
    el.searchResults.innerHTML = '';
    showView('search');
    loadRecent();
  });
}

function init() {
  cacheDom();
  bindEvents();
  showView('search');
  loadRecent();
}

document.addEventListener('DOMContentLoaded', init);
