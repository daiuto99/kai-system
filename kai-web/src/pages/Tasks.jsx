import { useState, useEffect, useRef } from 'react'
import { CheckSquare, Plus, Trash2, RefreshCw, GripVertical } from 'lucide-react'

const PRIORITY_COLOR = { 1: '#ef4444', 2: '#f97316', 3: '#f59e0b', 4: '#9ca3af' }
const PRIORITY_LABEL = { 1: 'P1', 2: 'P2', 3: 'P3', 4: 'P4' }
const TODAY = new Date().toISOString().slice(0, 10)
const IN_7 = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)

function bucket(task) {
  if (!task.due) return 'backlog'
  if (task.due <= TODAY) return 'today'
  if (task.due <= IN_7) return 'week'
  return 'backlog'
}

async function moveTask(id, targetCol) {
  let body = {}
  if (targetCol === 'today')   body = { move_to_today: true }
  if (targetCol === 'week')    body = { due_date: new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10) }
  if (targetCol === 'backlog') body = { due_date: '' }
  await fetch(`/api/tasks/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// ── Task Card ────────────────────────────────────────────────────────────────

function TaskCard({ task, onComplete, onDelete, onMove, colId }) {
  const pc = PRIORITY_COLOR[task.priority || 4]
  const dragRef = useRef(null)

  function onDragStart(e) {
    e.dataTransfer.setData('taskId', task.id)
    e.dataTransfer.setData('fromCol', colId)
    e.dataTransfer.effectAllowed = 'move'
    setTimeout(() => { if (dragRef.current) dragRef.current.style.opacity = '0.4' }, 0)
  }
  function onDragEnd() {
    if (dragRef.current) dragRef.current.style.opacity = '1'
  }

  return (
    <div ref={dragRef} draggable
      onDragStart={onDragStart} onDragEnd={onDragEnd}
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 8, padding: '9px 11px',
        borderRadius: 9, background: 'var(--bg-card)', border: '1px solid var(--border)',
        cursor: 'grab', transition: 'border-color 0.15s, box-shadow 0.15s',
        userSelect: 'none',
      }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = pc + '60'; e.currentTarget.style.boxShadow = `0 0 0 1px ${pc}20` }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none' }}
    >
      <GripVertical size={12} color="var(--text-subtle)" style={{ flexShrink: 0, marginTop: 2 }} />
      <div style={{ width: 6, height: 6, borderRadius: '50%', background: pc, flexShrink: 0, marginTop: 4 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: 'var(--text-primary)', lineHeight: 1.4 }}>{task.content}</div>
        {task.due && (
          <div style={{ fontSize: 10, color: task.due < TODAY ? '#ef4444' : 'var(--text-subtle)', marginTop: 2 }}>
            {task.due < TODAY ? 'Overdue · ' : ''}{task.due}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
        <span style={{ fontSize: 9, fontFamily: 'monospace', color: pc, fontWeight: 700, marginTop: 2 }}>
          {PRIORITY_LABEL[task.priority || 4]}
        </span>
        <button onClick={() => onComplete(task.id)}
          style={{ all: 'unset', cursor: 'pointer', color: '#10b981', fontSize: 13, lineHeight: 1, padding: '0 2px' }}
          title="Complete">✓</button>
        <button onClick={() => onDelete(task.id)}
          style={{ all: 'unset', cursor: 'pointer', color: 'var(--text-subtle)', lineHeight: 1, padding: '0 2px' }}
          title="Delete">
          <Trash2 size={11} />
        </button>
      </div>
    </div>
  )
}

// ── Add Task Row ─────────────────────────────────────────────────────────────

function AddTask({ colId, onAdded }) {
  const [open, setOpen] = useState(false)
  const [val, setVal]   = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    if (!val.trim()) return
    setBusy(true)
    const body = { content: val.trim() }
    if (colId === 'today') body.due_date = TODAY
    if (colId === 'week')  body.due_date = new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10)
    await fetch('/api/tasks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    setVal(''); setBusy(false); setOpen(false); onAdded()
  }

  if (!open) return (
    <button onClick={() => setOpen(true)} style={{
      all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--text-subtle)',
      display: 'flex', alignItems: 'center', gap: 5, padding: '5px 2px',
      width: '100%',
    }}
      onMouseEnter={e => e.currentTarget.style.color = 'var(--text-secondary)'}
      onMouseLeave={e => e.currentTarget.style.color = 'var(--text-subtle)'}
    >
      <Plus size={12} /> Add task
    </button>
  )

  return (
    <form onSubmit={submit} style={{ display: 'flex', gap: 6 }}>
      <input autoFocus value={val} onChange={e => setVal(e.target.value)}
        onKeyDown={e => e.key === 'Escape' && setOpen(false)}
        placeholder="Task name..."
        style={{
          flex: 1, fontSize: 12, padding: '6px 9px', borderRadius: 7,
          border: '1px solid var(--accent)', background: 'var(--bg-base)',
          color: 'var(--text-primary)', fontFamily: 'inherit', outline: 'none',
        }}
      />
      <button type="submit" disabled={busy || !val.trim()} style={{
        all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
        padding: '6px 10px', borderRadius: 7, background: 'var(--accent)', color: '#fff',
        opacity: busy || !val.trim() ? 0.5 : 1,
      }}>Add</button>
    </form>
  )
}

// ── Column ────────────────────────────────────────────────────────────────────

function Column({ id, label, accent, items, onComplete, onDelete, onMove, onAdded }) {
  const [over, setOver] = useState(false)

  function onDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setOver(true) }
  function onDragLeave() { setOver(false) }
  function onDrop(e) {
    e.preventDefault(); setOver(false)
    const taskId = e.dataTransfer.getData('taskId')
    const fromCol = e.dataTransfer.getData('fromCol')
    if (taskId && fromCol !== id) onMove(taskId, id)
  }

  return (
    <div
      onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}
      style={{
        display: 'flex', flexDirection: 'column', gap: 0,
        background: over ? accent + '08' : 'transparent',
        borderRadius: 12, border: over ? `1px dashed ${accent}50` : '1px solid transparent',
        transition: 'all 0.15s', padding: 8, minHeight: 120,
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 10 }}>
        <div style={{ width: 3, height: 14, borderRadius: 2, background: accent }} />
        <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: accent }}>{label}</span>
        <span style={{
          fontSize: 10, fontWeight: 600, color: 'var(--text-subtle)',
          background: 'var(--bg-elevated)', border: '1px solid var(--border)',
          borderRadius: 10, padding: '1px 7px',
        }}>{items.length}</span>
      </div>

      {/* Cards */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}>
        {items.length === 0 ? (
          <div style={{ padding: '16px 0', textAlign: 'center', fontSize: 11, color: 'var(--text-subtle)', fontStyle: 'italic' }}>
            {over ? 'Drop here' : 'Empty'}
          </div>
        ) : items.map(t => (
          <TaskCard key={t.id} task={t} colId={id}
            onComplete={onComplete} onDelete={onDelete} onMove={onMove} />
        ))}
      </div>

      {/* Add task */}
      <div style={{ marginTop: 8 }}>
        <AddTask colId={id} onAdded={onAdded} />
      </div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function Tasks() {
  const [tasks,   setTasks]   = useState([])
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    try {
      const d = await fetch('/api/tasks').then(r => r.json())
      const all = [...(d.today || []), ...(d.inbox || [])]
      const seen = new Set()
      setTasks(all.filter(t => { if (seen.has(t.id)) return false; seen.add(t.id); return true }))
    } catch {}
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  async function handleComplete(id) {
    await fetch(`/api/tasks/${id}/complete`, { method: 'POST' })
    setTasks(p => p.filter(t => t.id !== id))
  }

  async function handleDelete(id) {
    await fetch(`/api/tasks/${id}`, { method: 'DELETE' })
    setTasks(p => p.filter(t => t.id !== id))
  }

  async function handleMove(id, toCol) {
    await moveTask(id, toCol)
    // Optimistic update: recalculate due date locally
    setTasks(prev => prev.map(t => {
      if (t.id !== id) return t
      if (toCol === 'today')   return { ...t, due: TODAY }
      if (toCol === 'week')    return { ...t, due: new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10) }
      if (toCol === 'backlog') return { ...t, due: null }
      return t
    }))
  }

  const todayItems   = tasks.filter(t => bucket(t) === 'today').sort((a, b) => (a.priority || 4) - (b.priority || 4))
  const weekItems    = tasks.filter(t => bucket(t) === 'week').sort((a, b) => (a.due || '').localeCompare(b.due || ''))
  const backlogItems = tasks.filter(t => bucket(t) === 'backlog').sort((a, b) => (a.priority || 4) - (b.priority || 4))

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '24px 24px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 10, letterSpacing: '-0.02em' }}>
            <CheckSquare size={18} color="var(--text-subtle)" strokeWidth={1.75} />
            Tasks
          </h1>
          {!loading && (
            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-subtle)' }}>
              {tasks.length} open · {todayItems.length} today · drag to reschedule
            </p>
          )}
        </div>
        <button onClick={load} style={{
          all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
          padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border)',
          background: 'var(--bg-card)', color: 'var(--text-secondary)',
          display: 'flex', alignItems: 'center', gap: 5,
        }}>
          <RefreshCw size={11} /> Refresh
        </button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--text-subtle)', fontSize: 13 }}>Loading…</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, alignItems: 'start' }}>
          <Column id="today"   label="Today"     accent="#6366f1" items={todayItems}
            onComplete={handleComplete} onDelete={handleDelete} onMove={handleMove} onAdded={load} />
          <Column id="week"    label="This Week"  accent="#10b981" items={weekItems}
            onComplete={handleComplete} onDelete={handleDelete} onMove={handleMove} onAdded={load} />
          <Column id="backlog" label="Backlog"    accent="#f59e0b" items={backlogItems}
            onComplete={handleComplete} onDelete={handleDelete} onMove={handleMove} onAdded={load} />
        </div>
      )}
    </div>
  )
}
