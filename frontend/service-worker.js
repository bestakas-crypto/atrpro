// 스펙 4.8 캐시 전략:
//   /api/            -> Network Only (캐시 저장 금지, 실패 시 index.html로 대체하지 않음)
//   페이지 이동(navigate) -> Network First, 실패 시 캐시된 index.html
//   정적 CSS/JS/아이콘/매니페스트 -> Network First + 캐시 갱신 (오프라인 시 캐시 폴백)
const CACHE_NAME = 'atrsite-pro-cache-v1';
const PRECACHE_URLS = [
  './',
  './index.html',
  './css/style.css',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

function isApiRequest(url) {
  return url.pathname.startsWith('/api/');
}

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);

  // /api/ 요청: 절대 캐시하지 않고, 실패해도 index.html로 대체하지 않는다.
  // (그렇지 않으면 프런트엔드가 HTML을 JSON으로 파싱하려다 예외가 난다 -- 스펙 4.8)
  if (isApiRequest(url)) {
    event.respondWith(fetch(event.request));
    return;
  }

  const isNavigate = event.request.mode === 'navigate';

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200 && response.type === 'basic') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => {
          if (cached) return cached;
          if (isNavigate) return caches.match('./index.html');
          return Response.error();
        })
      )
  );
});
