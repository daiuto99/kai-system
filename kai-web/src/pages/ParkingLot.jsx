import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { Inbox, ArrowUpRight, Archive, RefreshCw } from 'lucide-react'

const TYPE_COLORS = {
  product:  'bg-blue-500/10 text-blue-400',
  idea:     'bg-purple-500/10 text-purple-400',
  task:     'bg-green-500/10 text-green-400',
  article:  'bg-orange-500/10 text-orange-400',
  note:     'bg-gray-500/10 text-gray-400',
  link:     'bg-cyan-500/10 text-cyan-400',
  default:  'bg-white/5 text-white/50',
}

const ADVISORS = ['chief', 'beats', 'biz', 'doc', 'coach', 'ember']

function CaptureCard({ item, onRoute, onArchive }) {
  const [routing, setRouting] = useState(false)
  const typeStyle = TYPE_COLORS[item.type] || TYPE_COLORS.default

  return (
    <div className="kai-card px-5 py-4">
      <div className="flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5 flex-wrap">
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${typeStyle}`}>
              {item.type || 'item'}
            </span>
            <span className="text-xs kai-text-subtle">{item.date}</span>
          </div>
          <p className="text-sm font-medium mb-1">{item.title}</p>
          {item.summary && (
            <p className="text-xs kai-text-secondary leading-relaxed">{item.summary}</p>
          )}
          {item.url && (
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-kai-blue hover:underline flex items-center gap-1 mt-1.5"
            >
              {item.url.length > 60 ? item.url.slice(0, 60) + '...' : item.url}
              <ArrowUpRight size={10} />
            </a>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {routing ? (
            <div className="flex gap-1 flex-wrap justify-end max-w-xs">
              {ADVISORS.map(a => (
                <button
                  key={a}
                  onClick={() => { onRoute(item.slug, a); setRouting(false) }}
                  className="text-xs px-2 py-1 rounded-lg bg-white/5 hover:bg-kai-blue/20 hover:text-kai-blue transition-colors capitalize"
                >
                  {a}
                </button>
              ))}
              <button
                onClick={() => setRouting(false)}
                className="text-xs px-2 py-1 rounded-lg bg-white/5 kai-text-subtle hover:bg-white/10 transition-colors"
              >
                ✕
              </button>
            </div>
          ) : (
            <>
              <button
                onClick={() => setRouting(true)}
                className="btn-ghost text-xs"
              >
                Route
              </button>
              <button
                onClick={() => onArchive(item.slug)}
                className="btn-ghost text-xs"
              >
                <Archive size={13} />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ParkingLot() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getParkingLot()
      setItems(data.items || [])
    } catch (e) {
      setError('Could not load parking lot.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleRoute(slug, advisor) {
    try {
      await api.routeCapture(slug, advisor)
      setItems(prev => prev.filter(i => i.slug !== slug))
    } catch { }
  }

  async function handleArchive(slug) {
    try {
      await api.archiveCapture(slug)
      setItems(prev => prev.filter(i => i.slug !== slug))
    } catch { }
  }

  return (
    <div className="max-w-3xl mx-auto px-8 py-10">
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Inbox size={20} className="kai-text-subtle" />
            Parking Lot
          </h1>
          <p className="kai-text-subtle text-sm mt-1">
            {items.length > 0 ? `${items.length} item${items.length !== 1 ? 's' : ''} captured` : 'Everything captured'}
          </p>
        </div>
        <button onClick={load} className="btn-ghost flex items-center gap-1.5 text-xs">
          <RefreshCw size={12} />
          Refresh
        </button>
      </div>

      {loading ? (
        <div className="kai-card px-5 py-12 text-center kai-text-subtle text-sm">
          Loading captures...
        </div>
      ) : error ? (
        <div className="kai-card px-5 py-8 text-center">
          <p className="text-sm text-kai-red/80">{error}</p>
        </div>
      ) : items.length === 0 ? (
        <div className="kai-card px-5 py-12 text-center">
          <Inbox size={32} className="kai-text-subtle mx-auto mb-3" />
          <p className="text-sm kai-text-subtle">The lot is empty. Drop something in #kai-parking-lot.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(item => (
            <CaptureCard
              key={item.slug}
              item={item}
              onRoute={handleRoute}
              onArchive={handleArchive}
            />
          ))}
        </div>
      )}
    </div>
  )
}
