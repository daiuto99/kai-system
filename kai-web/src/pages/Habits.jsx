import { useState, useEffect } from 'react'
import {
  Activity, Brain, HeartHandshake, Briefcase, Compass,
  Dumbbell, Stethoscope, HeartPulse, Heart, Smile,
  BookOpen, Lightbulb, Infinity, Feather,
  Users, Baby, Globe, Home, Waves,
  TrendingUp, DollarSign, BarChart, Target, Trophy,
  Sparkles, Star, Sun, Moon, Flame, Zap,
  Music, Palette, Mic, Camera, Pen, Mountain,
  Shield, Crown, Award, TreePine, Leaf, Coffee,
  Gem, Rocket, Bike, Wind, Flower, Eye, Anchor, Map, Flag, Clock,
  Check, X, Plus,
} from 'lucide-react'

const _ICON_MAP = {
  Activity, Brain, HeartHandshake, Briefcase, Compass,
  Dumbbell, Stethoscope, HeartPulse, Heart, Smile,
  BookOpen, Lightbulb, Infinity, Feather,
  Users, Baby, Globe, Home, Waves,
  TrendingUp, DollarSign, BarChart, Target, Trophy,
  Sparkles, Star, Sun, Moon, Flame, Zap,
  Music, Palette, Mic, Camera, Pen, Mountain,
  Shield, Crown, Award, TreePine, Leaf, Coffee,
  Gem, Rocket, Bike, Wind, Flower, Eye, Anchor, Map, Flag, Clock,
}

const ICON_SET = [
  'Activity','Dumbbell','HeartPulse','Heart','Smile','Stethoscope',
  'Brain','BookOpen','Lightbulb','Feather','Infinity','Pen',
  'Users','Baby','HeartHandshake','Home','Globe','Waves',
  'Briefcase','TrendingUp','DollarSign','BarChart','Target','Trophy',
  'Compass','Sparkles','Star','Sun','Moon','Flame','Zap',
  'Music','Palette','Mic','Camera','Mountain','Rocket',
  'Shield','Crown','Award','TreePine','Leaf','Coffee',
  'Gem','Bike','Wind','Flower','Eye','Anchor','Clock','Flag',
]

function LucideIcon({ name, size = 14, color = 'currentColor' }) {
  const C = _ICON_MAP[name]
  return C ? <C size={size} color={color} strokeWidth={1.75} /> : null
}

const HCOLOR = [
  '#e53935','#e64a19','#f57c00','#f9a825','#fdd835',
  '#c0ca33','#7cb342','#2e7d32','#00695c','#00838f',
  '#0277bd','#1565c0','#283593','#4527a0','#6a1b9a',
  '#ad1457','#880e4f','#4e342e','#546e7a','#37474f',
]
const habitColor = idx => HCOLOR[idx % HCOLOR.length] || 'var(--accent)'

function WeekDots({ completions }) {
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(); d.setDate(d.getDate() - (6 - i))
    return d.toISOString().slice(0, 10)
  })
  return (
    <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
      {days.map(day => (
        <div key={day} title={day} style={{
          width: 6, height: 6, borderRadius: '50%',
          background: completions?.includes(day) ? '#22c55e' : 'var(--border)',
          transition: 'background 0.2s',
        }} />
      ))}
    </div>
  )
}

function IconPicker({ current, onSelect, onClose }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 200,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)',
    }} onClick={onClose}>
      <div style={{
        background: 'var(--bg-card)', borderRadius: 16, padding: '20px 20px 16px',
        border: '1px solid var(--border)', width: 320,
        boxShadow: '0 24px 48px rgba(0,0,0,0.4)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Pick Icon</span>
          <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', color: 'var(--text-muted)' }}><X size={16} /></button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(10, 1fr)', gap: 6 }}>
          {ICON_SET.map(name => (
            <button key={name} onClick={() => onSelect(name)} title={name} style={{
              all: 'unset', cursor: 'pointer',
              width: 28, height: 28, borderRadius: 7,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: current === name ? 'var(--accent)' : 'var(--bg-elevated)',
              color: current === name ? '#fff' : 'var(--text-secondary)',
              transition: 'all 0.15s',
            }}
              onMouseEnter={e => { if (current !== name) e.currentTarget.style.background = 'var(--border)' }}
              onMouseLeave={e => { if (current !== name) e.currentTarget.style.background = 'var(--bg-elevated)' }}
            >
              <LucideIcon name={name} size={14} color={current === name ? '#fff' : 'var(--text-secondary)'} />
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function AddHabitModal({ onAdd, onClose }) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function submit(e) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setSaving(true)
    setErr('')
    try {
      const r = await fetch('/api/habits', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: trimmed }),
      })
      if (!r.ok) throw new Error(await r.text())
      const habit = await r.json()
      onAdd(habit)
    } catch (e) {
      setErr('Failed to create habit. Try again.')
      setSaving(false)
    }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 200,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)',
    }} onClick={onClose}>
      <div style={{
        background: 'var(--bg-card)', borderRadius: 16, padding: '24px',
        border: '1px solid var(--border)', width: 360,
        boxShadow: '0 24px 48px rgba(0,0,0,0.4)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>Add Habit</span>
          <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', color: 'var(--text-muted)' }}><X size={16} /></button>
        </div>
        <form onSubmit={submit}>
          <input
            autoFocus
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Habit name…"
            style={{
              width: '100%', boxSizing: 'border-box',
              padding: '10px 14px', borderRadius: 8,
              border: '1px solid var(--border)', background: 'var(--bg-elevated)',
              color: 'var(--text-primary)', fontSize: 14,
              fontFamily: 'inherit', outline: 'none',
              marginBottom: err ? 8 : 16,
            }}
          />
          {err && <p style={{ fontSize: 12, color: '#ef4444', margin: '0 0 14px' }}>{err}</p>}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" onClick={onClose} style={{
              padding: '8px 16px', borderRadius: 8, border: '1px solid var(--border)',
              background: 'transparent', color: 'var(--text-secondary)', fontSize: 13,
              cursor: 'pointer', fontFamily: 'inherit',
            }}>Cancel</button>
            <button type="submit" disabled={saving || !name.trim()} style={{
              padding: '8px 16px', borderRadius: 8, border: 'none',
              background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600,
              cursor: saving ? 'wait' : 'pointer', fontFamily: 'inherit',
              opacity: (!name.trim() || saving) ? 0.6 : 1,
            }}>{saving ? 'Adding…' : 'Add'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function Habits() {
  const [habits,    setHabits]    = useState([])
  const [loading,   setLoading]   = useState(true)
  const [icons,     setIcons]     = useState({})
  const [pickerFor, setPickerFor] = useState(null)
  const [showAdd,   setShowAdd]   = useState(false)
  const today = new Date().toISOString().slice(0, 10)

  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(); d.setDate(d.getDate() - (6 - i))
    return d.toISOString().slice(0, 10)
  })

  useEffect(() => {
    fetch('/api/habits').then(r => r.json())
      .then(d => setHabits(d.habits || d || []))
      .catch(() => {}).finally(() => setLoading(false))

    fetch('/api/habits/icons').then(r => r.json())
      .then(d => setIcons(d || {}))
      .catch(() => {
        try { setIcons(JSON.parse(localStorage.getItem('kai-habit-icons') || '{}')) } catch {}
      })
  }, [])

  function toggle(h) {
    const done = h.completions?.includes(today)
    fetch(`/api/habits/${h.id}/complete`, { method: done ? 'DELETE' : 'POST' })
      .then(r => r.json())
      .then(() => setHabits(prev => prev.map(x => x.id === h.id
        ? { ...x, completions: done
            ? x.completions.filter(c => c !== today)
            : [...(x.completions || []), today] }
        : x)))
      .catch(() => {})
  }

  function assignIcon(habitId, iconName) {
    const updated = { ...icons, [habitId]: iconName }
    setIcons(updated)
    setPickerFor(null)
    fetch('/api/habits/icons', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ icons: updated }),
    }).catch(() => {
      localStorage.setItem('kai-habit-icons', JSON.stringify(updated))
    })
  }

  function handleAdd(habit) {
    setHabits(prev => [...prev, habit])
    setShowAdd(false)
  }

  function getIconName(h) { return icons[h.id] || null }

  const groups = habits.reduce((acc, h) => {
    const g = h.group || 'Habits'
    if (!acc[g]) acc[g] = []
    acc[g].push(h)
    return acc
  }, {})

  const doneCount = habits.filter(h => h.completions?.includes(today)).length
  const total     = habits.length
  const pct       = total ? Math.round((doneCount / total) * 100) : 0

  const pickerHabit = pickerFor ? habits.find(h => h.id === pickerFor) : null

  return (
    <div style={{ height: '100%', background: 'var(--bg-screen)', overflowY: 'auto' }}>
      {pickerFor && (
        <IconPicker
          current={getIconName(pickerHabit)}
          onSelect={name => assignIcon(pickerFor, name)}
          onClose={() => setPickerFor(null)}
        />
      )}
      {showAdd && (
        <AddHabitModal
          onAdd={handleAdd}
          onClose={() => setShowAdd(false)}
        />
      )}

      <div style={{ maxWidth: 680, margin: '0 auto', padding: '28px 20px' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 28 }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 300, color: 'var(--text-primary)', letterSpacing: '-0.02em', margin: 0 }}>
              Habits <span style={{ fontWeight: 600 }}>— Today</span>
            </h1>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '4px 0 0' }}>
              {new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            {total > 0 && (
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 28, fontWeight: 300, color: doneCount === total ? '#22c55e' : 'var(--text-primary)', letterSpacing: '-0.03em' }}>
                  {doneCount}<span style={{ fontSize: 16, color: 'var(--text-muted)', fontWeight: 300 }}>/{total}</span>
                </div>
                <div style={{ height: 3, width: 80, background: 'var(--border)', borderRadius: 2, marginTop: 6 }}>
                  <div style={{ height: '100%', width: `${pct}%`, background: pct === 100 ? '#22c55e' : 'var(--accent)', borderRadius: 2, transition: 'width 0.4s ease' }} />
                </div>
              </div>
            )}
            <button
              onClick={() => setShowAdd(true)}
              title="Add habit"
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                width: 34, height: 34, borderRadius: 8,
                border: '1px solid var(--border)', background: 'transparent',
                color: 'var(--text-secondary)', cursor: 'pointer', transition: 'all 0.2s',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-secondary)' }}
            >
              <Plus size={16} />
            </button>
          </div>
        </div>

        {loading ? (
          <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '60px 0', fontSize: 13 }}>Loading…</p>
        ) : habits.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '80px 0' }}>
            <p style={{ fontSize: 14, color: 'var(--text-muted)' }}>No habits yet.</p>
            <button
              onClick={() => setShowAdd(true)}
              style={{
                marginTop: 12, display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '9px 18px', borderRadius: 8, border: '1px solid var(--border)',
                background: 'transparent', color: 'var(--text-secondary)', fontSize: 13,
                cursor: 'pointer', fontFamily: 'inherit', transition: 'all 0.2s',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)' }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-secondary)' }}
            >
              <Plus size={13} /> Add your first habit
            </button>
          </div>
        ) : (
          Object.entries(groups).map(([groupName, groupHabits]) => {
            const groupDone = groupHabits.filter(h => h.completions?.includes(today)).length
            return (
              <div key={groupName} style={{ marginBottom: 32 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
                    {groupName}
                  </span>
                  <span style={{ fontSize: 10, color: 'var(--text-subtle)' }}>{groupDone}/{groupHabits.length}</span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingBottom: 6, borderBottom: '1px solid var(--border)', marginBottom: 4 }}>
                  <div style={{ width: 34, flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: 9, fontWeight: 600, color: 'var(--text-subtle)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>Habit</span>
                  <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-subtle)', textTransform: 'uppercase', letterSpacing: '0.07em', width: 36, textAlign: 'center' }}>Today</span>
                  <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-subtle)', textTransform: 'uppercase', letterSpacing: '0.07em', width: 80, textAlign: 'right' }}>Week</span>
                </div>

                {groupHabits.map((h, idx) => {
                  const isDone    = h.completions?.includes(today)
                  const weekCount = weekDays.filter(d => h.completions?.includes(d)).length
                  const weekPct   = Math.round((weekCount / 7) * 100)
                  const accent    = habitColor(h.color ?? 0)
                  const iconName  = getIconName(h)

                  return (
                    <div key={h.id} style={{
                      display: 'flex', alignItems: 'center', gap: 12,
                      padding: '8px 0',
                      borderBottom: idx < groupHabits.length - 1 ? '1px solid var(--border)' : 'none',
                    }}>
                      <button
                        onClick={() => toggle(h)}
                        onContextMenu={e => { e.preventDefault(); setPickerFor(h.id) }}
                        title={`${h.displayName || h.name} — right-click to change icon`}
                        style={{
                          all: 'unset', cursor: 'pointer', flexShrink: 0,
                          width: 34, height: 34, borderRadius: 9,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          background: isDone ? '#22c55e22' : accent + '22',
                          border: `1.5px solid ${isDone ? '#22c55e55' : accent + '44'}`,
                          color: isDone ? '#22c55e' : accent,
                          transition: 'all 0.15s',
                        }}
                      >
                        {isDone
                          ? <Check size={15} strokeWidth={2.5} color="#22c55e" />
                          : iconName
                            ? <LucideIcon name={iconName} size={15} color={accent} />
                            : <span style={{ fontSize: 13, fontWeight: 700 }}>{(h.displayName || h.name || '?')[0].toUpperCase()}</span>
                        }
                      </button>

                      <span style={{
                        flex: 1, fontSize: 13, fontWeight: 500,
                        color: isDone ? 'var(--text-muted)' : 'var(--text-primary)',
                        textDecoration: isDone ? 'line-through' : 'none',
                        transition: 'color 0.2s',
                      }}>
                        {h.displayName || h.name}
                      </span>

                      <div style={{ width: 36, display: 'flex', justifyContent: 'center' }}>
                        <div style={{
                          width: 20, height: 20, borderRadius: '50%',
                          background: isDone ? '#22c55e' : 'var(--bg-elevated)',
                          border: `1.5px solid ${isDone ? '#22c55e' : 'var(--border)'}`,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          transition: 'all 0.2s',
                        }}>
                          {isDone && <Check size={11} strokeWidth={2.5} color="#fff" />}
                        </div>
                      </div>

                      <div style={{ width: 80, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                        <span style={{ fontSize: 10, color: weekPct >= 70 ? '#22c55e' : weekPct >= 40 ? 'var(--accent)' : 'var(--text-muted)', fontWeight: 600 }}>
                          {weekCount}/7
                        </span>
                        <WeekDots completions={h.completions} />
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          })
        )}

        {habits.length > 0 && (
          <p style={{ fontSize: 11, color: 'var(--text-subtle)', textAlign: 'center', marginTop: 12 }}>
            Right-click any habit icon to assign a Lucide icon
          </p>
        )}
      </div>
    </div>
  )
}
