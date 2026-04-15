import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { Sparkles, RefreshCw } from 'lucide-react'

const CATEGORY_COLORS = {
  Insight:     'text-blue-400   bg-blue-400/10',
  Truth:       'text-purple-400 bg-purple-400/10',
  Pattern:     'text-teal-400   bg-teal-400/10',
  Realization: 'text-orange-400 bg-orange-400/10',
  Question:    'text-yellow-400 bg-yellow-400/10',
}

export default function Insights() {
  const [insights, setInsights] = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('all')

  async function load() {
    setLoading(true)
    try {
      const data = await api.getInsights()
      setInsights(data.insights || [])
    } catch {
      setInsights([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const categories = ['all', ...new Set(insights.map(i => i.category))]
  const filtered = filter === 'all' ? insights : insights.filter(i => i.category === filter)

  return (
    <div className="max-w-2xl mx-auto px-8 py-10">
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Sparkles size={20} className="kai-text-subtle" />
            Insights
          </h1>
          <p className="kai-text-subtle text-sm mt-1">
            Extracted from Ember sessions. Patterns over time.
          </p>
        </div>
        <button onClick={load} className="btn-ghost flex items-center gap-1.5 text-xs">
          <RefreshCw size={12} />
        </button>
      </div>

      {/* Category filter */}
      {categories.length > 1 && (
        <div className="flex gap-1.5 mb-6 flex-wrap">
          {categories.map(c => (
            <button
              key={c}
              onClick={() => setFilter(c)}
              className={`text-xs px-3 py-1 rounded-full border transition-colors capitalize
                ${filter === c
                  ? 'border-kai-blue text-kai-blue bg-kai-blue/10'
                  : 'border-white/10 kai-text-subtle hover:border-white/20'}`}
            >
              {c}
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="kai-card px-5 py-12 text-center kai-text-subtle text-sm">
          Loading insights...
        </div>
      ) : filtered.length === 0 ? (
        <div className="kai-card px-5 py-12 text-center">
          <Sparkles size={28} className="kai-text-subtle mx-auto mb-3" />
          <p className="text-sm kai-text-subtle">No insights yet.</p>
          <p className="text-xs kai-text-subtle mt-1">
            They're extracted from your Ember sessions automatically.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((insight, i) => {
            const catStyle = CATEGORY_COLORS[insight.category] || 'text-white/50 bg-white/5'
            return (
              <div key={i} className="kai-card px-5 py-4">
                <div className="flex items-start gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${catStyle}`}>
                        {insight.category}
                      </span>
                      {insight.date && (
                        <span className="text-xs kai-text-subtle">{insight.date}</span>
                      )}
                    </div>
                    <p className="text-sm leading-relaxed">{insight.content}</p>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
