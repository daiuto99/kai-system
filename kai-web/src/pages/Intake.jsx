import React, { useState, useEffect, useCallback } from 'react'

const API = '/api'
const ADVISOR = 'creative'
const ADVISOR_COLOR = '#a855f7'

const CATEGORIES = [
  { id: 'web_design',    label: 'Web Design' },
  { id: 'ui_ux',         label: 'UI / UX' },
  { id: 'typography',    label: 'Typography' },
  { id: 'logo',          label: 'Logo' },
  { id: 'marketing',     label: 'Marketing' },
  { id: 'color_palette', label: 'Color Palette' },
]

const STEPS = ['File', 'Verdict', 'Category', 'Notes', 'Review', 'Done']

function StepBar({ stage }) {
  const stageToStep = { idle: 0, q1: 1, q2: 2, q3: 3, clarifying: 4, done: 5 }
  const current = stageToStep[stage] ?? 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, padding: '0 24px', marginBottom: 28 }}>
      {STEPS.map((label, i) => {
        const done = i < current
        const active = i === current
        return (
          <React.Fragment key={label}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5, minWidth: 52 }}>
              <div style={{
                width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700,
                background: done ? ADVISOR_COLOR : active ? ADVISOR_COLOR + '22' : 'var(--bg-muted)',
                border: active ? '2px solid ' + ADVISOR_COLOR : done ? 'none' : '1px solid var(--border)',
                color: done ? '#fff' : active ? ADVISOR_COLOR : 'var(--text-tertiary)',
                transition: 'all 0.2s',
              }}>
                {done ? '✓' : i + 1}
              </div>
              <div style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em',
                color: active ? ADVISOR_COLOR : done ? 'var(--text-secondary)' : 'var(--text-subtle)' }}>
                {label}
              </div>
            </div>
            {i < STEPS.length - 1 && (
              <div style={{ flex: 1, height: 2, background: i < current ? ADVISOR_COLOR : 'var(--border)', transition: 'background 0.3s', marginBottom: 20 }} />
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}

function FileCard({ file, selected, onClick }) {
  const ext = file.ext.replace('.', '').toUpperCase()
  const isImage = ['.png', '.jpg', '.jpeg', '.gif', '.webp'].includes(file.ext)
  const kb = Math.round(file.size / 1024)
  return (
    <div onClick={onClick} style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px',
      borderRadius: 10, border: '1.5px solid ' + (selected ? ADVISOR_COLOR : 'var(--border)'),
      background: selected ? ADVISOR_COLOR + '10' : 'var(--bg-card)',
      cursor: 'pointer', transition: 'all 0.15s',
    }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.borderColor = ADVISOR_COLOR + '60' }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.borderColor = 'var(--border)' }}
    >
      <div style={{
        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
        background: isImage ? ADVISOR_COLOR + '20' : 'var(--bg-muted)',
        border: '1px solid ' + (isImage ? ADVISOR_COLOR + '30' : 'var(--border)'),
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 10, fontWeight: 800, color: isImage ? ADVISOR_COLOR : 'var(--text-tertiary)',
        letterSpacing: '0.03em',
      }}>{ext}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>{kb > 1024 ? (kb/1024).toFixed(1) + ' MB' : kb + ' KB'}</div>
      </div>
      {selected && <div style={{ width: 8, height: 8, borderRadius: '50%', background: ADVISOR_COLOR, flexShrink: 0 }} />}
    </div>
  )
}

function VerdictStep({ onAnswer, loading }) {
  const [choice, setChoice] = useState(null)
  return (
    <div>
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>
        Is this a reference or an avoid?
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 24, lineHeight: 1.6 }}>
        Reference examples show direction to follow. Avoid examples capture what not to do.
      </div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 28 }}>
        {[
          { value: 'reference', label: 'Reference', desc: 'Direction to follow', icon: '↑' },
          { value: 'avoid',     label: 'Avoid',     desc: 'What not to do',     icon: '✕' },
        ].map(opt => (
          <div key={opt.value} onClick={() => setChoice(opt.value)} style={{
            flex: 1, padding: '18px 20px', borderRadius: 12, cursor: 'pointer', transition: 'all 0.15s',
            border: '2px solid ' + (choice === opt.value ? ADVISOR_COLOR : 'var(--border)'),
            background: choice === opt.value ? ADVISOR_COLOR + '12' : 'var(--bg-card)',
          }}
            onMouseEnter={e => { if (choice !== opt.value) e.currentTarget.style.borderColor = ADVISOR_COLOR + '50' }}
            onMouseLeave={e => { if (choice !== opt.value) e.currentTarget.style.borderColor = 'var(--border)' }}
          >
            <div style={{ fontSize: 22, marginBottom: 8, color: choice === opt.value ? ADVISOR_COLOR : 'var(--text-tertiary)' }}>{opt.icon}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 3 }}>{opt.label}</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{opt.desc}</div>
          </div>
        ))}
      </div>
      <button disabled={!choice || loading} onClick={() => onAnswer(choice)} style={{
        padding: '10px 24px', borderRadius: 8, border: 'none', fontFamily: 'inherit',
        background: choice ? ADVISOR_COLOR : 'var(--bg-muted)',
        color: choice ? '#fff' : 'var(--text-subtle)', fontSize: 13, fontWeight: 600,
        cursor: choice ? 'pointer' : 'default', transition: 'all 0.15s',
      }}>{loading ? 'Saving…' : 'Continue'} →</button>
    </div>
  )
}

function CategoryStep({ onAnswer, loading }) {
  const [selected, setSelected] = useState([])
  const toggle = id => setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])
  return (
    <div>
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>
        What category applies?
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 20 }}>
        Pick all that apply.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 28 }}>
        {CATEGORIES.map(cat => {
          const on = selected.includes(cat.id)
          return (
            <div key={cat.id} onClick={() => toggle(cat.id)} style={{
              padding: '12px 14px', borderRadius: 10, cursor: 'pointer', transition: 'all 0.15s',
              border: '1.5px solid ' + (on ? ADVISOR_COLOR : 'var(--border)'),
              background: on ? ADVISOR_COLOR + '14' : 'var(--bg-card)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}
              onMouseEnter={e => { if (!on) e.currentTarget.style.borderColor = ADVISOR_COLOR + '50' }}
              onMouseLeave={e => { if (!on) e.currentTarget.style.borderColor = 'var(--border)' }}
            >
              <div style={{
                width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                border: '1.5px solid ' + (on ? ADVISOR_COLOR : 'var(--border)'),
                background: on ? ADVISOR_COLOR : 'transparent',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                {on && <div style={{ width: 6, height: 6, borderRadius: 1, background: '#fff' }} />}
              </div>
              <span style={{ fontSize: 12, fontWeight: 600, color: on ? 'var(--text-primary)' : 'var(--text-secondary)' }}>{cat.label}</span>
            </div>
          )
        })}
      </div>
      <button disabled={!selected.length || loading} onClick={() => onAnswer(selected.join(','))} style={{
        padding: '10px 24px', borderRadius: 8, border: 'none', fontFamily: 'inherit',
        background: selected.length ? ADVISOR_COLOR : 'var(--bg-muted)',
        color: selected.length ? '#fff' : 'var(--text-subtle)', fontSize: 13, fontWeight: 600,
        cursor: selected.length ? 'pointer' : 'default',
      }}>{loading ? 'Saving…' : 'Continue'} →</button>
    </div>
  )
}

function NotesStep({ onAnswer, loading }) {
  const [notes, setNotes] = useState('')
  return (
    <div>
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>
        Walk me through it
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 20, lineHeight: 1.6 }}>
        What specifically do you like or not like? Elements, sections, specific decisions — be as direct as you want.
      </div>
      <textarea
        value={notes}
        onChange={e => setNotes(e.target.value)}
        placeholder="e.g. Love the whitespace and type scale. The color palette is too saturated and the nav is overcrowded…"
        autoFocus
        style={{
          width: '100%', minHeight: 120, background: 'var(--bg-input)', color: 'var(--text-primary)',
          border: '1px solid var(--border)', borderRadius: 10, outline: 'none', resize: 'vertical',
          fontFamily: 'inherit', fontSize: 13, lineHeight: 1.7, padding: '12px 14px',
          boxSizing: 'border-box', marginBottom: 20,
        }}
        onFocus={e => e.target.style.borderColor = ADVISOR_COLOR}
        onBlur={e => e.target.style.borderColor = 'var(--border)'}
      />
      <button disabled={!notes.trim() || loading} onClick={() => onAnswer(notes)} style={{
        padding: '10px 24px', borderRadius: 8, border: 'none', fontFamily: 'inherit',
        background: notes.trim() ? ADVISOR_COLOR : 'var(--bg-muted)',
        color: notes.trim() ? '#fff' : 'var(--text-subtle)', fontSize: 13, fontWeight: 600,
        cursor: notes.trim() ? 'pointer' : 'default',
      }}>{loading ? 'Creative is thinking…' : 'Continue'} →</button>
    </div>
  )
}

function ClarifyStep({ question, index, total, onAnswer, loading }) {
  const [answer, setAnswer] = useState('')
  useEffect(() => setAnswer(''), [question])
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: ADVISOR_COLOR, marginBottom: 10 }}>
        Creative — Follow-up {index + 1} of {total}
      </div>
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 20, lineHeight: 1.5 }}>
        {question}
      </div>
      <textarea
        value={answer}
        onChange={e => setAnswer(e.target.value)}
        placeholder="Your answer…"
        autoFocus
        style={{
          width: '100%', minHeight: 90, background: 'var(--bg-input)', color: 'var(--text-primary)',
          border: '1px solid var(--border)', borderRadius: 10, outline: 'none', resize: 'vertical',
          fontFamily: 'inherit', fontSize: 13, lineHeight: 1.7, padding: '12px 14px',
          boxSizing: 'border-box', marginBottom: 20,
        }}
        onFocus={e => e.target.style.borderColor = ADVISOR_COLOR}
        onBlur={e => e.target.style.borderColor = 'var(--border)'}
      />
      <button disabled={!answer.trim() || loading} onClick={() => onAnswer(answer)} style={{
        padding: '10px 24px', borderRadius: 8, border: 'none', fontFamily: 'inherit',
        background: answer.trim() ? ADVISOR_COLOR : 'var(--bg-muted)',
        color: answer.trim() ? '#fff' : 'var(--text-subtle)', fontSize: 13, fontWeight: 600,
        cursor: answer.trim() ? 'pointer' : 'default',
      }}>{loading ? 'Saving…' : total > 1 && index < total - 1 ? 'Next →' : 'Finish'}</button>
    </div>
  )
}

function DoneSummary({ summary, onNext, onReset }) {
  const cats = CATEGORIES.filter(c => summary.categories?.includes(c.id))
  const pos = summary.annotations?.positive || []
  const neg = summary.annotations?.negative || []
  return (
    <div>
      <div style={{ fontSize: 22, marginBottom: 6 }}>✓</div>
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
        {summary.filename} saved
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 24 }}>
        Logged as <strong style={{ color: ADVISOR_COLOR }}>{summary.verdict}</strong> · {cats.map(c => c.label).join(', ') || 'general'}
      </div>

      {(pos.length > 0 || neg.length > 0) && (
        <div style={{ marginBottom: 24, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {pos.length > 0 && (
            <div style={{ background: '#10b98110', border: '1px solid #10b98130', borderRadius: 8, padding: '10px 14px' }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#34d399', marginBottom: 6 }}>Positive</div>
              {pos.map((p, i) => <div key={i} style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>· {p}</div>)}
            </div>
          )}
          {neg.length > 0 && (
            <div style={{ background: '#ef444410', border: '1px solid #ef444430', borderRadius: 8, padding: '10px 14px' }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#f87171', marginBottom: 6 }}>Avoid</div>
              {neg.map((n, i) => <div key={i} style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>· {n}</div>)}
            </div>
          )}
        </div>
      )}

      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 20 }}>
        Creative will apply these notes in future design conversations.
        {summary.queue_remaining > 0 && <span> <strong>{summary.queue_remaining} more file{summary.queue_remaining > 1 ? 's' : ''}</strong> ready to process.</span>}
      </div>

      <div style={{ display: 'flex', gap: 10 }}>
        {summary.queue_remaining > 0 && (
          <button onClick={onNext} style={{ padding: '9px 20px', borderRadius: 8, border: 'none', fontFamily: 'inherit', background: ADVISOR_COLOR, color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
            Next File →
          </button>
        )}
        <button onClick={onReset} style={{ padding: '9px 20px', borderRadius: 8, border: '1px solid var(--border)', fontFamily: 'inherit', background: 'none', color: 'var(--text-secondary)', fontSize: 13, cursor: 'pointer' }}>
          {summary.queue_remaining > 0 ? 'Done for now' : 'Process another'}
        </button>
      </div>
    </div>
  )
}

export default function Intake() {
  const [files, setFiles] = useState(null)
  const [selectedFile, setSelectedFile] = useState(null)
  const [stage, setStage] = useState('idle')
  const [clarifyQ, setClarifyQ] = useState('')
  const [clarifyIdx, setClarifyIdx] = useState(0)
  const [clarifyTotal, setClarifyTotal] = useState(0)
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadFiles = useCallback(() => {
    fetch(`${API}/intake/resources/${ADVISOR}`)
      .then(r => r.json())
      .then(d => setFiles(d.files || []))
      .catch(() => setFiles([]))
  }, [])

  useEffect(() => { loadFiles() }, [loadFiles])

  async function startFile(file) {
    setSelectedFile(file)
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`${API}/intake/start/${ADVISOR}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name }),
      })
      const d = await r.json()
      if (d.ok) setStage('q1')
      else setError(d.error || 'Failed to start')
    } catch(e) { setError('Network error') }
    setLoading(false)
  }

  async function sendReply(text) {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`${API}/intake/reply/${ADVISOR}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      const d = await r.json()
      if (!d.ok && d.error) { setError(d.error); setLoading(false); return }
      if (d.stage === 'q2') setStage('q2')
      else if (d.stage === 'q3') setStage('q3')
      else if (d.stage === 'clarifying') {
        setClarifyQ(d.current_question)
        setClarifyIdx(d.question_index)
        setClarifyTotal(d.question_total)
        setStage('clarifying')
      } else if (d.stage === 'done') {
        setSummary(d.summary)
        setStage('done')
        loadFiles()
      }
    } catch(e) { setError('Network error') }
    setLoading(false)
  }

  function reset() {
    setStage('idle')
    setSelectedFile(null)
    setSummary(null)
    setError('')
    loadFiles()
  }

  async function startNext() {
    loadFiles()
    setStage('idle')
    setSelectedFile(null)
    setSummary(null)
  }

  const processedCount = null

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ padding: '16px 24px 14px', flexShrink: 0, borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 3 }}>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.03em' }}>Knowledge Intake</h1>
          <span style={{ fontSize: 12, color: ADVISOR_COLOR, fontWeight: 600 }}>Creative</span>
        </div>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-tertiary)' }}>
          Process design examples and style references for Creative's knowledge base.
          Drop files into <code style={{ fontSize: 11, background: 'var(--bg-muted)', padding: '1px 5px', borderRadius: 4 }}>~/vault/60_Council/creative/resources/</code>
        </p>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>
        <StepBar stage={stage} />

        <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 20, alignItems: 'start' }}>
          {/* File list */}
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-subtle)', marginBottom: 10 }}>
              Unprocessed Files
            </div>
            {!files ? (
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Loading…</div>
            ) : files.length === 0 ? (
              <div style={{ padding: '20px 16px', borderRadius: 10, border: '1px dashed var(--border)', textAlign: 'center' }}>
                <div style={{ fontSize: 22, marginBottom: 8, opacity: 0.4 }}>📁</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                  No files yet.<br />Drop files into the resources folder.
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {files.map(f => (
                  <FileCard
                    key={f.name} file={f}
                    selected={selectedFile?.name === f.name && stage !== 'idle'}
                    onClick={() => { if (stage === 'idle' || stage === 'done') startFile(f) }}
                  />
                ))}
              </div>
            )}
            {files && files.length > 0 && stage === 'idle' && (
              <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-subtle)', lineHeight: 1.5 }}>
                Click a file to begin intake.
              </div>
            )}
          </div>

          {/* Wizard panel */}
          <div style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 14, padding: '28px 28px',
            borderTop: '3px solid ' + ADVISOR_COLOR,
          }}>
            {stage === 'idle' && (
              <div style={{ textAlign: 'center', padding: '32px 0', color: 'var(--text-tertiary)' }}>
                <div style={{ fontSize: 36, marginBottom: 12, opacity: 0.3 }}>◈</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>Select a file to begin</div>
                <div style={{ fontSize: 12, lineHeight: 1.6 }}>
                  Creative will ask you a few questions about the example, then store your preferences in the knowledge base.
                </div>
              </div>
            )}
            {stage === 'q1' && (
              <VerdictStep onAnswer={sendReply} loading={loading} />
            )}
            {stage === 'q2' && (
              <CategoryStep onAnswer={sendReply} loading={loading} />
            )}
            {stage === 'q3' && (
              <NotesStep onAnswer={sendReply} loading={loading} />
            )}
            {stage === 'clarifying' && (
              <ClarifyStep
                question={clarifyQ}
                index={clarifyIdx}
                total={clarifyTotal}
                onAnswer={sendReply}
                loading={loading}
              />
            )}
            {stage === 'done' && summary && (
              <DoneSummary summary={summary} onNext={startNext} onReset={reset} />
            )}

            {error && (
              <div style={{ marginTop: 14, padding: '8px 12px', borderRadius: 7, background: '#ef444415', border: '1px solid #ef444430', fontSize: 12, color: '#f87171' }}>
                {error}
              </div>
            )}

            {selectedFile && stage !== 'idle' && stage !== 'done' && (
              <div style={{ marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ fontSize: 11, color: 'var(--text-subtle)' }}>
                  Processing: <span style={{ color: 'var(--text-tertiary)', fontWeight: 600 }}>{selectedFile.name}</span>
                </div>
                <button onClick={async () => { await fetch(`${API}/intake/cancel/${ADVISOR}`, { method: 'DELETE' }); reset() }}
                  style={{ fontSize: 11, color: 'var(--text-subtle)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', padding: '2px 6px' }}>
                  Cancel
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
