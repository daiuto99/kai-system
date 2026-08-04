import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { Users } from 'lucide-react'

const ADVISORS = [
  { id: 'kai', name: 'KAI', role: 'Command & operations', color: 'text-blue-400' },
  { id: 'beats', name: 'Beats', role: 'Music & creative',       color: 'text-orange-400' },
  { id: 'ember', name: 'Ember', role: 'Emotional & personal',   color: 'text-rose-400'   },
  { id: 'doc',   name: 'Doc',   role: 'Health & science',       color: 'text-green-400'  },
  { id: 'coach', name: 'Coach', role: 'Performance & mindset',  color: 'text-yellow-400' },
]

export default function Council() {
  const [active, setActive] = useState('kai')
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.getChannelHistory(active)
      .then(data => setHistory(data.messages || []))
      .catch(() => setHistory([]))
      .finally(() => setLoading(false))
  }, [active])

  const advisor = ADVISORS.find(a => a.id === active)

  return (
    <div className="flex h-full">
      {/* Advisor list */}
      <div className="w-48 flex-shrink-0 border-r kai-divider py-6 px-3">
        <p className="text-xs font-semibold uppercase tracking-widest kai-text-subtle px-2 mb-3">
          Advisors
        </p>
        <div className="space-y-0.5">
          {ADVISORS.map(a => (
            <button
              key={a.id}
              onClick={() => setActive(a.id)}
              className={`w-full text-left px-3 py-2.5 rounded-lg transition-colors
                ${active === a.id ? 'bg-kai-blue/15 text-white' : 'kai-text-secondary hover:bg-white/5 hover:text-white'}`}
            >
              <p className={`text-sm font-medium ${active === a.id ? a.color : ''}`}>{a.name}</p>
              <p className="text-xs kai-text-subtle mt-0.5">{a.role}</p>
            </button>
          ))}
        </div>
      </div>

      {/* Conversation */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-6 py-4 border-b kai-divider">
          <div className="flex items-center gap-2">
            <Users size={15} className="kai-text-subtle" />
            <p className="text-sm font-semibold">{advisor?.name}</p>
            <span className="text-xs kai-text-subtle">— {advisor?.role}</span>
          </div>
          <p className="text-xs kai-text-subtle mt-1">
            Conversations happen in Buzz (Nostr advisor channels). History shown here.
          </p>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading ? (
            <div className="text-center kai-text-subtle text-sm py-12">Loading history...</div>
          ) : history.length === 0 ? (
            <div className="text-center kai-text-subtle py-12">
              <p className="text-sm">No conversation history yet.</p>
              <p className="text-xs mt-1">Open #{active} in Buzz to start.</p>
            </div>
          ) : (
            <div className="space-y-4 max-w-2xl">
              {history.map((msg, i) => (
                <div
                  key={i}
                  className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div className={`max-w-lg rounded-xl px-4 py-3 text-sm leading-relaxed
                    ${msg.role === 'user'
                      ? 'bg-kai-blue/20 text-white'
                      : 'bg-kai-dark-card2 kai-text-secondary border border-kai-dark-border'
                    }`}
                  >
                    {msg.content}
                    {msg.ts && (
                      <p className="text-xs kai-text-subtle mt-1.5">
                        {new Date(parseFloat(msg.ts) * 1000).toLocaleTimeString([], {
                          hour: '2-digit', minute: '2-digit'
                        })}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
