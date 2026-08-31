import { useState, useEffect, useCallback } from 'react'
import { Sparkles, Check, X as XIcon, ChevronDown, RefreshCw } from 'lucide-react'
import { api } from '../lib/api'

// P-4a — the morning digest ON the P-3 surface. Custodian Findings ride the slice-1
// bridge into SILENT finding-cards in the T2 queue (notify=False); this is the PULL
// view that makes them visible and tap-to-approvable. Backend is already live —
// GET /t2/queue?kind=finding + POST /t2/respond — so this is pure surface, no new store.
// Renders nothing when the queue is empty, so it never clutters a quiet morning.

function ageOf(iso) {
  if (!iso) return ''
  try {
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.round(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.round(hrs / 24)}d ago`
  } catch { return '' }
}

export default function ProactiveDigest() {
  const [items, setItems]     = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy]       = useState({})   // id -> true while a tap is in flight
  const [open, setOpen]       = useState({})   // id -> detail expanded

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api.getProactiveQueue()
      setItems((d.queue || []).filter(e => e.status === 'pending'))
    } catch {
      setItems([])   // fail-soft: a quiet surface beats an error banner on the home page
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function resolve(id, approved) {
    setBusy(b => ({ ...b, [id]: true }))
    try {
      await api.respondProactive(id, approved)
      setItems(list => list.filter(e => e.id !== id))   // it's resolved — drop the card
    } catch {
      setBusy(b => ({ ...b, [id]: false }))   // let Leo retry the tap
    }
  }

  // Nothing pending (or still loading the first time) → render nothing at all.
  if (loading || items.length === 0) return null

  return (
    <div className="kai-card" style={{ padding: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Sparkles size={15} color="var(--accent)" strokeWidth={1.9} />
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Morning digest</span>
          <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-tertiary)', background: 'var(--bg-muted)', borderRadius: 10, padding: '2px 7px' }}>
            {items.length}
          </span>
        </span>
        <button onClick={load}
          style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-tertiary)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-secondary)' }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-tertiary)' }}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.map(e => {
          const isOpen = !!open[e.id]
          const isBusy = !!busy[e.id]
          const hasDetail = e.detail && e.detail.trim().length > 0
          return (
            <div key={e.id} style={{
              padding: '11px 13px', borderRadius: 10, border: '1px solid var(--border)',
              background: 'var(--bg-card)', opacity: isBusy ? 0.5 : 1, transition: 'opacity 0.15s',
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <button
                    onClick={() => hasDetail && setOpen(o => ({ ...o, [e.id]: !o[e.id] }))}
                    style={{ all: 'unset', cursor: hasDetail ? 'pointer' : 'default', display: 'flex', alignItems: 'center', gap: 6, width: '100%' }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: isOpen ? 'normal' : 'nowrap' }}>
                      {e.action}
                    </span>
                    {hasDetail && <ChevronDown size={13} color="var(--text-tertiary)" style={{ flexShrink: 0, transform: isOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }} />}
                  </button>
                  <div style={{ fontSize: 10, color: 'var(--text-subtle)', marginTop: 3 }}>
                    {e.advisor ? e.advisor.toUpperCase() : 'KAI'}{e.created_at ? ` · ${ageOf(e.created_at)}` : ''}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                  <button disabled={isBusy} onClick={() => resolve(e.id, true)} title="Approve"
                    style={{ all: 'unset', cursor: isBusy ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600, color: '#10b981', padding: '5px 10px', borderRadius: 8, background: 'rgba(16,185,129,0.10)', border: '1px solid rgba(16,185,129,0.25)' }}>
                    <Check size={13} /> Approve
                  </button>
                  <button disabled={isBusy} onClick={() => resolve(e.id, false)} title="Dismiss"
                    style={{ all: 'unset', cursor: isBusy ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', padding: '5px 10px', borderRadius: 8, background: 'var(--bg-muted)', border: '1px solid var(--border)' }}>
                    <XIcon size={13} /> Dismiss
                  </button>
                </div>
              </div>
              {isOpen && hasDetail && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 9, paddingTop: 9, borderTop: '1px solid var(--border)', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                  {e.detail}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
