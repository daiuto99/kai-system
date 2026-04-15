import { useState, useEffect } from 'react'
import { api } from '../lib/api'
import { CheckSquare, RefreshCw } from 'lucide-react'

const PRIORITY_LABEL = { 1: 'P1', 2: 'P2', 3: 'P3', 4: 'P4' }
const PRIORITY_COLOR = {
  1: 'text-kai-red',
  2: 'text-kai-yellow',
  3: 'text-kai-blue',
  4: 'kai-text-subtle',
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
    <div className="max-w-3xl mx-auto px-8 py-10">
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <CheckSquare size={20} className="kai-text-subtle" />
            Tasks
          </h1>
          <p className="kai-text-subtle text-sm mt-1">Pulled from Todoist. Ranked by KAI.</p>
        </div>
        <button onClick={load} className="btn-ghost flex items-center gap-1.5 text-xs">
          <RefreshCw size={12} />
        </button>
      </div>

      {loading ? (
        <div className="kai-card px-5 py-12 text-center kai-text-subtle text-sm">
          Loading tasks...
        </div>
      ) : tasks.length === 0 ? (
        <div className="kai-card px-5 py-12 text-center">
          <p className="text-sm kai-text-subtle">Queue is clear.</p>
        </div>
      ) : (
        <div className="kai-card divide-y kai-divider">
          {tasks.map((task, i) => (
            <div key={task.id || i} className="px-5 py-3.5 flex items-start gap-4">
              <span className="text-xs kai-text-subtle font-mono mt-0.5 w-5 flex-shrink-0">
                {i + 1}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm leading-snug">{task.content}</p>
                {task.due && (
                  <p className="text-xs kai-text-subtle mt-0.5">Due {task.due}</p>
                )}
                {task.project && (
                  <p className="text-xs kai-text-subtle mt-0.5">{task.project}</p>
                )}
              </div>
              <span className={`text-xs font-mono flex-shrink-0 mt-0.5 ${PRIORITY_COLOR[task.priority] || 'kai-text-subtle'}`}>
                {PRIORITY_LABEL[task.priority] || '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
