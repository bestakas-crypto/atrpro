// 숫자/통화 표시 포맷터. 기존 atrsite app.js와 동일한 규칙을 그대로 유지한다
// (스펙 13.1 "유지할 부분" -- 숫자용 모노스페이스 글꼴, 표시 형식).

export function num(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

export function formatMoney(n) {
  if (n == null || !Number.isFinite(n)) return '-';
  return Math.round(n).toLocaleString('ko-KR');
}

export function formatPrice(n) {
  if (n == null || !Number.isFinite(n)) return '-';
  const rounded = Math.round(n * 100) / 100;
  return rounded.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatQty(n) {
  if (n == null || !Number.isFinite(n)) return '-';
  const rounded = Math.round(n * 10000) / 10000;
  return rounded.toLocaleString('ko-KR', { maximumFractionDigits: 4 });
}

export function formatPercent(n) {
  if (n == null || !Number.isFinite(n)) return '-';
  const rounded = Math.round(n * 100) / 100;
  return (rounded >= 0 ? '+' : '') + rounded.toFixed(2) + '%';
}

export function pnlClass(n) {
  if (n == null || !Number.isFinite(n)) return '';
  return n >= 0 ? 'pnl-pos' : 'pnl-neg';
}

export function todayDateStr() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// <input type="datetime-local"> 기본값 (초 단위 절삭, 로컬 타임존 그대로).
export function nowDatetimeLocalStr() {
  const d = new Date();
  const pad = (v) => String(v).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
