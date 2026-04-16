import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { CheckSquare, RefreshCw } from 'lucide-react'

const PRIORITY_LABEL = { 1: 'P1', 2: 'P2', 3: 'P3', 4: 'P4' }
const PRIORITY_STYLE = {
  1: { color: '#ef4444' },
  2: { color: '#f59e0b' },
  3: { color: '#3882F6' },
  4: { color: '#9ca3af' },
}

export default function Tasks() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    try {
      const data = await api.getFocusBrief()
      const all = [...(data.top3 || []), ...(data.next5 || []), ...(data.remaining || [])]
      setTasks(all)
    } catch {
      setTasks([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '32px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 300, color: '#1f2937', letterSpacing: '-0.02em', margin: 0, lineHeight: 1.3, display: 'flex', alignItems: 'center', gap: 8 }}>
            <CheckSquare size={20} color="#9ca3af" />
            <span>Tasks</span>
          </h1>
          <p style={{ fontSize: 13, color: '#9ca3af', margin: '4px 0 0' }}>Pulled from Todoist. Ranked by KAI.</p>
        </div>
        <button onClick={load} className="btn-ghost" style={{ padding: '6px 10px' }}>
          <RefreshCw size={13} />
        </button>
      </div>

      {loading ? (
        <div className="kai-card" style={{ padding: '48px 20px', textAlign: 'center', fontSize: 13, color: '#9ca3af' }}>
          Loading tasks...
        </div>
      ) : tasks.length === 0 ? (
        <div className="kai-card" style={{ padding: '48px 20px', textAlign: 'center' }}>
          <p style={{ fontSize: 13, color: '#9ca3af', margin: 0 }}>Queue is clear.</p>
        </div>
      ) : (
        <div className="kai-card" style={{ overflow: 'hidden' }}>
          {tasks.map((task, i) => (
            <div
              key={task.id || i}
              style={{
                padding: '12px 20px', display: 'flex', alignItems: 'flex-start', gap: 14,
                borderBottom: i < tasks.length - 1 ? '1px solid #e8ecf1' : 'none',
              }}
            >
              <span style={{ fontSize: 11, color: '#9ca3af', fontFamily: 'monospace', marginTop: 2, width: 20, flexShrink: 0 }}>
                {i + 1}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p style={{ fontSize: 13, color: '#1f2937', lineHeight: 1.4, margin: 0 }}>{task.content}</p>
                {task.due && (
                  <p style={{ fontSize: 12, color: '#9ca3af', margin: '2px 0 0' }}>Due {task.due}</p>
                )}
                {task.project && (
                  <p style={{ fontSize: 12, color: '#9ca3af', margin: '2px 0 0' }}>{task.project}</p>
                )}
              </div>
              <span style={{ fontSize: 11, fontFamily: 'monospace', flexShrink: 0, marginTop: 2, ...(PRIORITY_STYLE[task.priority] || { color: '#9ca3af' }) }}>
                {PRIORITY_LABEL[task.priority] || '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
