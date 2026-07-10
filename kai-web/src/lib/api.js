const BASE = '/api'
const COUNCIL = '/council'

async function get(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

async function put(url, body) {
  const r = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

async function patch(url, body) {
  const r = await fetch(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

async function del(url) {
  const r = await fetch(url, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

export const api = {
  // Health
  health: () => get(`${BASE}/health`),
  getCloseStatus: () => get(`${BASE}/session/close-status`),

  // Harmony
  getHarmony: () => get(`${BASE}/harmony`),
  updateAspectStatus: (domainId, aspect, status) =>
    put(`${BASE}/harmony/${domainId}/aspect/${aspect}`, { status }),

  // Parking Lot / Lot Inventory
  getParkingLot: () => get(`${BASE}/parking-lot/list`),
  triageCapture: (slug, action, advisor = 'kai', notes = '') =>
    post(`${BASE}/parking-lot/${slug}/triage`, { action, advisor, notes }),
  routeCapture: (slug, advisor) =>
    post(`${BASE}/parking-lot/${slug}/route`, { advisor }),
  archiveCapture: (slug) =>
    post(`${BASE}/parking-lot/${slug}/archive`, {}),
  deleteCapture: (slug) =>
    del(`${BASE}/parking-lot/${slug}`),
  quickCapture: (text) =>
    post(`${BASE}/parking-lot/quick`, { text }),
  enrichAll: () =>
    post(`${BASE}/parking-lot/enrich-all`, {}),
  patchCapture: (slug, data) =>
    patch(`${BASE}/parking-lot/${slug}`, data),

  // Focus / Today
  getFocusBrief: () => get(`${BASE}/focus/today`),
  getTodayFocus: () => get(`${BASE}/focus/today`),

  // Council — chat. No history param: memory is server-owned (CONTEXT_SPEC
  // §4.1) — a client-supplied history field is rejected with 400.
  sendMessage: (message, channel = 'kai') =>
    post(`${COUNCIL}/message`, { message, channel,
      trigger_source: `dashboard:chat:${channel}` }),

  // Council — history
  getChannelHistory: (channel, limit = 80) =>
    get(`${COUNCIL}/history/${channel}?limit=${limit}`),
  clearHistory: (channel) =>
    fetch(`${COUNCIL}/history/${channel}`, { method: 'DELETE' }).then(r => r.json()),

  // Projects
  setupProject: (body) => post(`${BASE}/projects/setup`, body),

  // Wellbeing check-in
  getCheckin: () => get(`${BASE}/checkin`),
  saveCheckin: (body) => post(`${BASE}/checkin`, body),
  getCheckinHistory: (limit = 14) => get(`${BASE}/checkin/history?limit=${limit}`),

  // Insights
  getInsights: () => get(`${BASE}/insights`),

  // Usage / cost
  getTokenUsage: () => get(`${BASE}/token-usage`),

  // Generic helpers — auto-prefix /api for worker routes. Callers like WordPress.jsx
  // use api.get('/wordpress/sites') → fetch('/api/wordpress/sites').
  get:   (path) => get(`${BASE}${path}`),
  post:  (path, body) => post(`${BASE}${path}`, body),
  patch: (path, body) => patch(`${BASE}${path}`, body),
  del:   (path) => del(`${BASE}${path}`),
}
