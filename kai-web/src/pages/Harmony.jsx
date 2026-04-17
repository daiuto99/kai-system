import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { HARMONY_DOMAINS, domainOverallStatus } from '../lib/harmonyData'
import { StatusBadge, StatusDot, StatusToggle } from '../components/StatusBadge'
import { ChevronDown, ChevronRight } from 'lucide-react'

const ASPECT_LABELS = {
  premise: 'Premise',
  vision:  'Vision',
  purpose: 'Purpose',
  strategy: 'Strategy'
}

const ASPECT_DESCRIPTIONS = {
  premise:  'Core beliefs you choose to hold in this area',
  vision:   'What "great" looks like when fully expressed',
  purpose:  'Why this area matters and what\'s on the line',
  strategy: 'How you intend to live this out day-to-day'
}

function AspectRow({ aspect, data, onStatusChange }) {
  return (
    <div style={{ padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <p style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)', margin: 0 }}>{ASPECT_LABELS[aspect]}</p>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: 0, display: 'none' }} className="sm:block">
              — {ASPECT_DESCRIPTIONS[aspect]}
            </p>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {data.statements.map((s, i) => (
              <p key={i} style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5, margin: 0 }}>{s}</p>
            ))}
          </div>
        </div>
        <div style={{ flexShrink: 0, paddingTop: 2 }}>
          <StatusToggle status={data.status} onChange={(s) => onStatusChange(aspect, s)} />
        </div>
      </div>
    </div>
  )
}

function DomainCard({ domain, onStatusChange }) {
  const [expanded, setExpanded] = useState(false)
  const overall = domainOverallStatus(domain)

  return (
    <div className="kai-card" style={{ overflow: 'hidden' }}>
      <button
        onClick={() => setExpanded(e => !e)}
        style={{
          width: '100%', padding: '14px 20px', display: 'flex', alignItems: 'center',
          gap: 12, textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer',
          transition: 'background 0.15s', fontFamily: 'inherit',
        }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-surface)'}
        onMouseLeave={e => e.currentTarget.style.background = 'none'}
      >
        <StatusDot status={overall} size={9} />
        <span style={{ fontSize: 18 }}>{domain.icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)', margin: 0 }}>{domain.name}</p>
          <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
            {Object.values(domain.aspects).map((a, i) => (
              <StatusDot key={i} status={a.status} size={5} />
            ))}
          </div>
        </div>
        <StatusBadge status={overall} />
        {expanded
          ? <ChevronDown size={15} color="#9ca3af" style={{ flexShrink: 0 }} />
          : <ChevronRight size={15} color="#9ca3af" style={{ flexShrink: 0 }} />
        }
      </button>

      {expanded && (
        <div style={{ padding: '0 20px 16px', borderTop: '1px solid var(--border)' }}>
          <div style={{ paddingTop: 4 }}>
            {Object.entries(domain.aspects).map(([aspect, data]) => (
              <AspectRow
                key={aspect}
                aspect={aspect}
                data={data}
                onStatusChange={onStatusChange(domain.id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Harmony() {
  const [domains, setDomains] = useState(HARMONY_DOMAINS)
  const [saving, setSaving] = useState(false)
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    api.getHarmony()
      .then(data => { if (data?.domains) setDomains(data.domains) })
      .catch(() => {})
  }, [])

  function handleStatusChange(domainId) {
    return async (aspect, status) => {
      setDomains(prev => prev.map(d =>
        d.id === domainId
          ? { ...d, aspects: { ...d.aspects, [aspect]: { ...d.aspects[aspect], status } } }
          : d
      ))
      setSaving(true)
      try {
        await api.updateAspectStatus(domainId, aspect, status)
      } catch {
        // Silent fail
      } finally {
        setSaving(false)
      }
    }
  }

  const counts = {
    all:    domains.length,
    red:    domains.filter(d => domainOverallStatus(d) === 'red').length,
    yellow: domains.filter(d => domainOverallStatus(d) === 'yellow').length,
    green:  domains.filter(d => domainOverallStatus(d) === 'green').length,
  }

  const filtered = filter === 'all'
    ? domains
    : domains.filter(d => domainOverallStatus(d) === filter)

  const statusColors = {
    red:    { text: '#ef4444', bg: 'rgba(239,68,68,0.08)',   border: filter === 'red'    ? '#ef4444' : '#e8ecf1' },
    yellow: { text: '#f59e0b', bg: 'rgba(245,158,11,0.08)',  border: filter === 'yellow' ? '#f59e0b' : '#e8ecf1' },
    green:  { text: '#10b981', bg: 'rgba(16,185,129,0.08)',  border: filter === 'green'  ? '#10b981' : '#e8ecf1' },
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '32px 24px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 300, color: 'var(--text-primary)', letterSpacing: '-0.02em', margin: 0, lineHeight: 1.3 }}>
            Harmony <span style={{ fontWeight: 600 }}>— Your Life Map</span>
          </h1>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: '4px 0 0' }}>13 domains. 4 aspects each. Your life in full.</p>
        </div>
        {saving && <p style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Saving...</p>}
      </div>

      {/* Status summary */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 20 }}>
        {[
          { status: 'red',    label: 'Needs attention' },
          { status: 'yellow', label: 'In & out'        },
          { status: 'green',  label: 'Embodied'        },
        ].map(({ status, label }) => {
          const sc = statusColors[status]
          return (
            <button
              key={status}
              onClick={() => setFilter(f => f === status ? 'all' : status)}
              style={{
                background: filter === status ? sc.bg : '#ffffff',
                border: `1px solid ${sc.border}`,
                borderRadius: 16, padding: '12px 16px', textAlign: 'left',
                cursor: 'pointer', transition: 'all 0.2s',
                boxShadow: filter === status ? 'none' : '0 2px 8px rgba(0,0,0,0.04)',
                fontFamily: 'inherit',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: sc.text, display: 'inline-block' }} />
                <span style={{ fontSize: 20, fontWeight: 600, color: sc.text }}>{counts[status]}</span>
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: 0 }}>{label}</p>
            </button>
          )
        })}
      </div>

      {/* Domain list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {filtered.map(domain => (
          <DomainCard
            key={domain.id}
            domain={domain}
            onStatusChange={handleStatusChange}
          />
        ))}
      </div>
    </div>
  )
}
