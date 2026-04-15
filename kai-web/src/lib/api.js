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

export const api = {
  // Health
  health: () => get(`${BASE}/health`),

  // Harmony
  getHarmony: () => get(`${BASE}/harmony`),
  updateAspectStatus: (domainId, aspect, status) =>
    put(`${BASE}/harmony/${domainId}/aspect/${aspect}`, { status }),

  // Parking Lot
  getParkingLot: () => get(`${BASE}/parking-lot/list`),
  routeCapture: (slug, advisor) =>
    post(`${BASE}/parking-lot/${slug}/route`, { advisor }),
  archiveCapture: (slug) =>
    post(`${BASE}/parking-lot/${slug}/archive`, {}),
  quickCapture: (text) =>
    post(`${BASE}/parking-lot/quick`, { text }),

  // Focus / Today
  getFocusBrief: () => get(`${BASE}/focus/today`),

  // Council — chat
  sendMessage: (message, channel = 'kai') =>
    post(`${COUNCIL}/message`, { message, channel }),

  // Council — history
  getChannelHistory: (channel, limit = 80) =>
    get(`${COUNCIL}/history/${channel}?limit=${limit}`),

  // Insights
  getInsights: () => get(`${BASE}/insights`),
}
