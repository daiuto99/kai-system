// KAI service worker — P5 (KAI-1459). Makes the console an installable PWA:
// an app-shell cache so it opens instantly / offline-tolerant, WITHOUT ever caching
// live data. Alerts still ride Buzz/Telegram — this is the work surface, not the
// alert rail, so there are no push handlers here by design.
const CACHE = 'kai-shell-v1'
const SHELL = ['/', '/index.html', '/manifest.json', '/icon-192.png', '/icon-512.png']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return

  // NEVER cache live data — the API and council responses are always network-truth.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/council/') ||
      url.pathname.startsWith('/orchestrator/')) {
    return  // fall through to the network
  }

  // App navigations: network-first (fresh SPA), fall back to the cached shell offline.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match('/index.html').then((r) => r || caches.match('/')))
    )
    return
  }

  // Static assets (hashed Vite bundles, icons, fonts): cache-first, then populate.
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res && res.status === 200 && res.type === 'basic') {
        const copy = res.clone()
        caches.open(CACHE).then((c) => c.put(req, copy))
      }
      return res
    }).catch(() => hit))
  )
})
