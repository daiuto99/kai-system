// Work — Leo's actual work, structured as Business → Project → Deliverable.
// This is NOT KAI's own sprint/Plane board (that's the internal build board);
// the KAI System project is excluded on purpose. Text-forward / Vercel-style to
// match Now.jsx exactly: hairlines not boxes, mono labels, terra accent, fail-soft.
//
// Backend reality: there is no Business→Deliverable model server-side yet, and no
// GET /projects. Real projects come from /plane/issues (the Plane board), grouped
// under a built-in "Personal" business since no business/owner field exists. The
// Business / Deliverable / Brand / Ideas framing is honest STRUCTURE — real data
// where it exists, quiet structural sections where the backend isn't there yet.
// STRUCTURE-FIRST: layout of the IA; deep interaction is deferred.
import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, ArrowUpRight } from 'lucide-react'
import { api } from '../lib/api'

const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace"

// KAI's own project — its internal sprint board, deliberately not shown here.
const KAI_PROJECT_ID = '78c49227-82d4-477d-a920-66b08cb91c56'

// The one built-in business. Everything real hangs off it until a business/owner
// field exists server-side and we can group by it for real.
const PERSONAL = 'Personal'

// ── a text-forward section: a mono label over a hairline, then rows ───────────
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

// a single hairline-separated row
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

function SubLabel({ children }) {
  return (
    <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-tertiary)', fontFamily: MONO, margin: '16px 8px 2px' }}>{children}</div>
  )
}

// A structural chip — a labeled element in the anatomy (Brand, Deliverable…).
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

// ── Project anatomy — the structure of a project, revealed on expand ──────────
// Overview · Ethos & Goal · Research · Tasks (Plane, scoped) · Deliverable · Brand.
// All structural today except Tasks, which links to the live scoped Plane board.
function ProjectAnatomy({ project }) {
  const ANATOMY = [
    { key: 'overview',    label: 'Overview',      note: 'Auto-generated from the project — structural for now.' },
    { key: 'ethos',       label: 'Ethos & Goal',  note: 'Why it exists and what winning looks like.' },
    { key: 'research',    label: 'Research',       note: 'Source material, references, notes.' },
  ]
  return (
    <div style={{
      margin: '0 8px 4px', padding: '4px 0 10px',
      borderLeft: '2px solid var(--border)', paddingLeft: 16,
    }}>
      {ANATOMY.map((a) => (
        <div key={a.key} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-secondary)' }}>{a.label}</div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2, lineHeight: 1.45 }}>{a.note}</div>
        </div>
      ))}

      {/* Tasks — the one LIVE anatomy element: links to the scoped Plane board */}
      <div style={{ padding: '8px 0', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-secondary)' }}>Tasks</div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
            {typeof project.openCount === 'number' ? `${project.openCount} open in Plane` : 'Tracked in Plane'}
          </div>
        </div>
        <Link to="/plane" style={{
          fontSize: 11.5, fontWeight: 600, fontFamily: MONO, color: 'var(--accent)',
          textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 3, flexShrink: 0,
        }}>
          Open in Plane <ArrowUpRight size={13} strokeWidth={2.2} />
        </Link>
      </div>

      {/* Deliverable — the project's OUTPUT (an app, a site, a bio…) */}
      <div style={{ padding: '8px 0', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-secondary)' }}>Deliverable</div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>The output this project ships — app, site, bio…</div>
        </div>
        <Chip>Not linked</Chip>
      </div>

      {/* Brand — project-scope brand, inheriting from the house (business) brand */}
      <div style={{ padding: '8px 0', display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-secondary)' }}>Brand</div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>Project brand, inheriting from the house brand.</div>
        </div>
        <Chip>Inherits house</Chip>
      </div>
    </div>
  )
}

// A single project row that expands to reveal its anatomy.
function ProjectRow({ project }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <Row onClick={() => setOpen((o) => !o)}>
        <ChevronRight
          size={14}
          strokeWidth={2.2}
          style={{ flexShrink: 0, color: 'var(--text-tertiary)', transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms', alignSelf: 'center' }}
        />
        {project.identifier && (
          <span style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)', flexShrink: 0, minWidth: 44 }}>{project.identifier}</span>
        )}
        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{project.name}</span>
        {typeof project.openCount === 'number' && project.openCount > 0 && (
          <span style={{ fontSize: 11.5, fontFamily: MONO, color: 'var(--text-tertiary)', flexShrink: 0 }}>{project.openCount} open</span>
        )}
      </Row>
      {open && <ProjectAnatomy project={project} />}
    </div>
  )
}

// ── A Business — a top-level container grouping its projects ──────────────────
function Business({ name, projects }) {
  return (
    <Section label={name} right={`${projects.length} project${projects.length === 1 ? '' : 's'}`} style={{ marginBottom: 8 }}>
      {/* House brand — business-scope brand layer, structural */}
      <Row>
        <span style={{ fontSize: 14, color: 'var(--text-secondary)', flex: 1 }}>House brand</span>
        <Chip>Business scope</Chip>
      </Row>

      {projects.length === 0 ? (
        <div style={{ padding: '14px 8px', fontSize: 13.5, color: 'var(--text-tertiary)' }}>
          No projects here yet. Promote an idea below, or start one.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: '0 30px' }}>
          {projects.map((p) => <ProjectRow key={p.id || p.name} project={p} />)}
        </div>
      )}
    </Section>
  )
}

// ── Ideas — loose files / informal brainstorm; each promotable → Project ──────
// Structural: no ideas backend yet, so this shows the shape + the Promote affordance.
function Ideas() {
  const [ideas, setIdeas] = useState(null)
  useEffect(() => {
    // Fail-soft probe: if an ideas surface ever lands, it renders; otherwise empty.
    api.get('/ideas').then((d) => setIdeas(d.ideas || [])).catch(() => setIdeas([]))
  }, [])
  const list = ideas || []
  return (
    <Section label="Ideas" right="loose brainstorm">
      {list.length === 0 ? (
        <div style={{ padding: '14px 8px', fontSize: 13.5, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
          Nothing captured yet. Loose files and half-formed ideas land here; each can be
          promoted into a Project — its Overview auto-generates from what you've written.
        </div>
      ) : (
        list.map((idea, i) => (
          <Row key={idea.id || i}>
            <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{idea.title || idea.name || 'Untitled idea'}</span>
            <span style={{
              fontSize: 11, fontWeight: 600, fontFamily: MONO, color: 'var(--accent)',
              display: 'inline-flex', alignItems: 'center', gap: 3, flexShrink: 0,
            }}>
              Promote to Project <ArrowUpRight size={13} strokeWidth={2.2} />
            </span>
          </Row>
        ))
      )}
    </Section>
  )
}

export default function Work() {
  const [projects, setProjects] = useState(null)

  useEffect(() => {
    // Real data source: the Plane board. There is no GET /projects and no
    // Business→Deliverable model server-side yet, so we read /plane/issues and
    // treat each Plane project (except KAI's own) as one of Leo's projects.
    api.get('/plane/issues')
      .then((d) => {
        const raw = d.projects || []
        const mapped = raw
          .filter((p) => p.id !== KAI_PROJECT_ID)
          .map((p) => ({
            id: p.id,
            name: p.name,
            identifier: p.identifier,
            // No business/owner field exists — fall back to the Personal business.
            business: p.business || p.owner || PERSONAL,
            openCount: Array.isArray(p.issues) ? p.issues.length : undefined,
          }))
        setProjects(mapped)
      })
      .catch(() => setProjects([]))
  }, [])

  const list = projects || []

  // Group projects by business. Everything lands under "Personal" until the
  // backend carries a real business field. Personal always renders (it's built-in).
  const byBusiness = {}
  for (const p of list) {
    const b = p.business || PERSONAL
    if (!byBusiness[b]) byBusiness[b] = []
    byBusiness[b].push(p)
  }
  if (!byBusiness[PERSONAL]) byBusiness[PERSONAL] = []
  // Personal first, then any others alphabetically.
  const businessNames = [PERSONAL, ...Object.keys(byBusiness).filter((b) => b !== PERSONAL).sort()]

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
      <div style={{ fontSize: 26, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>Work</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 5, fontFamily: MONO, letterSpacing: '0.02em' }}>
        BUSINESS · PROJECT · DELIVERABLE
      </div>

      {/* Businesses — each a top-level container of projects */}
      <div style={{ marginTop: 30, display: 'flex', flexDirection: 'column', gap: 34 }}>
        {businessNames.map((name) => (
          <Business key={name} name={name} projects={byBusiness[name]} />
        ))}

        {/* Ideas — loose brainstorm, promotable to Projects */}
        <Ideas />
      </div>
    </div>
  )
}
