// Work — Leo's agency console: Business → Project → Deliverable, backed by the
// LIVE /console/* backend (P1–P4). This is NOT KAI's own sprint/Plane board.
//
// P5 (KAI-1459): the app shell + context switching.
//  - Reads the real object model from GET /api/console/store (no more /plane/issues
//    fallback, no more fake single "Personal" business).
//  - One active context at a time — "Open" a Business or Project to scope the view;
//    "Switch" swaps; the active context PERSISTS in localStorage so you resume where
//    you left off (design §6).
//  - Opening a Project loads its workspace (docs + deliverables + tasks) and brand
//    from GET /api/console/project/{id}/workspace and /brand.
//  - Ideas are real (from the store); each promotes via POST /api/console/promote
//    (KAI-1463) into a first-class Project.
// Drafts-only; brand-bearing authoring stays behind the Creative Gate — this surface
// only READS brand resolution.
import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, ArrowUpRight, ArrowLeft, FileText, Check, Circle } from 'lucide-react'
import { api } from '../lib/api'

const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace"
const CTX_KEY = 'kai-console-context'  // persisted active context {type,id}

// ── persistence ───────────────────────────────────────────────────────────────
function loadContext() {
  try { return JSON.parse(localStorage.getItem(CTX_KEY) || 'null') } catch { return null }
}
function saveContext(ctx) {
  try {
    if (ctx) localStorage.setItem(CTX_KEY, JSON.stringify(ctx))
    else localStorage.removeItem(CTX_KEY)
  } catch { /* ignore */ }
}

// ── primitives (match Now.jsx / the shipped shell: hairlines, mono labels) ──────
function Section({ label, right, children, style }) {
  return (
    <section style={style}>
      <div style={{
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        paddingBottom: 10, borderBottom: '1px solid var(--border)',
      }}>
        <span style={{
          fontSize: 11, fontWeight: 600, letterSpacing: '0.15em', textTransform: 'uppercase',
          color: 'var(--text-tertiary)', fontFamily: MONO,
        }}>{label}</span>
        {right && <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: MONO }}>{right}</span>}
      </div>
      <div>{children}</div>
    </section>
  )
}

function Row({ children, onClick }) {
  const [hover, setHover] = useState(false)
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex', alignItems: 'baseline', gap: 14, padding: '12px 8px',
        borderBottom: '1px solid var(--border)', cursor: onClick ? 'pointer' : 'default',
        background: hover && onClick ? 'var(--accent-bg)' : 'transparent',
        transition: 'background 120ms',
      }}
    >{children}</div>
  )
}

function Chip({ children, tone }) {
  const accent = tone === 'accent'
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase',
      fontFamily: MONO, padding: '2px 7px', borderRadius: 5,
      color: accent ? 'var(--accent)' : 'var(--text-tertiary)',
      border: `1px solid ${accent ? 'var(--accent)' : 'var(--border)'}`,
      background: accent ? 'var(--accent-bg)' : 'transparent',
      flexShrink: 0,
    }}>{children}</span>
  )
}

// A small text action (Open / Switch / Promote…).
function Action({ children, onClick, disabled }) {
  const [hover, setHover] = useState(false)
  return (
    <span
      onClick={disabled ? undefined : onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        fontSize: 11, fontWeight: 600, fontFamily: MONO, flexShrink: 0,
        color: disabled ? 'var(--text-tertiary)' : 'var(--accent)',
        opacity: disabled ? 0.5 : (hover ? 0.75 : 1),
        cursor: disabled ? 'default' : 'pointer',
        display: 'inline-flex', alignItems: 'center', gap: 3,
      }}
    >{children}</span>
  )
}

// ── the open-Project workspace — LIVE data from /console/project/{id}/workspace ──
const DOC_SLOTS = [
  { key: 'overview',   label: 'Overview' },
  { key: 'ethos_goal', label: 'Ethos & Goal' },
  { key: 'research',   label: 'Research' },
]

function ProjectWorkspace({ projectId, onBack, onSwitch }) {
  const [ws, setWs] = useState(null)
  const [brand, setBrand] = useState(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    let live = true
    setWs(null); setBrand(null); setErr(false)
    api.getProjectWorkspace(projectId).then((d) => { if (live) setWs(d) }).catch(() => { if (live) setErr(true) })
    api.getProjectBrand(projectId).then((d) => { if (live) setBrand(d) }).catch(() => { if (live) setBrand(null) })
    return () => { live = false }
  }, [projectId])

  if (err) return (
    <div style={{ padding: '20px 8px', fontSize: 13.5, color: 'var(--text-tertiary)' }}>
      Could not load this project. <Action onClick={onBack}>Back to all</Action>
    </div>
  )
  if (!ws) return <div style={{ padding: '20px 8px', fontSize: 13, color: 'var(--text-tertiary)', fontFamily: MONO }}>Loading context…</div>

  const p = ws.project || {}
  const docs = ws.docs || {}
  const delivs = ws.deliverables || []
  const eff = brand?.effective
  const brandSrc = eff?.source || 'none'

  return (
    <div>
      {/* context header — you are scoped to this project */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <Action onClick={onBack}><ArrowLeft size={13} strokeWidth={2.2} /> All</Action>
        <span style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)' }}>ACTIVE CONTEXT</span>
      </div>
      <div style={{ fontSize: 24, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>{p.name}</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 4 }}>
        {p.milestone ? `${p.milestone}${p.milestone_pct ? ` · ${p.milestone_pct}%` : ''}` : (p.description || '')}
        {p.next ? ` — next: ${p.next}` : ''}
      </div>

      {/* Docs — the editable doc-set, with real presence + a content preview */}
      <Section label="Docs" right="overview · ethos & goal · research" style={{ marginTop: 26 }}>
        {DOC_SLOTS.map(({ key, label }) => {
          const content = (docs[key] || '').trim()
          const has = content.length > 0
          return (
            <div key={key} style={{ padding: '10px 8px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                {has
                  ? <Check size={13} strokeWidth={2.4} style={{ color: 'var(--accent)', flexShrink: 0, alignSelf: 'center' }} />
                  : <Circle size={11} strokeWidth={2} style={{ color: 'var(--text-tertiary)', flexShrink: 0, alignSelf: 'center' }} />}
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', flex: 1 }}>{label}</span>
                <Chip>{has ? `${content.length} chars` : 'empty'}</Chip>
              </div>
              {has && (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4, lineHeight: 1.5,
                  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {content.replace(/^#.*$/m, '').trim().slice(0, 220)}
                </div>
              )}
            </div>
          )
        })}
      </Section>

      {/* Tasks — the scoped Plane board (or the pending-provisioning note) */}
      <Section label="Tasks" style={{ marginTop: 26 }}>
        <Row>
          <span style={{ fontSize: 13.5, color: 'var(--text-secondary)', flex: 1 }}>
            {p.plane_project ? 'Scoped Plane board' : (ws.tasks?.note || 'Per-project Plane board pending')}
          </span>
          {p.plane_project
            ? <Link to="/plane" style={{ fontSize: 11.5, fontWeight: 600, fontFamily: MONO, color: 'var(--accent)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 3 }}>Open in Plane <ArrowUpRight size={13} strokeWidth={2.2} /></Link>
            : <Chip>Not provisioned</Chip>}
        </Row>
      </Section>

      {/* Deliverable — real deliverables the project ships */}
      <Section label="Deliverables" right={`${delivs.length}`} style={{ marginTop: 26 }}>
        {delivs.length === 0
          ? <div style={{ padding: '14px 8px', fontSize: 13.5, color: 'var(--text-tertiary)' }}>No deliverables linked yet.</div>
          : delivs.map((d) => (
              <Row key={d.id}>
                <span style={{ fontSize: 13.5, color: 'var(--text-primary)', flex: 1 }}>{d.name}</span>
                <Chip>{d.type}</Chip>
                <Chip tone={d.status === 'in-progress' ? 'accent' : undefined}>{d.status}</Chip>
              </Row>
            ))}
      </Section>

      {/* Brand — effective (project ↔ house) resolution, READ-ONLY (Creative Gate authors) */}
      <Section label="Brand" right="resolved" style={{ marginTop: 26 }}>
        <Row>
          <span style={{ fontSize: 13.5, color: 'var(--text-secondary)', flex: 1 }}>
            {brand
              ? (brandSrc === 'none' ? 'No brand authored — inherits nothing yet' :
                 brandSrc === 'house' ? 'Inherits the house brand' : 'Project-specific brand')
              : 'Resolving…'}
          </span>
          {brand && <Chip tone={brandSrc !== 'none' ? 'accent' : undefined}>{brandSrc}</Chip>}
        </Row>
        {eff?.style_md && (
          <div style={{ padding: '10px 8px', fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5,
            display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden', fontFamily: MONO }}>
            {eff.style_md.replace(/^#.*$/m, '').trim().slice(0, 260)}
          </div>
        )}
      </Section>
    </div>
  )
}

// ── the open-Idea workspace — the living brief (render + EDIT) + sources index ──
// B1 (KAI-1465): the idea-side twin of ProjectWorkspace. The brief is the single
// state doc KAI maintains; here Leo reads it to get up to speed and captures new
// thinking back — the read-state → work → write-state loop (design §3/§4).
function IdeaWorkspace({ slug, onBack }) {
  const [ws, setWs] = useState(null)
  const [err, setErr] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState(null)
  // Brainstorm loop (B2): converse with KAI (grounded by the brief) then capture.
  const [chat, setChat] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [sending, setSending] = useState(false)
  const [grounded, setGrounded] = useState(false)  // brief injected on first turn only
  const [capturing, setCapturing] = useState(false)
  const [capDraft, setCapDraft] = useState('')
  const [capBusy, setCapBusy] = useState(false)
  const [capErr, setCapErr] = useState(null)

  const load = useCallback(() => {
    setWs(null); setErr(false)
    api.getIdeaWorkspace(slug).then(setWs).catch(() => setErr(true))
  }, [slug])
  useEffect(() => { load() }, [load])

  const startEdit = () => { setDraft(ws?.brief?.content || ''); setSaveErr(null); setEditing(true) }
  const save = async () => {
    setSaving(true); setSaveErr(null)
    try {
      await api.putIdeaBrief(slug, draft)
      setEditing(false)
      load()
    } catch (e) {
      setSaveErr(`Save failed: ${e.message || e}`)
    } finally { setSaving(false) }
  }

  const send = async () => {
    const text = chatInput.trim()
    if (!text || sending) return
    setChat((c) => [...c, { role: 'user', content: text }])
    setChatInput('')
    setSending(true)
    try {
      // Catch-up: on the first turn KAI receives the living brief so it's up to speed.
      const briefText = ws?.brief?.content || ''
      const msg = grounded
        ? text
        : `We're brainstorming on my idea "${ws?.idea?.name || slug}". Here's the current living brief so you're caught up:\n\n---\n${briefText}\n---\n\nBuild on this, don't just summarize it. ${text}`
      const res = await api.sendMessage(msg, 'kai')
      setGrounded(true)
      setChat((c) => [...c, { role: 'assistant', content: res.reply || '(no reply)' }])
    } catch (e) {
      setChat((c) => [...c, { role: 'assistant', content: `Error: ${e.message || e}`, error: true }])
    } finally { setSending(false) }
  }

  const [ingesting, setIngesting] = useState(false)
  const ingest = async () => {
    setIngesting(true)
    try { await api.ingestIdeaSources(slug); load() } catch { /* surfaced via reload */ } finally { setIngesting(false) }
  }

  const lastAssistant = [...chat].reverse().find((m) => m.role === 'assistant' && !m.error)?.content || ''
  const openCapture = () => { setCapDraft(lastAssistant); setCapErr(null); setCapturing(true) }
  const doCapture = async () => {
    const r = capDraft.trim()
    if (!r) { setCapErr('Nothing to capture.'); return }
    setCapBusy(true); setCapErr(null)
    try {
      await api.captureIdeaRiff(slug, r)
      setCapturing(false)
      load()  // brief refreshes with the folded riff
    } catch (e) {
      setCapErr(`Capture failed: ${e.message || e}`)
    } finally { setCapBusy(false) }
  }

  if (err) return (
    <div style={{ padding: '20px 8px', fontSize: 13.5, color: 'var(--text-tertiary)' }}>
      Could not load this idea. <Action onClick={onBack}>Back to all</Action>
    </div>
  )
  if (!ws) return <div style={{ padding: '20px 8px', fontSize: 13, color: 'var(--text-tertiary)', fontFamily: MONO }}>Loading idea…</div>

  const idea = ws.idea || {}
  const brief = ws.brief || {}
  const sources = ws.sources || []

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <Action onClick={onBack}><ArrowLeft size={13} strokeWidth={2.2} /> All</Action>
        <span style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)' }}>IDEA · BRAINSTORM</span>
      </div>
      <div style={{ fontSize: 24, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>{idea.name || idea.slug}</div>
      {idea.note && <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 4, lineHeight: 1.5 }}>{idea.note}</div>}

      {/* Living brief — the single state doc KAI maintains; render + edit (B1) */}
      <Section label="Brief" right={brief.exists ? '_brief.md' : 'not started — seeded'} style={{ marginTop: 26 }}>
        {!editing && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 8 }}>
            <Action onClick={startEdit}>{brief.exists ? 'Edit brief' : 'Start the brief'}</Action>
          </div>
        )}
        {editing ? (
          <div style={{ paddingTop: 10 }}>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              style={{ width: '100%', minHeight: 320, resize: 'vertical', fontFamily: MONO, fontSize: 12.5,
                lineHeight: 1.6, color: 'var(--text-primary)', background: 'var(--accent-bg)',
                border: '1px solid var(--border)', borderRadius: 6, padding: 12, boxSizing: 'border-box' }}
            />
            {saveErr && <div style={{ fontSize: 12, color: 'var(--danger, #e06c6c)', fontFamily: MONO, marginTop: 6 }}>{saveErr}</div>}
            <div style={{ display: 'flex', gap: 16, justifyContent: 'flex-end', marginTop: 10 }}>
              <Action onClick={() => setEditing(false)} disabled={saving}>Cancel</Action>
              <Action onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save brief'}</Action>
            </div>
          </div>
        ) : (
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: MONO, fontSize: 12.5,
            lineHeight: 1.6, color: 'var(--text-secondary)', margin: '10px 0 0', padding: '2px' }}>
            {(brief.content || '').trim()}
          </pre>
        )}
      </Section>

      {/* Brainstorm — the loop: catch-up (brief) → converse with KAI → capture (B2) */}
      <Section label="Brainstorm with KAI" right="brief → converse → capture" style={{ marginTop: 26 }}>
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {chat.length === 0 && (
            <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', lineHeight: 1.5, padding: '2px 2px' }}>
              KAI reads the brief above to get up to speed the moment you send. Riff freely, then capture what&apos;s worth keeping back into the brief.
            </div>
          )}
          {chat.map((m, i) => (
            <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '86%' }}>
              <div style={{ fontSize: 10, fontFamily: MONO, letterSpacing: '0.08em', textTransform: 'uppercase',
                color: 'var(--text-tertiary)', marginBottom: 3, textAlign: m.role === 'user' ? 'right' : 'left' }}>
                {m.role === 'user' ? 'You' : 'KAI'}
              </div>
              <div style={{ fontSize: 13.5, lineHeight: 1.55, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)',
                background: m.role === 'user' ? 'var(--accent-bg)' : 'transparent',
                color: m.error ? 'var(--danger, #e06c6c)' : 'var(--text-primary)' }}>
                {m.content}
              </div>
            </div>
          ))}
          {sending && <div style={{ fontSize: 12, fontFamily: MONO, color: 'var(--text-tertiary)' }}>KAI is thinking…</div>}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12 }}>
          <input
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder="Riff with KAI on this idea…"
            style={{ flex: 1, fontSize: 13.5, color: 'var(--text-primary)', background: 'transparent',
              border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', outline: 'none' }}
          />
          <Action onClick={send} disabled={sending || !chatInput.trim()}>Send</Action>
        </div>
        {chat.some((m) => m.role === 'assistant' && !m.error) && !capturing && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
            <Action onClick={openCapture}>Capture riff → brief</Action>
          </div>
        )}
        {capturing && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)', marginBottom: 6 }}>
              What&apos;s worth keeping? This folds into the brief&apos;s &quot;Last session&apos;s riff&quot;.
            </div>
            <textarea
              value={capDraft}
              onChange={(e) => setCapDraft(e.target.value)}
              style={{ width: '100%', minHeight: 120, resize: 'vertical', fontFamily: MONO, fontSize: 12.5,
                lineHeight: 1.6, color: 'var(--text-primary)', background: 'var(--accent-bg)',
                border: '1px solid var(--border)', borderRadius: 6, padding: 12, boxSizing: 'border-box' }}
            />
            {capErr && <div style={{ fontSize: 12, color: 'var(--danger, #e06c6c)', fontFamily: MONO, marginTop: 6 }}>{capErr}</div>}
            <div style={{ display: 'flex', gap: 16, justifyContent: 'flex-end', marginTop: 10 }}>
              <Action onClick={() => setCapturing(false)} disabled={capBusy}>Cancel</Action>
              <Action onClick={doCapture} disabled={capBusy}>{capBusy ? 'Capturing…' : 'Capture to brief'}</Action>
            </div>
          </div>
        )}
      </Section>

      {/* Sources index — live enumeration + one-line gist per file (B3 ingest) */}
      <Section label="Sources" right={`${sources.length}`} style={{ marginTop: 26 }}>
        {sources.length === 0
          ? <div style={{ padding: '14px 8px', fontSize: 13.5, color: 'var(--text-tertiary)' }}>No files dropped in this idea&apos;s folder yet.</div>
          : sources.map((s) => (
              <div key={s.name} style={{ padding: '10px 8px', borderBottom: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                  <FileText size={13} strokeWidth={2} style={{ flexShrink: 0, color: 'var(--text-tertiary)', alignSelf: 'center' }} />
                  <span style={{ fontSize: 13.5, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                  <Chip>{s.kind}</Chip>
                  {typeof s.bytes === 'number' && <span style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)' }}>{s.bytes} B</span>}
                </div>
                {s.description && (
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4, marginLeft: 23, lineHeight: 1.5,
                    display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                    {s.description}
                  </div>
                )}
              </div>
            ))}
        {sources.length > 0 && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 10 }}>
            <Action onClick={ingest} disabled={ingesting}>{ingesting ? 'Ingesting…' : 'Ingest sources → brief'}</Action>
          </div>
        )}
      </Section>
    </div>
  )
}

// ── a project row inside a business (in the overview) ───────────────────────────
function ProjectRow({ project, onOpen }) {
  return (
    <Row onClick={() => onOpen(project.id)}>
      <ChevronRight size={14} strokeWidth={2.2} style={{ flexShrink: 0, color: 'var(--text-tertiary)', alignSelf: 'center' }} />
      <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{project.name}</span>
      {project.milestone && (
        <span style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)', flexShrink: 0 }}>{project.milestone_pct ? `${project.milestone_pct}%` : project.status}</span>
      )}
      <Action onClick={(e) => { e.stopPropagation(); onOpen(project.id) }}>Open <ArrowUpRight size={12} strokeWidth={2.2} /></Action>
    </Row>
  )
}

// ── a Business block in the overview ────────────────────────────────────────────
function BusinessBlock({ business, projects, onOpenProject }) {
  return (
    <Section label={business.name} right={`${projects.length} project${projects.length === 1 ? '' : 's'}`} style={{ marginBottom: 8 }}>
      <Row>
        <span style={{ fontSize: 14, color: 'var(--text-secondary)', flex: 1 }}>House brand</span>
        <Chip tone={business.house_brand?.style_ref ? 'accent' : undefined}>{business.house_brand?.style_ref ? 'Business scope' : 'Not authored'}</Chip>
      </Row>
      {projects.length === 0 ? (
        <div style={{ padding: '14px 8px', fontSize: 13.5, color: 'var(--text-tertiary)' }}>
          No projects here yet. Promote an idea below, or start one.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: '0 30px' }}>
          {projects.map((p) => <ProjectRow key={p.id} project={p} onOpen={onOpenProject} />)}
        </div>
      )}
    </Section>
  )
}

// ── Ideas — real, promotable → Project (KAI-1463 promote wiring) ────────────────
function Ideas({ ideas, businesses, onPromoted, onOpen }) {
  const [busy, setBusy] = useState(null)   // slug being promoted
  const [pick, setPick] = useState(null)   // slug whose business picker is open
  const [error, setError] = useState(null)

  const promote = useCallback(async (idea, businessId) => {
    setBusy(idea.slug); setError(null)
    try {
      const res = await api.promoteIdea({ idea_slug: idea.slug, business_id: businessId })
      setPick(null)
      onPromoted(res)  // parent refetches store + opens the new project
    } catch (e) {
      setError(`Promote failed: ${e.message || e}`)
    } finally {
      setBusy(null)
    }
  }, [onPromoted])

  return (
    <Section label="Ideas" right="loose brainstorm — promotable">
      {error && <div style={{ padding: '10px 8px', fontSize: 12.5, color: 'var(--danger, #e06c6c)', fontFamily: MONO }}>{error}</div>}
      {(!ideas || ideas.length === 0) ? (
        <div style={{ padding: '14px 8px', fontSize: 13.5, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
          Nothing captured yet. Loose files and half-formed ideas land here; each can be
          promoted into a Project — its Overview auto-generates from what you've written.
        </div>
      ) : ideas.map((idea) => (
        <div key={idea.slug}>
          <Row onClick={() => onOpen(idea.slug)}>
            <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{idea.name || idea.slug}</span>
            <Chip>{idea.status}</Chip>
            <Action onClick={(e) => { e.stopPropagation(); onOpen(idea.slug) }}>Brainstorm <ArrowUpRight size={13} strokeWidth={2.2} /></Action>
            {busy === idea.slug
              ? <span style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)' }}>promoting…</span>
              : <Action onClick={(e) => { e.stopPropagation(); setPick(pick === idea.slug ? null : idea.slug) }}>
                  Promote to Project <ArrowUpRight size={13} strokeWidth={2.2} />
                </Action>}
          </Row>
          {pick === idea.slug && (
            <div style={{ padding: '10px 8px 14px 8px', borderBottom: '1px solid var(--border)', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 11.5, fontFamily: MONO, color: 'var(--text-tertiary)' }}>Promote under →</span>
              {businesses.map((b) => (
                <span key={b.id} onClick={() => promote(idea, b.id)}
                  style={{ fontSize: 11.5, fontFamily: MONO, color: 'var(--accent)', cursor: 'pointer',
                    border: '1px solid var(--accent)', borderRadius: 5, padding: '3px 8px', background: 'var(--accent-bg)' }}>
                  {b.name}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </Section>
  )
}

// ── the page ─────────────────────────────────────────────────────────────────
export default function Work() {
  const [store, setStore] = useState(null)
  const [context, setContext] = useState(loadContext)   // {type:'project', id} | null
  const [loadErr, setLoadErr] = useState(false)

  const fetchStore = useCallback(() => {
    return api.getConsoleStore()
      .then((d) => { setStore(d); setLoadErr(false); return d })
      .catch(() => { setStore({ businesses: [], projects: [], ideas: [] }); setLoadErr(true) })
  }, [])

  useEffect(() => { fetchStore() }, [fetchStore])

  const openProject = useCallback((id) => { const c = { type: 'project', id }; setContext(c); saveContext(c) }, [])
  const openIdea = useCallback((slug) => { const c = { type: 'idea', id: slug }; setContext(c); saveContext(c) }, [])
  const clearContext = useCallback(() => { setContext(null); saveContext(null) }, [])

  const onPromoted = useCallback(async (res) => {
    await fetchStore()
    if (res?.project?.id) openProject(res.project.id)
  }, [fetchStore, openProject])

  if (!store) return <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px', fontFamily: MONO, color: 'var(--text-tertiary)' }}>Loading console…</div>

  const businesses = store.businesses || []
  const projects = store.projects || []
  const ideas = store.ideas || []

  // Project-scoped view — one active context at a time (design §6).
  if (context?.type === 'project') {
    return (
      <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
        <ProjectWorkspace projectId={context.id} onBack={clearContext} onSwitch={openProject} />
      </div>
    )
  }

  // Idea-scoped view — the brainstorm loop over an idea's living brief (B1).
  if (context?.type === 'idea') {
    return (
      <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
        <IdeaWorkspace slug={context.id} onBack={clearContext} />
      </div>
    )
  }

  // Overview — all businesses + their projects + ideas.
  const projectsByBiz = {}
  for (const p of projects) {
    const b = p.business_id || 'unassigned'
    ;(projectsByBiz[b] = projectsByBiz[b] || []).push(p)
  }

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
      <div style={{ fontSize: 26, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>Work</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 5, fontFamily: MONO, letterSpacing: '0.02em' }}>
        BUSINESS · PROJECT · DELIVERABLE
        {loadErr && <span style={{ color: 'var(--danger, #e06c6c)' }}> — console offline, showing empty</span>}
      </div>

      <div style={{ marginTop: 30, display: 'flex', flexDirection: 'column', gap: 34 }}>
        {businesses.map((b) => (
          <BusinessBlock key={b.id} business={b} projects={projectsByBiz[b.id] || []} onOpenProject={openProject} />
        ))}
        <Ideas ideas={ideas} businesses={businesses} onPromoted={onPromoted} onOpen={openIdea} />
      </div>
    </div>
  )
}
