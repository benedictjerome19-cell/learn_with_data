/* Service Worker for Consistency Pulse PWA */
const CACHE_NAME = 'pulse-cache-v3';
// Only public static assets are cached; authenticated pages and APIs always hit the network
const ASSETS_TO_CACHE = [
  '/static/styles.css?v=3',
  '/static/js/app.js?v=3',
  '/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE).catch((err) => {
        console.warn('Non-fatal cache warm-up error:', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const isStatic = url.origin === self.location.origin &&
    (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json');
  if (event.request.method !== 'GET' || !isStatic) {
    return;
  }
  // Network first, cached copy when offline
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
