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
    <div className="py-3 border-b kai-divider last:border-0">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <p className="text-sm font-medium capitalize">{ASPECT_LABELS[aspect]}</p>
            <p className="text-xs kai-text-subtle hidden sm:block">
              — {ASPECT_DESCRIPTIONS[aspect]}
            </p>
          </div>
          <div className="space-y-1">
            {data.statements.map((s, i) => (
              <p key={i} className="text-xs kai-text-secondary leading-relaxed">
                {s}
              </p>
            ))}
          </div>
        </div>
        <div className="flex-shrink-0 pt-0.5">
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
    <div className="kai-card overflow-hidden">
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full px-5 py-4 flex items-center gap-4 text-left hover:bg-white/5 transition-colors"
      >
        <StatusDot status={overall} size={9} />
        <span className="text-lg">{domain.icon}</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">{domain.name}</p>
          <div className="flex gap-1 mt-1">
            {Object.values(domain.aspects).map((a, i) => (
              <StatusDot key={i} status={a.status} size={5} />
            ))}
          </div>
        </div>
        <StatusBadge status={overall} />
        {expanded
          ? <ChevronDown size={15} className="kai-text-subtle flex-shrink-0" />
          : <ChevronRight size={15} className="kai-text-subtle flex-shrink-0" />
        }
      </button>

      {expanded && (
        <div className="px-5 pb-4 border-t kai-divider">
          <div className="pt-3">
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
      // Optimistic update
      setDomains(prev => prev.map(d =>
        d.id === domainId
          ? { ...d, aspects: { ...d.aspects, [aspect]: { ...d.aspects[aspect], status } } }
          : d
      ))
      setSaving(true)
      try {
        await api.updateAspectStatus(domainId, aspect, status)
      } catch {
        // Silent fail — local state kept
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

  return (
    <div className="max-w-3xl mx-auto px-8 py-10">
      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Harmony</h1>
          <p className="kai-text-subtle text-sm mt-1">13 domains. 4 aspects each. Your life in full.</p>
        </div>
        {saving && <p className="text-xs kai-text-subtle">Saving...</p>}
      </div>

      {/* Status summary */}
      <div className="grid grid-cols-3 gap-3 mb-6">
        {[
          { status: 'red',    label: 'Needs attention' },
          { status: 'yellow', label: 'In & out'        },
          { status: 'green',  label: 'Embodied'        },
        ].map(({ status, label }) => (
          <button
            key={status}
            onClick={() => setFilter(f => f === status ? 'all' : status)}
            className={`kai-card px-4 py-3 text-left transition-all
              ${filter === status ? 'ring-1 ring-kai-blue' : 'hover:bg-white/5'}`}
          >
            <div className="flex items-center gap-2 mb-1">
              <StatusDot status={status} size={7} />
              <span className={`text-lg font-semibold
                ${status === 'red' ? 'text-kai-red' : status === 'yellow' ? 'text-kai-yellow' : 'text-kai-green'}`}>
                {counts[status]}
              </span>
            </div>
            <p className="text-xs kai-text-subtle">{label}</p>
          </button>
        ))}
      </div>

      {/* Domain list */}
      <div className="space-y-2">
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
