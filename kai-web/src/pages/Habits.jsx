import { useState, useEffect } from 'react'

const HABITSYNC_COLOR = [
  '#e53935','#e64a19','#f57c00','#f9a825','#fdd835',
  '#c0ca33','#7cb342','#2e7d32','#00695c','#00838f',
  '#0277bd','#1565c0','#283593','#4527a0','#6a1b9a',
  '#ad1457','#880e4f','#4e342e','#546e7a','#37474f',
]

function habitColor(idx) {
  return HABITSYNC_COLOR[idx % HABITSYNC_COLOR.length] || '#6b7280'
}

function StreakDots({ completions }) {
  const days = []
  const today = new Date()
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(d.getDate() - i)
    days.push(d.toISOString().slice(0, 10))
  }
  return (
    <div style={{ display: 'flex', gap: 3 }}>
      {days.map(day => (
        <div key={day} title={day} style={{
          width: 8, height: 8, borderRadius: '50%',
          background: completions?.includes(day) ? '#10b981' : '#e8ecf1',
        }} />
      ))}
    </div>
  )
}

export default function Habits() {
  const [habits,  setHabits]  = useState([])
  const [loading, setLoading] = useState(true)
  const today = new Date().toISOString().slice(0, 10)

  useEffect(() => {
    fetch('/api/habits').then(r => r.json())
      .then(d => setHabits(d.habits || d || []))
      .catch(() => {}).finally(() => setLoading(false))
  }, [])

  function toggle(habit) {
    const done = habit.completions?.includes(today)
    fetch(`/api/habits/${habit.id}/complete`, { method: done ? 'DELETE' : 'POST' })
      .then(r => r.json())
      .then(() => {
        setHabits(prev => prev.map(h => h.id === habit.id
          ? { ...h, completions: done
              ? h.completions.filter(c => c !== today)
              : [...(h.completions || []), today] }
          : h))
      }).catch(() => {})
  }

  const groups = habits.reduce((acc, h) => {
    const g = h.group || 'Habits'
    if (!acc[g]) acc[g] = []
    acc[g].push(h)
    return acc
  }, {})

  const done  = habits.filter(h => h.completions?.includes(today)).length
  const total = habits.length

  return (
    <div style={{ height: '100%', background: 'var(--bg-screen)', overflowY: 'auto' }}>
      <div style={{ maxWidth: 700, margin: '0 auto', padding: '24px 16px' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>Habits</h1>
            <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: '3px 0 0' }}>
              {new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
            </p>
          </div>
          {total > 0 && (
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 28, fontWeight: 700, color: done === total ? '#10b981' : '#1f2937' }}>
                {done}<span style={{ fontSize: 16, color: 'var(--text-tertiary)', fontWeight: 400 }}>/{total}</span>
              </div>
              <div style={{ height: 4, width: 80, background: '#e8ecf1', borderRadius: 2, marginTop: 4 }}>
                <div style={{ height: '100%', width: `${total ? (done / total) * 100 : 0}%`, background: '#10b981', borderRadius: 2, transition: 'width 0.3s' }} />
              </div>
            </div>
          )}
        </div>

        {loading ? (
          <p style={{ color: 'var(--text-tertiary)', textAlign: 'center', padding: '40px 0' }}>Loading…</p>
        ) : habits.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px 0' }}>
            <p style={{ fontSize: 14, color: 'var(--text-tertiary)' }}>No habits yet.</p>
            <p style={{ fontSize: 13, color: 'var(--text-subtle)' }}>Add habits at <strong>habits.sonicink.space</strong></p>
          </div>
        ) : (
          Object.entries(groups).map(([groupName, groupHabits]) => (
            <div key={groupName} style={{ marginBottom: 28 }}>
              <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 10 }}>
                {groupName}
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {groupHabits.map(h => {
                  const isDone  = h.completions?.includes(today)
                  const accent  = habitColor(h.color ?? 0)
                  return (
                    <div key={h.id} onClick={() => toggle(h)} style={{
                      display: 'flex', alignItems: 'center', gap: 14,
                      padding: '12px 16px', borderRadius: 14,
                      background: isDone ? '#ecfdf5' : '#ffffff',
                      border: `1.5px solid ${isDone ? '#a7f3d0' : '#e8ecf1'}`,
                      cursor: 'pointer', transition: 'all 0.15s', userSelect: 'none',
                    }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = isDone ? '#10b981' : '#c2410c'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.08)' }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = isDone ? '#a7f3d0' : '#e8ecf1'; e.currentTarget.style.boxShadow = 'none' }}
                    >
                      {/* Icon */}
                      <div style={{
                        width: 40, height: 40, borderRadius: '50%', flexShrink: 0,
                        background: isDone ? '#10b981' : accent + '20',
                        border: `2px solid ${isDone ? '#10b981' : accent + '40'}`,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 18, transition: 'all 0.15s',
                      }}>
                        {isDone
                          ? <span style={{ color: '#fff', fontSize: 16, fontWeight: 700 }}>✓</span>
                          : h.emoji
                            ? <span>{h.emoji}</span>
                            : <span style={{ fontSize: 14, color: accent, fontWeight: 700 }}>{(h.displayName || h.name || '?')[0].toUpperCase()}</span>
                        }
                      </div>

                      {/* Name + streak */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <p style={{ fontSize: 14, fontWeight: 600, color: isDone ? '#059669' : '#1f2937', margin: 0 }}>
                          {h.displayName || h.name}
                        </p>
                        <StreakDots completions={h.completions} />
                      </div>

                      {/* Status */}
                      <span style={{
                        fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 6, flexShrink: 0,
                        background: isDone ? '#d1fae5' : '#f3f4f6',
                        color: isDone ? '#059669' : '#9ca3af',
                      }}>
                        {isDone ? 'Done' : 'Tap to log'}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
