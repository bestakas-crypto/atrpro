// 스펙 13.5 -- 오프라인에서는 "마지막으로 성공한 GET 응답"만 열람 전용으로 보여준다.
// 신규 체결 저장/설정 변경 등 쓰기 작업은 애초에 오프라인에서 시도되지 않으며
// (네트워크 오류로 그대로 실패), 이 모듈은 화면 표시용 스냅샷 캐시만 담당한다.
const STORAGE_KEY = 'atrsite-pro-offline-snapshot';

function loadAll() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

function saveAll(all) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch (e) {
    // localStorage 용량 초과 등은 오프라인 열람 기능만 못 쓰게 될 뿐이므로 무시한다.
  }
}

export function saveSnapshot(key, data) {
  const all = loadAll();
  all[key] = { data, syncedAt: new Date().toISOString() };
  saveAll(all);
}

export function loadSnapshot(key) {
  const all = loadAll();
  return all[key] || null;
}

export function latestSyncedAt() {
  const all = loadAll();
  const stamps = Object.values(all).map((v) => v.syncedAt).filter(Boolean).sort();
  return stamps.length ? stamps[stamps.length - 1] : null;
}
