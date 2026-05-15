import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Send, ChevronDown } from 'lucide-react'
import { api } from '../lib/api'
import { ADVISORS, getAdvisor } from '../lib/advisors'

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(parseFloat(ts) * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function Chat() {
  const { advisorId } = useParams()
  const navigate = useNavigate()
  const advisor = getAdvisor(advisorId || 'kai')

  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [showAdvisors, setShowAdvisors] = useState(false)
  const [modelCfg, setModelCfg] = useState({})

  useEffect(() => {
    fetch('/council/models/config')
      .then(r => r.json())
      .then(d => setModelCfg(d.advisors || {}))
      .catch(() => {})
  }, [])

  const bottomRef = useRef(null)
  const inputRef = useRef(null)
  const messagesRef = useRef(null)

  // Load history when advisor changes
  useEffect(() => {
    setLoadingHistory(true)
    setMessages([])
    api.getChannelHistory(advisor.channel)
      .then(data => setMessages(data.messages || []))
      .catch(() => setMessages([{ role: 'assistant', content: "Couldn't load history. Try refreshing.", ts: '', error: true }]))
      .finally(() => setLoadingHistory(false))
  }, [advisor.channel])

  // Pre-fill from Parking Lot "Ask KAI" action
  useEffect(() => {
    const prefill = sessionStorage.getItem('kai:prefill')
    if (prefill) {
      sessionStorage.removeItem('kai:prefill')
      setInput(prefill)
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [])

  // Scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinking])

  async function send() {
    const text = input.trim()
    if (!text || thinking) return
    setInput('')

    const userMsg = { role: 'user', content: text, ts: String(Date.now() / 1000) }
    setMessages(prev => [...prev, userMsg])
    setThinking(true)

    const history = messages.map(m => ({ role: m.role, content: m.content }))

    try {
      const data = await api.sendMessage(text, advisor.channel, history)
      const reply = data.reply || data.message || ''
      const assistantMsg = {
        role: 'assistant',
        content: reply,
        ts: String(Date.now() / 1000),
        provider: data.provider,
        model: data.model,
      }
      setMessages(prev => [...prev, assistantMsg])
    } catch (err) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Error: ' + (err?.message || err?.toString() || 'Unknown error'),
        ts: String(Date.now() / 1000),
        error: true,
      }])
    } finally {
      setThinking(false)
      inputRef.current?.focus()
    }
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  function switchAdvisor(a) {
    setShowAdvisors(false)
    navigate(`/chat/${a.id}`)
  }

  return (
    <div className="flex flex-col md:h-full" style={{ height: '100dvh' }}>

      {/* Header */}
      <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 border-b kai-divider">
        {/* Advisor selector trigger */}
        <button
          onClick={() => setShowAdvisors(v => !v)}
          className="flex items-center gap-2.5 group"
        >
          <span className="text-xl">{advisor.emoji}</span>
          <div className="text-left">
            <div className="flex items-center gap-1.5">
              <p className="text-sm font-semibold" style={{ color: advisor.color }}>
                {advisor.name}
              </p>
              <ChevronDown
                size={13}
                className={`kai-text-subtle transition-transform ${showAdvisors ? 'rotate-180' : ''}`}
              />
            </div>
            <p className="text-xs kai-text-subtle leading-none mt-0.5">{advisor.role}</p>
          </div>
        </button>

        {/* Desktop advisor chips */}
        <div className="hidden md:flex items-center gap-1">
          {ADVISORS.map(a => (
            <button
              key={a.id}
              onClick={() => switchAdvisor(a)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors
                ${advisor.id === a.id
                  ? 'bg-white/10 text-white'
                  : 'kai-text-subtle hover:text-white hover:bg-white/6'}`}
            >
              {a.emoji} {a.name}
            </button>
          ))}
        </div>
      </div>

      {/* Mobile advisor dropdown */}
      {showAdvisors && (
        <div className="md:hidden flex-shrink-0 border-b kai-divider bg-kai-dark-card2">
          <div className="flex overflow-x-auto px-4 py-3 gap-2 no-scrollbar">
            {ADVISORS.map(a => (
              <button
                key={a.id}
                onClick={() => switchAdvisor(a)}
                className={`flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium transition-colors
                  ${advisor.id === a.id ? 'bg-white/12 text-white' : 'bg-white/5 kai-text-subtle'}`}
              >
                <span>{a.emoji}</span>
                <span>{a.name}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Model indicator */}
      {(() => {
        const acfg = modelCfg[advisor.channel] || {}
        const prov = acfg.provider || 'anthropic'
        const mdl  = acfg.model || (prov === 'anthropic' ? 'claude-sonnet-4-5' : '—')
        const color = prov === 'anthropic' ? '#6366f1' : prov === 'ollama' ? '#f59e0b' : prov === 'openai' ? '#10a37f' : '#6b7280'
        const provLabel = prov === 'anthropic' ? 'Anthropic' : prov === 'ollama' ? 'Local' : prov === 'openai' ? 'OpenAI' : prov
        return (
          <div className="flex-shrink-0 flex items-center gap-1.5 px-4 py-1.5 border-b kai-divider" style={{ background: color + '08' }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, flexShrink: 0 }} />
            <span className="font-mono text-xs font-semibold" style={{ color: 'var(--text-secondary)' }}>{mdl}</span>
            <span className="text-xs kai-text-subtle">·</span>
            <span className="text-xs font-semibold" style={{ color }}>{provLabel}</span>
            {prov === 'ollama' && (
              <span className="text-xs kai-text-subtle ml-1">→ Anthropic fallback</span>
            )}
          </div>
        )
      })()}

      {/* Messages */}
      <div
        ref={messagesRef}
        className="flex-1 overflow-y-auto px-4 py-4 space-y-4"
      >
        {loadingHistory ? (
          <div className="flex justify-center pt-12">
            <p className="text-sm kai-text-subtle">Loading…</p>
          </div>
        ) : messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center pt-16 text-center px-8">
            <span className="text-4xl mb-4">{advisor.emoji}</span>
            <p className="text-base font-medium mb-1">{advisor.name}</p>
            <p className="text-sm kai-text-subtle max-w-xs">{advisor.intro}</p>
          </div>
        ) : (
          messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              {msg.role !== 'user' && (
                <span className="text-base mr-2 mt-0.5 flex-shrink-0">{advisor.emoji}</span>
              )}
              <div
                style={{ position: 'relative' }}
                className={`max-w-[85%] md:max-w-[70%] rounded-2xl px-4 py-3 text-sm leading-relaxed
                  ${msg.role === 'user'
                    ? 'bg-kai-blue text-white rounded-tr-sm'
                    : msg.error
                      ? 'bg-kai-red-dim text-kai-red border border-kai-red/20 rounded-tl-sm'
                      : 'bg-kai-dark-card2 kai-text-secondary border border-kai-dark-border rounded-tl-sm'
                  }`}
              >
                <p className="whitespace-pre-wrap">{msg.content}</p>
                {msg.ts && (
                  <p className="text-[10px] opacity-40 mt-1.5 text-right">
                    {formatTime(msg.ts)}
                  </p>
                )}
                {msg.role !== 'user' && msg.provider && (
                  <span title={msg.provider + '/' + msg.model} style={{
                    position: 'absolute', bottom: 5, right: 7,
                    width: 7, height: 7, borderRadius: '50%', display: 'inline-block',
                    background: msg.provider === 'anthropic' ? '#6366f1' : msg.provider === 'ollama' ? '#f59e0b' : msg.provider === 'openai' ? '#10a37f' : '#6b7280',
                    opacity: 0.8, cursor: 'default',
                  }} />
                )}
              </div>
            </div>
          ))
        )}

        {/* Thinking indicator */}
        {thinking && (
          <div className="flex justify-start items-end gap-2">
            <span className="text-base">{advisor.emoji}</span>
            <div className="bg-kai-dark-card2 border border-kai-dark-border rounded-2xl rounded-tl-sm px-4 py-3">
              <div className="flex gap-1 items-center h-4">
                <span className="w-1.5 h-1.5 rounded-full bg-white/30 animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-white/30 animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-white/30 animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div
        className="flex-shrink-0 px-4 pt-3 border-t kai-divider"
        style={{ paddingBottom: 'max(0.75rem, calc(84px + env(safe-area-inset-bottom)))' }}
      >
        <div className="flex items-end gap-2 bg-kai-dark-card2 border border-kai-dark-border rounded-2xl px-4 py-3">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder={`Message ${advisor.name}…`}
            rows={1}
            className="flex-1 bg-transparent resize-none outline-none kai-text-secondary placeholder:text-white/20 leading-relaxed max-h-32 overflow-y-auto"
            style={{ fontSize: '16px', minHeight: '20px' }}
            onInput={e => {
              e.target.style.height = 'auto'
              e.target.style.height = Math.min(e.target.scrollHeight, 128) + 'px'
            }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || thinking}
            className="flex-shrink-0 w-8 h-8 rounded-full bg-kai-blue flex items-center justify-center transition-opacity disabled:opacity-30"
          >
            <Send size={14} className="text-white" />
          </button>
        </div>
        <p className="text-[10px] kai-text-subtle text-center mt-1.5 hidden md:block">
          ↵ to send · ⇧↵ for new line
        </p>
      </div>
    </div>
  )
}
