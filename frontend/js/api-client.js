// 백엔드 REST API 클라이언트. 스펙 4.9(모듈 분리) + 4.8(서비스워커가 /api/를
// Network Only로 다루므로 여기서 실패는 항상 "진짜 네트워크/서버 오류"다).
import { loadSnapshot, saveSnapshot } from './offline-snapshot.js';

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : (detail && detail.message) || `API error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

// 스펙 14.1 -- 단일 사용자 인증. 서버(API_KEY 환경변수)가 켜져 있을 때만
// 의미가 있고, 로컬 개발(API_KEY 미설정)에서는 헤더를 보내도 서버가 무시한다.
const API_KEY_STORAGE_KEY = 'atrsite-pro-api-key';

function getStoredApiKey() {
  try {
    return localStorage.getItem(API_KEY_STORAGE_KEY) || '';
  } catch (e) {
    return '';
  }
}

function promptAndStoreApiKey() {
  const key = window.prompt('이 서버는 API 키가 필요합니다. 관리자에게 받은 키를 입력하세요.');
  if (key) {
    try { localStorage.setItem(API_KEY_STORAGE_KEY, key); } catch (e) { /* 무시 */ }
  }
  return key || '';
}

// 오프라인(네트워크 자체 실패)일 때만 캐시로 폴백한다 -- 서버가 4xx/5xx를
// "정상적으로" 응답한 경우는 오프라인이 아니므로 그대로 에러를 던진다.
async function request(method, path, body, { _retriedAuth = false } = {}) {
  const opts = { method, headers: {} };
  const apiKey = getStoredApiKey();
  if (apiKey) opts.headers['X-API-Key'] = apiKey;
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(path, opts);
  } catch (networkError) {
    if (method === 'GET') {
      const cached = loadSnapshot(path);
      if (cached) return { ...cached.data, __offline: true, __syncedAt: cached.syncedAt };
    }
    throw new ApiError(0, '네트워크에 연결할 수 없습니다.');
  }

  // API_KEY가 설정된 서버인데 키가 없거나 틀렸을 때 -- 한 번만 물어보고 재시도한다.
  if (res.status === 401 && !_retriedAuth) {
    const key = promptAndStoreApiKey();
    if (key) return request(method, path, body, { _retriedAuth: true });
  }

  if (res.status === 204) return null;

  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch (e) { data = text; }
  }

  if (!res.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
    throw new ApiError(res.status, detail);
  }

  if (method === 'GET') saveSnapshot(path, data);
  return data;
}

export const api = {
  getDashboard: () => request('GET', '/api/v1/dashboard'),
  refreshAllQuotes: () => request('POST', '/api/v1/dashboard/refresh-quotes'),

  createInstrument: (body) => request('POST', '/api/v1/instruments', body),
  getInstrument: (id) => request('GET', `/api/v1/instruments/${id}`),
  updateInstrumentSettings: (id, body) => request('PATCH', `/api/v1/instruments/${id}`, body),
  updateInstrumentManual: (id, body) => request('PATCH', `/api/v1/instruments/${id}/manual`, body),
  commitQuote: (id, price) => request('POST', `/api/v1/instruments/${id}/quote`, { price }),
  refreshQuote: (id) => request('POST', `/api/v1/instruments/${id}/quote/refresh`),
  commitAtr: (id, atr, tradeDate) => request('POST', `/api/v1/instruments/${id}/atr`, { atr, trade_date: tradeDate }),
  resetInstrument: (id) => request('POST', `/api/v1/instruments/${id}/reset`),
  deleteInstrument: (id) => request('DELETE', `/api/v1/instruments/${id}`),
  acknowledgeSignal: (id) => request('POST', `/api/v1/instruments/${id}/acknowledge`),

  createTrade: (instrumentId, body) => request('POST', `/api/v1/instruments/${instrumentId}/trades`, body),
  updateTrade: (instrumentId, tradeId, body) => request('PATCH', `/api/v1/instruments/${instrumentId}/trades/${tradeId}`, body),
  deleteTrade: (instrumentId, tradeId) => request('DELETE', `/api/v1/instruments/${instrumentId}/trades/${tradeId}`),

  listDeposits: () => request('GET', '/api/v1/deposits'),
  createDeposit: (body) => request('POST', '/api/v1/deposits', body),
  updateDeposit: (id, body) => request('PATCH', `/api/v1/deposits/${id}`, body),
  deleteDeposit: (id) => request('DELETE', `/api/v1/deposits/${id}`),

  getFx: () => request('GET', '/api/v1/fx'),
  putFx: (body) => request('PUT', '/api/v1/fx', body),

  importLegacy: (payload, { dryRun = true } = {}) =>
    request('POST', `/api/v1/legacy/import?dry_run=${dryRun ? '1' : '0'}`, payload),
};
