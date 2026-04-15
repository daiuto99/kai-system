import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Check, ChevronRight, Send, Clock } from 'lucide-react'
import { api } from '../lib/api'
import { ADVISORS, getAdvisor } from '../lib/advisors'

// ── Helpers ────────────────────────────────────────────────────────────────

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(parseFloat(ts) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// ── Widget shell ───────────────────────────────────────────────────────────

function Widget({ title, action, children, className = '', noPad = false }) {
  return (
    <div className={`bg-white rounded-2xl border border-kai-light-border shadow-widget flex flex-col overflow-hidden ${className}`}>
      {title && (
        <div className="flex items-center justify-between px-4 pt-4 pb-2 flex-shrink-0">
          <span className="text-[11px] font-semibold uppercase tracking-widest text-kai-light-subtle">
            {title}
          </span>
          {action}
        </div>
      )}
      <div className={`flex-1 overflow-hidden ${noPad ? '' : 'px-4 pb-4'}`}>
        {children}
      </div>
    </div>
  )
}

// ── Projects & Status ──────────────────────────────────────────────────────

const PROJECTS = [
  { name: 'KAI', status: 'green', next: 'Phase 2 — Command Center UI' },
  { name: 'Encore', status: 'yellow', next: 'Active build' },
  { name: 'LaunchBox', status: 'green', next: 'Podcast workflow automation' },
  { name: 'Soul Collective', status: 'yellow', next: 'Early stage — needs space' },
  { name: 'Revolt Group', status: 'yellow', next: 'Messaging + website overhaul' },
]

const STATUS_DOT = { green: 'bg-kai-green', yellow: 'bg-kai-yellow', red: 'bg-kai-red' }

function ProjectsWidget({ className }) {
  return (
    <Widget title="Projects" className={className}>
      <div className="space-y-2.5">
        {PROJECTS.map(p => (
          <div key={p.name} className="flex items-start gap-2.5 group cursor-pointer">
            <span className={`mt-1.5 w-2 h-2 rounded-full flex-shrink-0 ${STATUS_DOT[p.status]}`} />
            <div className="min-w-0">
              <p className="text-sm font-medium text-kai-light-text leading-tight">{p.name}</p>
              <p className="text-xs text-kai-light-muted leading-snug mt-0.5 truncate">{p.next}</p>
            </div>
            <ChevronRight size={14} className="ml-auto mt-1 text-kai-light-subtle opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" />
          </div>
        ))}
      </div>
    </Widget>
  )
}

// ── Habits / Harmony ───────────────────────────────────────────────────────

function HabitsHarmonyWidget({ className }) {
  const [harmony, setHarmony] = useState(null)

  useEffect(() => {
    api.getHarmony?.()
      .then(setHarmony)
      .catch(() => {})
  }, [])

  const counts = harmony
    ? Object.values(harmony).reduce((acc, d) => {
        Object.values(d.aspects || {}).forEach(v => {
          acc[v] = (acc[v] || 0) + 1
        })
        return acc
      }, { G: 0, Y: 0, R: 0 })
    : null

  return (
    <Widget title="Harmony" className={className}>
      <div className="flex flex-col items-center justify-center h-full py-2 gap-3">
        <span className="text-4xl text-kai-light-subtle select-none" style={{ fontFamily: 'serif' }}>和</span>
        {counts ? (
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-kai-green" />
              <span className="text-xs text-kai-light-muted">{counts.G || 0}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-kai-yellow" />
              <span className="text-xs text-kai-light-muted">{counts.Y || 0}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-kai-red" />
              <span className="text-xs text-kai-light-muted">{counts.R || 0}</span>
            </div>
          </div>
        ) : (
          <p className="text-xs text-kai-light-subtle">Loading…</p>
        )}
      </div>
    </Widget>
  )
}

// ── Today's Play ───────────────────────────────────────────────────────────

function TodayPlayWidget({ className }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [checked, setChecked] = useState({})

  useEffect(() => {
    api.getTodayFocus?.()
      .then(data => setTasks(data.tasks || data.items || []))
      .catch(() => setTasks([]))
      .finally(() => setLoading(false))
  }, [])

  return (
    <Widget title="Today's Play" className={className}>
      <div className="flex flex-col h-full">
        <div className="flex-1 overflow-y-auto space-y-2 pr-1">
          {loading ? (
            <p className="text-xs text-kai-light-subtle pt-2">Loading…</p>
          ) : tasks.length === 0 ? (
            <p className="text-xs text-kai-light-subtle pt-2">No tasks for today. Plan your day with KAI →</p>
          ) : (
            tasks.map((t, i) => (
              <div key={i} className="flex items-start gap-2.5 group">
                <button
                  onClick={() => setChecked(c => ({ ...c, [i]: !c[i] }))}
                  className={`mt-0.5 w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center transition-colors ${
                    checked[i]
                      ? 'bg-kai-terra border-kai-terra'
                      : 'border-kai-light-border hover:border-kai-terra'
                  }`}
                >
                  {checked[i] && <Check size={10} className="text-white" strokeWidth={3} />}
                </button>
                <span className={`text-sm leading-snug ${checked[i] ? 'line-through text-kai-light-subtle' : 'text-kai-light-text'}`}>
                  {t.content || t.title || t}
                </span>
              </div>
            ))
          )}
        </div>
        {/* Up next — calendar placeholder */}
        <div className="flex-shrink-0 mt-3 pt-3 border-t border-kai-light-divider flex items-center gap-2">
          <Clock size={12} className="text-kai-light-subtle flex-shrink-0" />
          <span className="text-xs text-kai-light-subtle italic">Calendar sync coming soon</span>
        </div>
      </div>
    </Widget>
  )
}

// ── Check-In ───────────────────────────────────────────────────────────────

function CheckInWidget({ className }) {
  const [intent, setIntent] = useState('')
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/checkin')
      .then(r => r.json())
      .then(d => {
        const today = new Date().toISOString().slice(0, 10)
        if (d.date === today) setIntent(d.intent || '')
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function save() {
    fetch('/api/checkin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intent }),
    })
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 2000) })
      .catch(() => {})
  }

  return (
    <Widget title="Today's Intent" className={className}>
      <div className="flex flex-col h-full gap-2">
        <textarea
          value={intent}
          onChange={e => setIntent(e.target.value)}
          onBlur={save}
          placeholder="What do you want to get done today?"
          className="flex-1 w-full text-sm text-kai-light-text placeholder:text-kai-light-subtle resize-none outline-none leading-relaxed bg-transparent"
          style={{ fontSize: '16px', minHeight: '80px' }}
        />
        <div className="flex items-center justify-between flex-shrink-0">
          <span className="text-[11px] text-kai-light-subtle">Auto-saves on blur</span>
          {saved && <span className="text-[11px] text-kai-green">Saved ✓</span>}
        </div>
      </div>
    </Widget>
  )
}

// ── Chat Widget ────────────────────────────────────────────────────────────

function ChatWidget({ className }) {
  const [advisor, setAdvisor] = useState(getAdvisor('kai'))
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [showAdvisors, setShowAdvisors] = useState(false)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    api.getChannelHistory(advisor.channel)
      .then(data => setMessages(data.messages || []))
      .catch(() => {})
  }, [advisor.channel])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinking])

  async function send() {
    const text = input.trim()
    if (!text || thinking) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: text, ts: String(Date.now() / 1000) }])
    setThinking(true)
    try {
      const data = await api.sendMessage(text, advisor.channel)
      setMessages(prev => [...prev, { role: 'assistant', content: data.reply || data.message || '', ts: String(Date.now() / 1000) }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Something went wrong.', error: true, ts: String(Date.now() / 1000) }])
    } finally {
      setThinking(false)
      inputRef.current?.focus()
    }
  }

  return (
    <div className={`bg-white rounded-2xl border border-kai-light-border shadow-widget flex flex-col overflow-hidden ${className}`}>
      {/* Header */}
      <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 border-b border-kai-light-border">
        <button onClick={() => setShowAdvisors(v => !v)} className="flex items-center gap-2 group">
          <span className="text-lg">{advisor.emoji}</span>
          <div className="text-left">
            <p className="text-sm font-semibold text-kai-light-text leading-tight">{advisor.name}</p>
            <p className="text-[11px] text-kai-light-subtle leading-none mt-0.5">{advisor.role}</p>
          </div>
        </button>
        <div className="flex items-center gap-1">
          {ADVISORS.map(a => (
            <button
              key={a.id}
              onClick={() => { setAdvisor(a); setShowAdvisors(false) }}
              className={`w-7 h-7 rounded-full flex items-center justify-center text-sm transition-all ${
                advisor.id === a.id ? 'bg-kai-terra-dim ring-1 ring-kai-terra/30' : 'hover:bg-kai-light-bg'
              }`}
              title={a.name}
            >
              {a.emoji}
            </button>
          ))}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !thinking && (
          <div className="flex flex-col items-center justify-center h-full text-center py-6">
            <span className="text-3xl mb-2">{advisor.emoji}</span>
            <p className="text-xs text-kai-light-subtle max-w-[200px] leading-relaxed">{advisor.intro}</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            {msg.role !== 'user' && (
              <span className="text-base mr-1.5 mt-0.5 flex-shrink-0">{advisor.emoji}</span>
            )}
            <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'bg-kai-terra text-white rounded-tr-sm'
                : 'bg-kai-light-bg text-kai-light-text border border-kai-light-border rounded-tl-sm'
            }`}>
              <p className="whitespace-pre-wrap">{msg.content}</p>
              {msg.ts && <p className="text-[10px] opacity-40 mt-1 text-right">{formatTime(msg.ts)}</p>}
            </div>
          </div>
        ))}
        {thinking && (
          <div className="flex items-end gap-1.5">
            <span className="text-base">{advisor.emoji}</span>
            <div className="bg-kai-light-bg border border-kai-light-border rounded-2xl rounded-tl-sm px-3 py-2">
              <div className="flex gap-1 items-center h-4">
                <span className="w-1.5 h-1.5 rounded-full bg-kai-light-subtle animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-kai-light-subtle animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-kai-light-subtle animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 px-3 py-3 border-t border-kai-light-border">
        <div className="flex items-end gap-2 bg-kai-light-bg rounded-xl px-3 py-2 border border-kai-light-border">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder={`Message ${advisor.name}…`}
            rows={1}
            className="flex-1 bg-transparent resize-none outline-none text-kai-light-text placeholder:text-kai-light-subtle leading-relaxed overflow-y-auto"
            style={{ fontSize: '16px', minHeight: '20px', maxHeight: '96px' }}
            onInput={e => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 96) + 'px' }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || thinking}
            className="flex-shrink-0 w-7 h-7 rounded-full bg-kai-terra flex items-center justify-center transition-opacity disabled:opacity-30"
          >
            <Send size={13} className="text-white" />
          </button>
        </div>
      </div>
    </div>
  )
}

// ── The Lot ────────────────────────────────────────────────────────────────

function LotWidget() {
  const [items, setItems] = useState([])

  useEffect(() => {
    api.getParkingLot?.()
      .then(data => setItems(data.items || []))
      .catch(() => {})
  }, [])

  return (
    <div className="bg-white rounded-2xl border border-kai-light-border shadow-widget overflow-hidden">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-kai-light-subtle">The Lot</span>
        <span className="text-xs text-kai-light-subtle">{items.length} items</span>
      </div>
      <div className="px-4 pb-4">
        {items.length === 0 ? (
          <p className="text-xs text-kai-light-subtle py-2">Nothing in The Lot</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {items.slice(0, 8).map((item, i) => (
              <div key={i} className="bg-kai-light-bg rounded-lg px-3 py-2 border border-kai-light-border">
                <p className="text-xs font-medium text-kai-light-text truncate">{item.title || item.content?.slice(0, 40) || 'Untitled'}</p>
                {item.category && (
                  <p className="text-[10px] text-kai-light-subtle mt-0.5">{item.category}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function Today() {
  return (
    <div className="h-full bg-kai-light-bg overflow-y-auto md:overflow-hidden">
      <div className="md:h-full flex flex-col px-4 pt-5 pb-4 gap-4">

        {/* Greeting */}
        <div className="flex-shrink-0 px-1">
          <h1 className="text-xl text-kai-light-text">
            {greeting()}, <strong>Leo</strong>
          </h1>
        </div>

        {/* Desktop grid */}
        <div className="hidden md:grid flex-1 min-h-0" style={{
          gridTemplateColumns: '1fr 0.7fr 1.1fr',
          gridTemplateRows: '1fr 1fr',
          gridTemplateAreas: '"projects harmony chat" "todayplay checkin chat"',
          gap: '12px',
        }}>
          <ProjectsWidget style={{ gridArea: 'projects' }} className="[grid-area:projects]" />
          <HabitsHarmonyWidget className="[grid-area:harmony]" style={{ gridArea: 'harmony' }} />
          <ChatWidget className="[grid-area:chat] row-span-2" style={{ gridArea: 'chat' }} />
          <TodayPlayWidget className="[grid-area:todayplay]" style={{ gridArea: 'todayplay' }} />
          <CheckInWidget className="[grid-area:checkin]" style={{ gridArea: 'checkin' }} />
        </div>

        {/* Mobile stack */}
        <div className="md:hidden flex flex-col gap-3 pb-24">
          <CheckInWidget className="min-h-[120px]" />
          <TodayPlayWidget className="min-h-[200px]" />
          <HabitsHarmonyWidget className="min-h-[140px]" />
          <ProjectsWidget className="min-h-[200px]" />
          <ChatWidget className="min-h-[400px]" />
        </div>

        {/* The Lot — full width below grid */}
        <div className="flex-shrink-0 hidden md:block">
          <LotWidget />
        </div>

      </div>
    </div>
  )
}
