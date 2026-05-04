import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { Globe, FileText, Layout, Plus, ExternalLink, RefreshCw, CheckCircle, Clock, AlertCircle, ChevronRight, Layers } from 'lucide-react'

const STATUS_COLOR = {
  publish:  '#10b981',
  draft:    '#f59e0b',
  private:  '#6366f1',
  trash:    '#ef4444',
}

const TASK_STATUS_META = {
  pending:    { color: '#f59e0b', icon: Clock,        label: 'Pending' },
  in_progress:{ color: '#6366f1', icon: RefreshCw,    label: 'In Progress' },
  complete:   { color: '#10b981', icon: CheckCircle,  label: 'Complete' },
}

const PRIORITY_COLOR = { high: '#ef4444', normal: '#6366f1', low: '#6b7280' }

export default function WordPress() {
  const [sites, setSites]         = useState([])
  const [activeSite, setActiveSite] = useState(null)
  const [posts, setPosts]         = useState([])
  const [pages, setPages]         = useState([])
  const [tasks, setTasks]         = useState([])
  const [view, setView]           = useState('pages')  // pages | posts | tasks
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState(null)
  const navigate = useNavigate()

  useEffect(() => { loadSites() }, [])
  useEffect(() => { if (activeSite) loadContent(activeSite) }, [activeSite, view])
  useEffect(() => { loadTasks() }, [])

  async function loadSites() {
    try {
      const d = await api.get('/wordpress/sites')
      setSites(d.sites || [])
      if (d.sites?.length) setActiveSite(d.sites[0].id)
    } catch(e) { setError('Could not load sites') }
  }

  async function loadContent(siteId) {
    setLoading(true)
    setError(null)
    try {
      if (view === 'pages' || view === 'posts') {
        const endpoint = view === 'pages'
          ? `/wordpress/${siteId}/posts?page_type=pages&count=30&status=any`
          : `/wordpress/${siteId}/posts?count=20&status=any`
        const d = await api.get(endpoint)
        if (view === 'pages') setPages(d.items || [])
        else setPosts(d.items || [])
      }
    } catch(e) { setError(`Could not load ${view}`) }
    setLoading(false)
  }

  async function loadTasks() {
    try {
      const d = await api.get('/wordpress/tasks')
      setTasks(d.tasks || [])
    } catch(e) { /* tasks endpoint may be new */ }
  }

  const site = sites.find(s => s.id === activeSite)
  const items = view === 'pages' ? pages : view === 'posts' ? posts : []
  const tasksBySite = activeSite
    ? tasks.filter(t => t.site === activeSite)
    : tasks

  return (
    <div style={{ padding: '24px', maxWidth: 1100, margin: '0 auto' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <Globe size={20} color="#6366f1" />
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>WordPress</h1>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}>
          {sites.length} sites registered
        </span>
      </div>

      {/* Site tabs */}
      <div style={{
        display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 20,
        borderBottom: '1px solid var(--border)', paddingBottom: 12,
      }}>
        {sites.map(s => (
          <button
            key={s.id}
            onClick={() => setActiveSite(s.id)}
            style={{
              padding: '5px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
              fontSize: 12, fontWeight: 500,
              background: activeSite === s.id ? '#6366f1' : 'var(--surface)',
              color: activeSite === s.id ? '#fff' : 'var(--text)',
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            {s.id}
            {s.blank_canvas_installed && (
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#10b981', display: 'inline-block' }} title="KAI Blank Canvas installed" />
            )}
          </button>
        ))}
      </div>

      {site && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 20 }}>

          {/* Main content panel */}
          <div>
            {/* Site meta + view toggle */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12,
              background: 'var(--surface)', borderRadius: 8,
              padding: '10px 16px', marginBottom: 16,
              border: '1px solid var(--border)',
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{site.description || site.id}</div>
                <a href={site.url} target="_blank" rel="noopener"
                  style={{ fontSize: 11, color: '#6366f1', textDecoration: 'none' }}>
                  {site.url} <ExternalLink size={10} style={{ verticalAlign: 'middle' }} />
                </a>
              </div>
              {site.business && (
                <span style={{
                  fontSize: 11, padding: '2px 8px', borderRadius: 4,
                  background: '#6366f115', color: '#6366f1', fontWeight: 500,
                }}>
                  {site.business}
                </span>
              )}
              <button
                onClick={() => loadContent(activeSite)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 4 }}
              >
                <RefreshCw size={14} />
              </button>
            </div>

            {/* View tabs */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
              {[
                { id: 'pages',  label: 'Pages',  icon: Layout },
                { id: 'posts',  label: 'Posts',  icon: FileText },
                { id: 'tasks',  label: `Tasks${tasksBySite.filter(t=>t.status!=='complete').length ? ` (${tasksBySite.filter(t=>t.status!=='complete').length})` : ''}`, icon: Layers },
              ].map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  onClick={() => setView(id)}
                  style={{
                    padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
                    cursor: 'pointer', fontSize: 12, fontWeight: 500,
                    display: 'flex', alignItems: 'center', gap: 6,
                    background: view === id ? 'var(--surface-active, #6366f115)' : 'transparent',
                    color: view === id ? '#6366f1' : 'var(--text-muted)',
                    borderColor: view === id ? '#6366f1' : 'var(--border)',
                  }}
                >
                  <Icon size={13} /> {label}
                </button>
              ))}
            </div>

            {/* Ask KAI bar */}
            <div
              onClick={() => navigate('/chat/kai')}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                background: '#6366f108', border: '1px dashed #6366f140',
                borderRadius: 8, padding: '10px 14px', marginBottom: 16,
                cursor: 'pointer', color: '#6366f1', fontSize: 13,
              }}
            >
              <Plus size={14} />
              Ask KAI to design or build something on <strong>{site.id}</strong>
              <ChevronRight size={13} style={{ marginLeft: 'auto' }} />
            </div>

            {/* Content list */}
            {loading ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
                <RefreshCw size={20} style={{ animation: 'spin 1s linear infinite' }} />
              </div>
            ) : error ? (
              <div style={{ display: 'flex', gap: 8, color: '#ef4444', padding: 16, alignItems: 'center' }}>
                <AlertCircle size={14} /> {error}
              </div>
            ) : view === 'tasks' ? (
              <TaskList tasks={tasksBySite} onRefresh={loadTasks} />
            ) : (
              <ContentList items={items} type={view} siteUrl={site.url} />
            )}
          </div>

          {/* Sidebar — all-sites task queue */}
          <div>
            <div style={{
              background: 'var(--surface)', borderRadius: 8,
              border: '1px solid var(--border)', overflow: 'hidden',
            }}>
              <div style={{
                padding: '12px 16px', borderBottom: '1px solid var(--border)',
                fontSize: 13, fontWeight: 600, display: 'flex', justifyContent: 'space-between',
              }}>
                Task Queue
                <span style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 400 }}>
                  {tasks.filter(t => t.status !== 'complete').length} pending
                </span>
              </div>
              {tasks.filter(t => t.status !== 'complete').length === 0 ? (
                <div style={{ padding: 20, color: 'var(--text-muted)', fontSize: 12, textAlign: 'center' }}>
                  No pending tasks
                </div>
              ) : (
                tasks.filter(t => t.status !== 'complete').slice(0, 10).map(task => (
                  <TaskCard key={task.id} task={task} />
                ))
              )}
            </div>

            {/* Plugin status */}
            <div style={{
              marginTop: 16, background: 'var(--surface)', borderRadius: 8,
              border: '1px solid var(--border)', padding: '12px 16px',
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>Blank Canvas Plugin</div>
              {sites.map(s => (
                <div key={s.id} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '4px 0', borderBottom: '1px solid var(--border)', fontSize: 12,
                }}>
                  <span>{s.id}</span>
                  {s.blank_canvas_installed
                    ? <span style={{ color: '#10b981', display: 'flex', gap: 4, alignItems: 'center' }}><CheckCircle size={12} /> Installed</span>
                    : <span style={{ color: 'var(--text-muted)' }}>Not installed</span>
                  }
                </div>
              ))}
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 10, lineHeight: 1.5 }}>
                Plugin: <code style={{ fontSize: 10 }}>~/kai-system/kai-wordpress-plugin/kai-blank-canvas.php</code>
              </div>
            </div>
          </div>

        </div>
      )}
    </div>
  )
}

function ContentList({ items, type, siteUrl }) {
  if (!items.length) return (
    <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: '20px 0' }}>
      No {type} found on this site.
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {items.map(item => (
        <div key={item.id} style={{
          background: 'var(--surface)', borderRadius: 8,
          border: '1px solid var(--border)', padding: '10px 14px',
          display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 500, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {item.title || '(no title)'}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
              /{item.slug} · {item.modified || item.date}
              {item.template === 'kai-blank' && (
                <span style={{ marginLeft: 8, color: '#6366f1', fontWeight: 600 }}>KAI</span>
              )}
            </div>
          </div>
          <span style={{
            fontSize: 11, padding: '2px 7px', borderRadius: 4, fontWeight: 500,
            background: `${STATUS_COLOR[item.status] || '#6b7280'}18`,
            color: STATUS_COLOR[item.status] || '#6b7280',
          }}>
            {item.status}
          </span>
          {item.link && (
            <a href={item.link} target="_blank" rel="noopener"
              style={{ color: 'var(--text-muted)', lineHeight: 1 }}>
              <ExternalLink size={13} />
            </a>
          )}
        </div>
      ))}
    </div>
  )
}

function TaskList({ tasks, onRefresh }) {
  if (!tasks.length) return (
    <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: '20px 0' }}>
      No tasks for this site. Ask KAI to queue one.
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {tasks.map(task => <TaskCard key={task.id} task={task} expanded />)}
    </div>
  )
}

function TaskCard({ task, expanded }) {
  const meta = TASK_STATUS_META[task.status] || TASK_STATUS_META.pending
  const Icon = meta.icon

  return (
    <div style={{
      padding: expanded ? '12px 14px' : '8px 16px',
      borderBottom: expanded ? 'none' : '1px solid var(--border)',
      background: expanded ? 'var(--surface)' : 'transparent',
      borderRadius: expanded ? 8 : 0,
      border: expanded ? '1px solid var(--border)' : 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon size={13} color={meta.color} />
        <span style={{ fontSize: 12, fontWeight: 500, flex: 1 }}>{task.title}</span>
        <span style={{
          fontSize: 10, padding: '1px 6px', borderRadius: 3,
          background: `${PRIORITY_COLOR[task.priority] || '#6b7280'}18`,
          color: PRIORITY_COLOR[task.priority] || '#6b7280',
        }}>
          {task.priority}
        </span>
      </div>
      {expanded && task.brief && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
          {task.brief.slice(0, 120)}{task.brief.length > 120 ? '…' : ''}
        </div>
      )}
      {expanded && (
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4 }}>
          {task.site} · {task.type} · {task.created}
        </div>
      )}
    </div>
  )
}
