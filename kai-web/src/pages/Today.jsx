import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { HARMONY_DOMAINS, domainOverallStatus, domainsNeedingAttention } from '../lib/harmonyData'
import { StatusDot } from '../components/StatusBadge'
import { RefreshCw, ArrowRight } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function formatDate() {
  return new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric'
  })
}

function priorityColor(priority) {
  if (priority >= 4 || priority === 1) return 'text-kai-red'
  if (priority === 2) return 'text-kai-yellow'
  return 'text-white/50'
}

export default function Today() {
  const [focus, setFocus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [harmony, setHarmony] = useState(HARMONY_DOMAINS)
  const navigate = useNavigate()

  async function loadFocus() {
    setLoading(true)
    try {
      const data = await api.getFocusBrief()
      setFocus(data)
    } catch {
      setFocus(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadFocus()
    api.getHarmony()
      .then(data => { if (data?.domains) setHarmony(data.domains) })
      .catch(() => {})
  }, [])

  const atRisk = domainsNeedingAttention(harmony).slice(0, 3)

  return (
    <div className="max-w-3xl mx-auto px-8 py-10">
      {/* Header */}
      <div className="mb-10">
        <p className="kai-text-subtle text-sm mb-1">{formatDate()}</p>
        <h1 className="text-3xl font-semibold tracking-tight">
          {greeting()}, Leo.
        </h1>
      </div>

      <div className="space-y-6">
        {/* Focus Stack */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-semibold uppercase tracking-widest kai-text-subtle">
              Focus Stack
            </h2>
            <button onClick={loadFocus} className="btn-ghost flex items-center gap-1.5 text-xs">
              <RefreshCw size={12} />
              Refresh
            </button>
          </div>

          <div className="kai-card divide-y kai-divider">
            {loading ? (
              <div className="px-5 py-8 text-center kai-text-subtle text-sm">
                Loading focus stack...
              </div>
            ) : focus?.top3?.length ? (
              <>
                {focus.top3.map((task, i) => (
                  <div key={task.id || i} className="px-5 py-3.5 flex items-start gap-4">
                    <span className="text-xs kai-text-subtle font-mono mt-0.5 w-4 flex-shrink-0">
                      {i + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium leading-snug">{task.content}</p>
                      {task.due && (
                        <p className="text-xs kai-text-subtle mt-0.5">
                          Due {task.due}
                        </p>
                      )}
                    </div>
                    <span className={`text-xs font-mono mt-0.5 ${priorityColor(task.priority)}`}>
                      P{task.priority}
                    </span>
                  </div>
                ))}
                {focus.next5?.length > 0 && (
                  <div className="px-5 py-3">
                    <p className="text-xs kai-text-subtle mb-2 font-medium">Next up</p>
                    <div className="space-y-1.5">
                      {focus.next5.map((task, i) => (
                        <div key={task.id || i} className="flex items-center gap-3">
                          <span className="text-xs kai-text-subtle font-mono w-4">{i + 4}</span>
                          <p className="text-xs kai-text-secondary truncate">{task.content}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="px-5 py-8 text-center">
                <p className="text-sm kai-text-subtle">Nothing in the queue.</p>
                <p className="text-xs kai-text-subtle mt-1">Calendar is clear today.</p>
              </div>
            )}
          </div>
        </section>

        {/* Harmony Pulse */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-semibold uppercase tracking-widest kai-text-subtle">
              Harmony Pulse
            </h2>
            <button
              onClick={() => navigate('/harmony')}
              className="btn-ghost flex items-center gap-1 text-xs"
            >
              Full view <ArrowRight size={12} />
            </button>
          </div>

          {atRisk.length === 0 ? (
            <div className="kai-card px-5 py-4">
              <div className="flex items-center gap-3">
                <StatusDot status="green" size={10} />
                <p className="text-sm">Harmony is stable. All domains are balanced.</p>
              </div>
            </div>
          ) : (
            <div className="kai-card divide-y kai-divider">
              {atRisk.map(domain => {
                const overall = domainOverallStatus(domain)
                const redAspects = Object.entries(domain.aspects)
                  .filter(([, a]) => a.status === 'red')
                  .map(([k]) => k)
                const yellowAspects = Object.entries(domain.aspects)
                  .filter(([, a]) => a.status === 'yellow')
                  .map(([k]) => k)
                return (
                  <div key={domain.id} className="px-5 py-3.5 flex items-center gap-4">
                    <StatusDot status={overall} size={8} />
                    <div className="flex-1">
                      <p className="text-sm font-medium">{domain.icon} {domain.name}</p>
                      {redAspects.length > 0 && (
                        <p className="text-xs text-kai-red/80 mt-0.5 capitalize">
                          {redAspects.join(', ')} needs attention
                        </p>
                      )}
                      {yellowAspects.length > 0 && redAspects.length === 0 && (
                        <p className="text-xs text-kai-yellow/80 mt-0.5 capitalize">
                          {yellowAspects.join(', ')} in & out
                        </p>
                      )}
                    </div>
                    <button
                      onClick={() => navigate('/harmony')}
                      className="text-xs kai-text-subtle hover:text-white transition-colors"
                    >
                      <ArrowRight size={14} />
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
