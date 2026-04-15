import { useState, useEffect, useRef } from 'react'
import { Plus, Check, Send, Clock, ExternalLink, FileText, Link, Image, Lightbulb, Film, Archive } from 'lucide-react'
import { api } from '../lib/api'
import { ADVISORS, getAdvisor } from '../lib/advisors'

// ── Helpers ────────────────────────────────────────────────────────────────

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function fmtTime(ts) {
  if (!ts) return ''
  return new Date(parseFloat(ts) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// ── Section header ─────────────────────────────────────────────────────────

function SectionHeader({ title, action }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
      <span className="section-title">{title}</span>
      {action}
    </div>
  )
}

// ── Projects ───────────────────────────────────────────────────────────────

const PROJECTS = [
  { name: 'KAI', status: 'green', next: 'Phase 2 — Command Center UI' },
  { name: 'Encore', status: 'yellow', next: 'Active build' },
  { name: 'LaunchBox', status: 'green', next: 'Podcast workflow automation' },
  { name: 'Soul Collective', status: 'yellow', next: 'Early stage — needs space' },
  { name: 'Revolt Group', status: 'yellow', next: 'Messaging + website overhaul' },
]

const SDOT = { green: '#10b981', yellow: '#f59e0b', red: '#ef4444' }

function ProjectsWidget() {
  return (
    <div className="kai-inner" style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <SectionHeader title="Projects & Status" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', overflowY: 'auto' }}>
        {PROJECTS.map(p => (
          <div key={p.name} className="list-item" style={{ gap: '10px' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: SDOT[p.status], flexShrink: 0 }} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: '13px', fontWeight: 500, color: '#1f2937', lineHeight: 1.3 }}>{p.name}</div>
              <div style={{ fontSize: '11px', color: '#6b7280', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.next}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Harmony ────────────────────────────────────────────────────────────────

function HarmonyWidget() {
  const [counts, setCounts] = useState(null)

  useEffect(() => {
    api.getHarmony?.()
      .then(data => {
        const c = { G: 0, Y: 0, R: 0 }
        Object.values(data).forEach(d => {
          Object.values(d.aspects || {}).forEach(v => { c[v] = (c[v] || 0) + 1 })
        })
        setCounts(c)
      })
      .catch(() => {})
  }, [])

  return (
    <div className="kai-inner" style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
      <SectionHeader title="Harmony" />
      <div style={{ fontSize: 40, color: '#9ca3af', fontFamily: 'serif', lineHeight: 1, userSelect: 'none' }}>和</div>
      {counts ? (
        <div style={{ display: 'flex', gap: 16 }}>
          {[['G','#10b981'],['Y','#f59e0b'],['R','#ef4444']].map(([k,c]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: c }} />
              <span style={{ fontSize: 12, color: '#6b7280' }}>{counts[k] || 0}</span>
            </div>
          ))}
        </div>
      ) : (
        <span style={{ fontSize: 12, color: '#9ca3af' }}>Loading…</span>
      )}
    </div>
  )
}

// ── Today's Play ───────────────────────────────────────────────────────────

function TodayPlayWidget() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [checked, setChecked] = useState({})

  useEffect(() => {
    api.getTodayFocus?.()
      .then(d => setTasks(d.tasks || d.items || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="kai-inner" style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <SectionHeader title="Today's Play" action={<button className="add-btn"><Plus size={14} /></button>} />
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {loading ? (
          <p style={{ fontSize: 12, color: '#9ca3af', padding: '8px 0' }}>Loading…</p>
        ) : tasks.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '24px 16px', color: '#9ca3af' }}>
            <div style={{ fontSize: 28, marginBottom: 8, opacity: 0.5 }}>📋</div>
            <p style={{ fontSize: 13 }}>No tasks for today.<br/>Use KAI to plan your day →</p>
          </div>
        ) : (
          tasks.map((t, i) => (
            <div
              key={i}
              className="list-item"
              onClick={() => setChecked(c => ({ ...c, [i]: !c[i] }))}
              style={{ opacity: checked[i] ? 0.55 : 1 }}
            >
              <div style={{
                width: 18, height: 18, borderRadius: 5, border: `2px solid ${checked[i] ? '#c2410c' : '#9ca3af'}`,
                backgroundColor: checked[i] ? '#c2410c' : 'transparent', flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.2s',
              }}>
                {checked[i] && <Check size={10} color="white" strokeWidth={3} />}
              </div>
              <span style={{ flex: 1, fontSize: 13, textDecoration: checked[i] ? 'line-through' : 'none', color: checked[i] ? '#9ca3af' : '#1f2937' }}>
                {t.content || t.title || String(t)}
              </span>
              {(t.priority === 1 || t.priority === 'high') && (
                <span style={{ fontSize: 10, padding: '3px 6px', borderRadius: 5, background: 'rgba(239,68,68,0.1)', color: '#ef4444', fontWeight: 600, textTransform: 'uppercase' }}>High</span>
              )}
            </div>
          ))
        )}
      </div>
      {/* Up next */}
      <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid #e8ecf1', display: 'flex', alignItems: 'center', gap: 6 }}>
        <Clock size={11} color="#9ca3af" />
        <span style={{ fontSize: 11, color: '#9ca3af', fontStyle: 'italic' }}>Calendar sync coming soon</span>
      </div>
    </div>
  )
}

// ── Check-In ───────────────────────────────────────────────────────────────

function CheckInWidget() {
  const [intent, setIntent] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetch('/api/checkin')
      .then(r => r.json())
      .then(d => {
        if (d.date === new Date().toISOString().slice(0, 10)) setIntent(d.intent || '')
      })
      .catch(() => {})
  }, [])

  function save() {
    fetch('/api/checkin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intent }),
    }).then(() => { setSaved(true); setTimeout(() => setSaved(false), 2000) })
  }

  return (
    <div className="kai-inner" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
      <SectionHeader
        title="Today's Intent"
        action={saved ? <span style={{ fontSize: 11, color: '#10b981', fontWeight: 600 }}>Saved ✓</span> : null}
      />
      <textarea
        value={intent}
        onChange={e => setIntent(e.target.value)}
        onBlur={save}
        placeholder="What do you want to get done today?"
        style={{
          flex: 1, width: '100%', fontSize: 13, color: '#1f2937', lineHeight: 1.6,
          background: 'transparent', border: 'none', outline: 'none', resize: 'none',
          fontFamily: 'inherit', minHeight: 80, placeholder: { color: '#9ca3af' },
        }}
      />
    </div>
  )
}

// ── Chat Widget ────────────────────────────────────────────────────────────

function ChatWidget() {
  const [advisor, setAdvisor] = useState(getAdvisor('kai'))
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    api.getChannelHistory(advisor.channel)
      .then(d => setMessages(d.messages || []))
      .catch(() => {})
  }, [advisor.channel])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinking])

  async function send() {
    const text = input.trim()
    if (!text || thinking) return
    setInput('')
    setMessages(p => [...p, { role: 'user', content: text, ts: String(Date.now() / 1000) }])
    setThinking(true)
    try {
      const d = await api.sendMessage(text, advisor.channel)
      setMessages(p => [...p, { role: 'assistant', content: d.reply || d.message || '', ts: String(Date.now() / 1000) }])
    } catch {
      setMessages(p => [...p, { role: 'assistant', content: 'Something went wrong.', error: true, ts: String(Date.now() / 1000) }])
    } finally {
      setThinking(false)
      inputRef.current?.focus()
    }
  }

  return (
    <div style={{
      background: '#ffffff', borderRadius: 20, boxShadow: '0 4px 20px rgba(0,0,0,0.06)',
      border: '1px solid rgba(0,0,0,0.04)', display: 'flex', flexDirection: 'column',
      overflow: 'hidden', height: '100%',
    }}>
      {/* Advisor row */}
      <div style={{ flexShrink: 0, padding: '14px 20px 10px', borderBottom: '1px solid #e8ecf1' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {ADVISORS.map(a => (
            <button
              key={a.id}
              onClick={() => setAdvisor(a)}
              style={{
                display: 'flex', alignItems: 'center', gap: 5, padding: '5px 10px',
                borderRadius: 8, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 500,
                transition: 'all 0.2s',
                background: advisor.id === a.id ? '#fff7ed' : 'transparent',
                color: advisor.id === a.id ? '#c2410c' : '#6b7280',
              }}
            >
              <span style={{ fontSize: 14 }}>{a.emoji}</span>
              {a.name}
            </button>
          ))}
        </div>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 8, background: '#fafbfc' }}>
        {messages.length === 0 && !thinking && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 10, color: '#9ca3af' }}>
            <span style={{ fontSize: 32 }}>{advisor.emoji}</span>
            <p style={{ fontSize: 13, textAlign: 'center', maxWidth: 220, lineHeight: 1.5 }}>{advisor.intro}</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
            {msg.role !== 'user' && <span style={{ fontSize: 14, marginRight: 6, marginTop: 2, flexShrink: 0 }}>{advisor.emoji}</span>}
            <div style={{
              maxWidth: '80%', padding: '8px 12px', borderRadius: 10, fontSize: 13, lineHeight: 1.4,
              background: msg.role === 'user' ? '#fff7ed' : '#ffffff',
              color: '#1f2937',
              border: msg.role === 'user' ? 'none' : '1px solid #e8ecf1',
              alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
              marginLeft: msg.role === 'user' ? 'auto' : 0,
            }}>
              <p style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{msg.content}</p>
              {msg.ts && <p style={{ fontSize: 10, opacity: 0.4, marginTop: 4, textAlign: 'right', marginBottom: 0 }}>{fmtTime(msg.ts)}</p>}
            </div>
          </div>
        ))}
        {thinking && (
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6 }}>
            <span style={{ fontSize: 14 }}>{advisor.emoji}</span>
            <div style={{ background: '#ffffff', border: '1px solid #e8ecf1', borderRadius: 10, padding: '8px 12px' }}>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center', height: 16 }}>
                {[0, 150, 300].map(d => (
                  <span key={d} style={{ width: 6, height: 6, borderRadius: '50%', background: '#9ca3af', display: 'inline-block', animation: `bounce 1s ${d}ms infinite` }} />
                ))}
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div style={{ flexShrink: 0, padding: '12px 16px', borderTop: '1px solid #e8ecf1', display: 'flex', gap: 10 }}>
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder={`Ask ${advisor.name} anything…`}
          style={{
            flex: 1, padding: '10px 14px', borderRadius: 10, border: '1px solid #e8ecf1',
            background: '#fafbfc', color: '#1f2937', fontSize: 13, fontFamily: 'inherit', outline: 'none',
          }}
          onFocus={e => e.target.style.borderColor = '#c2410c'}
          onBlur={e => e.target.style.borderColor = '#e8ecf1'}
        />
        <button
          onClick={send}
          disabled={!input.trim() || thinking}
          className="btn-primary"
          style={{ padding: '10px 16px', opacity: (!input.trim() || thinking) ? 0.4 : 1 }}
        >
          Send
        </button>
      </div>
    </div>
  )
}

// ── The Lot (thumbnail grid) ───────────────────────────────────────────────

const CAT_ICON = {
  'Links':     { icon: Link,      color: '#3b82f6', bg: '#eff6ff' },
  'Notes':     { icon: FileText,  color: '#8b5cf6', bg: '#f5f3ff' },
  'Images':    { icon: Image,     color: '#ec4899', bg: '#fdf2f8' },
  'Ideas':     { icon: Lightbulb, color: '#f59e0b', bg: '#fffbeb' },
  'Videos':    { icon: Film,      color: '#10b981', bg: '#ecfdf5' },
  'Documents': { icon: FileText,  color: '#6b7280', bg: '#f9fafb' },
}

function LotThumbnail({ item }) {
  const cat = item.category || 'Notes'
  const { icon: Icon, color, bg } = CAT_ICON[cat] || CAT_ICON['Notes']
  const isUrl = item.url || (item.content && item.content.startsWith('http'))
  const url = item.url || (isUrl ? item.content : null)

  return (
    <div style={{
      background: '#ffffff', borderRadius: 12, border: '1px solid #e8ecf1',
      overflow: 'hidden', cursor: 'pointer', transition: 'all 0.2s',
    }}
      onMouseEnter={e => { e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.08)'; e.currentTarget.style.transform = 'translateY(-2px)' }}
      onMouseLeave={e => { e.currentTarget.style.boxShadow = 'none'; e.currentTarget.style.transform = 'none' }}
    >
      {/* Thumbnail */}
      <div style={{ height: 72, background: bg, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
        {item.thumbnail ? (
          <img src={item.thumbnail} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <Icon size={24} color={color} strokeWidth={1.5} />
        )}
        {url && (
          <a href={url} target="_blank" rel="noopener noreferrer"
            onClick={e => e.stopPropagation()}
            style={{ position: 'absolute', top: 6, right: 6, background: 'rgba(255,255,255,0.9)', borderRadius: 6, padding: '3px 5px', display: 'flex' }}>
            <ExternalLink size={11} color="#6b7280" />
          </a>
        )}
      </div>
      {/* Label */}
      <div style={{ padding: '8px 10px' }}>
        <p style={{ fontSize: 12, fontWeight: 500, color: '#1f2937', margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', lineHeight: 1.3 }}>
          {item.title || item.content?.slice(0, 35) || 'Untitled'}
        </p>
        <p style={{ fontSize: 10, color: '#9ca3af', margin: '2px 0 0', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 500 }}>
          {cat}
        </p>
      </div>
    </div>
  )
}

function LotWidget() {
  const [items, setItems] = useState([])

  useEffect(() => {
    api.getParkingLot?.()
      .then(d => setItems(d.items || []))
      .catch(() => {})
  }, [])

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 4fr', gap: 12 }}>
      {/* Drop zone */}
      <div style={{
        background: '#fafbfc', padding: 20, display: 'flex', alignItems: 'center',
        justifyContent: 'center', border: '2px dashed #d1d5db', borderRadius: 16,
        transition: 'all 0.3s', cursor: 'default', minHeight: 100,
      }}
        onMouseEnter={e => { e.currentTarget.style.borderColor = '#c2410c'; e.currentTarget.style.background = '#fff7ed' }}
        onMouseLeave={e => { e.currentTarget.style.borderColor = '#d1d5db'; e.currentTarget.style.background = '#fafbfc' }}
      >
        <div style={{ textAlign: 'center' }}>
          <Archive size={20} color="#9ca3af" style={{ margin: '0 auto 6px' }} />
          <p style={{ fontSize: 11, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.1em', fontWeight: 500, margin: 0 }}>Drop Zone</p>
        </div>
      </div>

      {/* Inventory */}
      <div style={{ background: '#fafbfc', padding: 20, borderRadius: 16, border: '1px solid #e8ecf1' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <span className="section-title">The Lot</span>
          <span style={{ fontSize: 11, color: '#9ca3af' }}>{items.length} items</span>
        </div>
        {items.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '20px 0', color: '#9ca3af' }}>
            <p style={{ fontSize: 13 }}>Nothing in The Lot</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 10 }}>
            {items.slice(0, 12).map((item, i) => <LotThumbnail key={i} item={item} />)}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Today() {
  return (
    <div style={{ height: '100%', background: '#f8f9fa', overflowY: 'auto' }}>
      <div style={{ maxWidth: 1400, margin: '0 auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* Top card — greeting + widget grid */}
        <div className="kai-card" style={{ padding: 24 }}>
          <p style={{ fontSize: 22, fontWeight: 300, color: '#1f2937', letterSpacing: '-0.02em', marginBottom: 20 }}>
            {greeting()}, <strong style={{ fontWeight: 600 }}>Leo</strong>
          </p>

          {/* Desktop grid */}
          <div className="hidden md:grid" style={{ gridTemplateColumns: '1.2fr 0.6fr 1.3fr', gridTemplateRows: '1fr 1fr', gap: 12, minHeight: 480 }}>
            <div style={{ gridColumn: 1, gridRow: 1, display: 'flex' }}><ProjectsWidget /></div>
            <div style={{ gridColumn: 2, gridRow: 1, display: 'flex' }}><HarmonyWidget /></div>
            <div style={{ gridColumn: 3, gridRow: '1 / 3' }}><ChatWidget /></div>
            <div style={{ gridColumn: 1, gridRow: 2, display: 'flex' }}><TodayPlayWidget /></div>
            <div style={{ gridColumn: 2, gridRow: 2, display: 'flex' }}><CheckInWidget /></div>
          </div>

          {/* Mobile stack */}
          <div className="md:hidden" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <CheckInWidget />
            <TodayPlayWidget />
            <HarmonyWidget />
            <ProjectsWidget />
            <div style={{ minHeight: 400 }}><ChatWidget /></div>
          </div>
        </div>

        {/* Lot card */}
        <div className="kai-card" style={{ padding: 20 }}>
          <LotWidget />
        </div>

      </div>

      <style>{`
        @keyframes bounce {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-4px); }
        }
      `}</style>
    </div>
  )
}
