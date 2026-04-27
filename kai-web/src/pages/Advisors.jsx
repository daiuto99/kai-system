import React, { useState, useEffect } from 'react'

const API = '/api'

const COUNCIL   = ['beats','sky','roads','coach','doc','ember']
const EXEC_TEAM = ['creative','dev','devops']
const HAS_ORG   = ['creative','dev','devops','doc','coach','kai']

const STATUS_BADGE = {
  active:      { label: 'Active',      bg: '#10b98118', color: '#34d399', border: '#10b98130' },
  spec_needed: { label: 'Spec Needed', bg: '#f59e0b18', color: '#fbbf24', border: '#f59e0b30' },
}
const MODEL_OPTIONS = ['claude-sonnet-4-6','claude-opus-4-7','claude-haiku-4-5-20251001','qwen2.5:3b']

const OrgIcon = ({ color }) => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" style={{ flexShrink: 0 }}>
    <rect x="5" y="1" width="4" height="3" rx="1" fill={color} opacity="0.9"/>
    <rect x="1" y="9" width="3.5" height="3" rx="1" fill={color} opacity="0.7"/>
    <rect x="5.25" y="9" width="3.5" height="3" rx="1" fill={color} opacity="0.7"/>
    <rect x="9.5" y="9" width="3.5" height="3" rx="1" fill={color} opacity="0.7"/>
    <line x1="7" y1="4" x2="7" y2="7" stroke={color} strokeWidth="1" opacity="0.5"/>
    <line x1="2.75" y1="7" x2="11.25" y2="7" stroke={color} strokeWidth="1" opacity="0.5"/>
    <line x1="2.75" y1="7" x2="2.75" y2="9" stroke={color} strokeWidth="1" opacity="0.5"/>
    <line x1="7" y1="7" x2="7" y2="9" stroke={color} strokeWidth="1" opacity="0.5"/>
    <line x1="11.25" y1="7" x2="11.25" y2="9" stroke={color} strokeWidth="1" opacity="0.5"/>
  </svg>
)

function Avatar({ advisor, size = 48 }) {
  const [err, setErr] = useState(false)
  const r = Math.round(size * 0.25)
  if (advisor.avatar && !err) return (
    <img src={advisor.avatar} alt={advisor.name} onError={() => setErr(true)}
      style={{ width: size, height: size, borderRadius: r, objectFit: 'cover', objectPosition: 'center top', flexShrink: 0 }} />
  )
  const initials = advisor.name.split(' ').map(w => w[0]).join('').slice(0,2).toUpperCase()
  return (
    <div style={{
      width: size, height: size, borderRadius: r, flexShrink: 0,
      background: advisor.color + '22', border: '1px solid ' + advisor.color + '44',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: Math.round(size * 0.33), fontWeight: 800, color: advisor.color,
    }}>{initials}</div>
  )
}

function Badge({ label, bg, color, border }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 700,
      padding: '2px 7px', borderRadius: 20, letterSpacing: '0.04em', textTransform: 'uppercase',
      background: bg, color, border: '1px solid ' + border,
    }}>{label}</span>
  )
}

/* ── Photo-forward portrait card ─────────────────────────────────────────── */
function AdvisorCard({ advisor, isSelected, onClick, isExec = false }) {
  const [imgErr, setImgErr] = useState(false)
  const hasOrg = HAS_ORG.includes(advisor.id)

  return (
    <div onClick={onClick} style={{
      aspectRatio: '1',
      borderRadius: 12,
      overflow: 'hidden',
      position: 'relative',
      cursor: 'pointer',
      border: '2px solid ' + (isSelected ? advisor.color : 'transparent'),
      transition: 'transform 0.18s, box-shadow 0.18s, border-color 0.18s',
      transform: isSelected ? 'translateY(-3px)' : 'translateY(0)',
      boxShadow: isSelected
        ? '0 10px 36px ' + advisor.color + '40'
        : '0 2px 10px rgba(0,0,0,0.3)',
      background: 'var(--bg-card)',
    }}
      onMouseEnter={e => { if (!isSelected) { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 6px 24px rgba(0,0,0,0.35)' }}}
      onMouseLeave={e => { if (!isSelected) { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 2px 10px rgba(0,0,0,0.3)' }}}
    >
      {/* Photo fill */}
      <div style={{ position: 'absolute', inset: 0, background: advisor.color + '28' }}>
        {advisor.avatar && !imgErr ? (
          <img src={advisor.avatar} alt={advisor.name} onError={() => setImgErr(true)}
            style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center 20%', display: 'block' }} />
        ) : (
          <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 52, fontWeight: 900, color: advisor.color, opacity: 0.4, letterSpacing: '-0.04em' }}>
            {advisor.name.split(' ').map(w => w[0]).join('').slice(0,2).toUpperCase()}
          </div>
        )}
      </div>

      {/* Gradient overlay */}
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top, rgba(6,8,15,0.92) 0%, rgba(6,8,15,0.4) 45%, transparent 75%)' }} />

      {/* Color accent top */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: advisor.color, opacity: isSelected ? 1 : 0.7 }} />

      {/* Bottom name/role */}
      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: '12px 14px' }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: '#fff', letterSpacing: '-0.02em', marginBottom: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{advisor.name}</div>
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.65)', lineHeight: 1.3, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{advisor.role}</div>
      </div>

      {/* Org jump */}
      {hasOrg && (
        <button onClick={e => { e.stopPropagation(); document.getElementById('org-' + advisor.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}
          style={{ position: 'absolute', top: 10, right: 10, display: 'flex', alignItems: 'center', gap: 3, padding: '3px 8px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.25)', background: 'rgba(0,0,0,0.45)', cursor: 'pointer', color: '#fff', fontSize: 10, fontWeight: 600, backdropFilter: 'blur(6px)' }}>
          <OrgIcon color="#fff" /> Org
        </button>
      )}
    </div>
  )
}

/* ── KAI hero card ───────────────────────────────────────────────────────── */
function KaiCard({ advisor, isSelected, onClick }) {
  const [imgErr, setImgErr] = useState(false)
  return (
    <div onClick={onClick} style={{
      aspectRatio: '3 / 1',
      borderRadius: 14,
      overflow: 'hidden',
      position: 'relative',
      cursor: 'pointer',
      border: '2px solid ' + (isSelected ? '#6366f1' : 'transparent'),
      transition: 'transform 0.18s, box-shadow 0.18s, border-color 0.18s',
      transform: isSelected ? 'translateY(-3px)' : 'translateY(0)',
      boxShadow: isSelected ? '0 12px 48px #6366f150' : '0 4px 20px rgba(0,0,0,0.35)',
      background: 'var(--bg-card)',
    }}
      onMouseEnter={e => { if (!isSelected) { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 8px 32px rgba(0,0,0,0.4)' }}}
      onMouseLeave={e => { if (!isSelected) { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 4px 20px rgba(0,0,0,0.35)' }}}
    >
      <div style={{ position: 'absolute', inset: 0, background: '#0d1b2a' }}>
        {advisor.avatar && !imgErr ? (
          <img src={advisor.avatar} alt={advisor.name} onError={() => setImgErr(true)}
            style={{ position: 'absolute', width: '80%', height: '80%', top: '10%', left: '10%', objectFit: 'contain', objectPosition: 'center center', display: 'block' }} />
        ) : (
          <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 80, fontWeight: 900, color: '#6366f1', opacity: 0.25, letterSpacing: '-0.06em' }}>KAI</div>
        )}
      </div>
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top, rgba(13,27,42,0.98) 0%, rgba(13,27,42,0.3) 50%, transparent 80%)' }} />
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: '#6366f1', opacity: isSelected ? 1 : 0.8 }} />
      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: '22px 24px' }}>
        <div style={{ fontSize: 26, fontWeight: 900, color: '#fff', letterSpacing: '-0.04em', marginBottom: 4 }}>{advisor.name}</div>
        <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.65)', marginBottom: 10 }}>{advisor.role}</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <Badge {...(STATUS_BADGE[advisor.status] || STATUS_BADGE.active)} />
          {advisor.sidekick_enabled && <Badge label="SideKick" bg="#6366f118" color="#a5b4fc" border="#6366f130" />}
        </div>
      </div>
      {HAS_ORG.includes('kai') && (
        <button onClick={e => { e.stopPropagation(); document.getElementById('org-kai')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}
          style={{ position: 'absolute', top: 14, right: 14, display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 7, border: '1px solid rgba(255,255,255,0.25)', background: 'rgba(0,0,0,0.45)', cursor: 'pointer', color: '#fff', fontSize: 11, fontWeight: 600, backdropFilter: 'blur(6px)' }}>
          <OrgIcon color="#fff" /> Team
        </button>
      )}
    </div>
  )
}

/* ── Row drop-down detail panel ──────────────────────────────────────────── */
function AssetsTab({ advisor }) {
  const [assets, setAssets] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  useEffect(() => {
    fetch(API + '/advisors/' + advisor.id + '/assets').then(r => r.json()).then(d => setAssets(d.assets || {})).catch(() => setAssets({}))
  }, [advisor.id])
  async function save() {
    setSaving(true)
    try {
      const r = await fetch(API + '/advisors/' + advisor.id + '/assets', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(assets) })
      if (r.ok) { setSaved(true); setTimeout(() => setSaved(false), 2500) }
    } catch(e) {}
    setSaving(false)
  }
  if (!assets) return <div style={{ padding: '20px 0', color: 'var(--text-tertiary)', fontSize: 13 }}>Loading…</div>
  const F = ({ label, k, type, opts }) => (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 5 }}>{label}</div>
      {type === 'select' ? (
        <select value={assets[k]||''} onChange={e => setAssets(a=>({...a,[k]:e.target.value}))}
          style={{ width:'100%', background:'var(--bg-input)', color:'var(--text-primary)', border:'1px solid var(--border)', borderRadius:7, padding:'7px 10px', fontSize:12, fontFamily:'inherit', outline:'none' }}>
          {opts.map(o=><option key={o} value={o}>{o}</option>)}
        </select>
      ) : type === 'toggle' ? (
        <div onClick={() => setAssets(a=>({...a,[k]:!a[k]}))} style={{ display:'inline-flex', alignItems:'center', gap:8, cursor:'pointer', padding:'5px 10px', borderRadius:7, border:'1px solid var(--border)', background: assets[k] ? advisor.color+'18' : 'var(--bg-input)' }}>
          <div style={{ width:30, height:16, borderRadius:8, background: assets[k] ? advisor.color : 'var(--bg-muted)', position:'relative', transition:'background 0.2s' }}>
            <div style={{ position:'absolute', top:2, left: assets[k] ? 14 : 2, width:12, height:12, borderRadius:'50%', background:'#fff', transition:'left 0.2s' }}/>
          </div>
          <span style={{ fontSize:12, color: assets[k] ? 'var(--text-primary)' : 'var(--text-tertiary)' }}>{assets[k] ? 'Enabled' : 'Disabled'}</span>
        </div>
      ) : (
        <input type="text" value={assets[k]||''} onChange={e => setAssets(a=>({...a,[k]:e.target.value}))} placeholder={'Enter ' + label.toLowerCase() + '…'}
          style={{ width:'100%', background:'var(--bg-input)', color:'var(--text-primary)', border:'1px solid var(--border)', borderRadius:7, padding:'7px 10px', fontSize:12, fontFamily:'inherit', outline:'none', boxSizing:'border-box' }}/>
      )}
    </div>
  )
  return (
    <div>
      <F label="Status"         k="status"        type="select" opts={['active','spec_needed']} />
      <F label="Default Model"  k="default_model" type="select" opts={MODEL_OPTIONS} />
      <F label="Research Model" k="research_model" type="select" opts={MODEL_OPTIONS} />
      <F label="HeyGen Avatar ID"    k="heygen_id" />
      <F label="ElevenLabs Voice ID" k="elevenlabs_id" />
      <F label="SideKick" k="sidekick_enabled" type="toggle" />
      <div style={{ display:'flex', alignItems:'center', gap:10, marginTop:6 }}>
        <button onClick={save} disabled={saving} style={{ padding:'7px 18px', borderRadius:7, border:'none', background:advisor.color, color:'#fff', fontSize:12, fontWeight:600, cursor:'pointer', fontFamily:'inherit' }}>{saving?'Saving…':'Save'}</button>
        {saved && <span style={{ fontSize:11, color:'#10b981' }}>✓ Saved</span>}
      </div>
    </div>
  )
}

function RowDetail({ advisor, onClose }) {
  const [tab, setTab] = useState('persona')
  const [content, setContent] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setTab('persona'); setEditing(false); setSaved(false); setContent('')
    fetch(API + '/advisors/' + advisor.id).then(r=>r.json()).then(d=>setContent(d.content||'')).catch(()=>setContent(''))
  }, [advisor.id])

  async function savePersona() {
    setSaving(true)
    try {
      const r = await fetch(API + '/advisors/' + advisor.id, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content}) })
      if (r.ok) { setSaved(true); setEditing(false); setTimeout(()=>setSaved(false),2500) }
    } catch(e) {}
    setSaving(false)
  }

  const TABS = ['persona','assets','team']

  return (
    <div style={{
      marginTop: 8, marginBottom: 4,
      border: '1px solid ' + advisor.color + '40',
      borderTop: '2px solid ' + advisor.color,
      borderRadius: 12,
      background: 'var(--bg-card)',
      overflow: 'hidden',
      animation: 'dropIn 0.35s cubic-bezier(0.16, 1, 0.3, 1)',
    }}>
      <style>{`@keyframes dropIn { from { opacity: 0; transform: translateY(-5px) } to { opacity: 1; transform: translateY(0) } }`}</style>

      {/* Header */}
      <div style={{ padding: '14px 20px', display: 'flex', alignItems: 'center', gap: 12, borderBottom: '1px solid var(--border)' }}>
        <Avatar advisor={advisor} size={40} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>{advisor.name}</div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{advisor.role}</div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <Badge {...(STATUS_BADGE[advisor.status] || STATUS_BADGE.spec_needed)} />
          {advisor.sidekick_enabled && <Badge label="SideKick" bg="#6366f118" color="#a5b4fc" border="#6366f130" />}
        </div>
        <button onClick={onClose} style={{ background:'none', border:'none', cursor:'pointer', color:'var(--text-tertiary)', fontSize:20, padding:'2px 8px', lineHeight:1, marginLeft:8 }}>×</button>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', padding: '0 20px' }}>
        {TABS.map(t => (
          <button key={t} onClick={()=>setTab(t)} style={{ padding:'8px 14px', background:'none', border:'none', cursor:'pointer', fontFamily:'inherit', fontSize:11, fontWeight:600, letterSpacing:'0.02em', textTransform:'capitalize', color: tab===t ? advisor.color : 'var(--text-tertiary)', borderBottom:'2px solid ' + (tab===t ? advisor.color : 'transparent'), transition:'all 0.15s', marginBottom:-1 }}>{t}</button>
        ))}
        {tab === 'persona' && (
          <div style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:8 }}>
            {saved && <span style={{ fontSize:11, color:'#10b981' }}>✓ Saved</span>}
            {editing ? (
              <>
                <button onClick={()=>setEditing(false)} style={{ padding:'4px 10px', borderRadius:6, border:'1px solid var(--border)', background:'none', cursor:'pointer', fontSize:11, color:'var(--text-secondary)', fontFamily:'inherit' }}>Cancel</button>
                <button onClick={savePersona} disabled={saving} style={{ padding:'4px 10px', borderRadius:6, border:'none', background:advisor.color, cursor:'pointer', fontSize:11, color:'#fff', fontWeight:600, fontFamily:'inherit' }}>{saving?'Saving…':'Save'}</button>
              </>
            ) : (
              <button onClick={()=>setEditing(true)} style={{ padding:'4px 10px', borderRadius:6, border:'1px solid var(--border)', background:'none', cursor:'pointer', fontSize:11, color:'var(--text-secondary)', fontFamily:'inherit' }}>Edit</button>
            )}
          </div>
        )}
      </div>

      {/* Content */}
      <div style={{ padding: '18px 24px', maxHeight: 320, overflowY: 'auto' }}>
        {tab === 'persona' && (editing
          ? <textarea value={content} onChange={e=>setContent(e.target.value)} style={{ width:'100%', minHeight:220, background:'var(--bg-screen)', color:'var(--text-primary)', border:'1px solid var(--border)', borderRadius:8, outline:'none', resize:'vertical', fontFamily:'monospace', fontSize:12, lineHeight:1.7, padding:'12px 14px', boxSizing:'border-box' }}/>
          : content
            ? <pre style={{ whiteSpace:'pre-wrap', wordBreak:'break-word', fontSize:13, lineHeight:1.75, color:'var(--text-primary)', margin:0, fontFamily:'inherit' }}>{content}</pre>
            : <div style={{ color:'var(--text-subtle)', fontSize:13, fontStyle:'italic' }}>No persona file. Click Edit to create one.</div>
        )}
        {tab === 'assets' && <AssetsTab advisor={advisor} />}
        {tab === 'team' && (
          <div>
            <p style={{ margin:'0 0 12px', fontSize:12, color:'var(--text-tertiary)' }}>Scroll down to see the full specialist bench for {advisor.name}.</p>
            <button onClick={()=>{ document.getElementById('org-' + advisor.id)?.scrollIntoView({behavior:'smooth',block:'start'}) }} style={{ padding:'7px 14px', borderRadius:7, border:'1px solid ' + advisor.color + '40', background:advisor.color+'15', color:advisor.color, fontSize:12, fontWeight:600, cursor:'pointer', fontFamily:'inherit' }}>Jump to {advisor.name} Org ↓</button>
          </div>
        )}
      </div>
    </div>
  )
}

/* ── Card grid with row-drop expansion ───────────────────────────────────── */
function CardGrid({ advisors, selected, onSelect, isExec = false, cols = 3 }) {
  const rows = []
  for (let i = 0; i < advisors.length; i += cols) {
    rows.push(advisors.slice(i, i + cols))
  }
  return (
    <>
      {rows.map((row, rowIdx) => {
        const hit = row.find(a => a.id === selected?.id)
        return (
          <div key={rowIdx} style={{ marginBottom: hit ? 0 : 10 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(' + cols + ', 1fr)', gap: 10 }}>
              {row.map(a => (
                <AdvisorCard key={a.id} advisor={a} isSelected={selected?.id === a.id} onClick={() => onSelect(a)} isExec={isExec} />
              ))}
              {row.length < cols && Array(cols - row.length).fill(null).map((_, i) => <div key={'pad'+i} />)}
            </div>
            {hit && <RowDetail advisor={hit} onClose={() => onSelect(hit)} />}
          </div>
        )
      })}
    </>
  )
}

/* ── Specialist card ─────────────────────────────────────────────────────── */
function SpecCard({ spec, color }) {
  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', transition: 'all 0.15s' }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = color + '40'; e.currentTarget.style.transform = 'translateY(-1px)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = 'none' }}
    >
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 3 }}>{spec.name}</div>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.4 }}>{spec.domain}</div>
      {!spec.has_persona && <div style={{ marginTop: 7 }}><Badge label="Spec Needed" bg="#f59e0b18" color="#fbbf24" border="#f59e0b30" /></div>}
    </div>
  )
}

/* ── Org section ─────────────────────────────────────────────────────────── */
function OrgSection({ advisor, teamData }) {
  const team = teamData[advisor.id] || []
  if (!team.length) return null
  return (
    <div id={'org-' + advisor.id} style={{ marginBottom: 36, scrollMarginTop: 20 }}>
      <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:14 }}>
        <Avatar advisor={advisor} size={28} />
        <div style={{ width:2, height:20, borderRadius:1, background:advisor.color }} />
        <span style={{ fontSize:13, fontWeight:800, color:'var(--text-primary)', letterSpacing:'-0.02em' }}>{advisor.name}</span>
        <span style={{ fontSize:11, color:'var(--text-tertiary)' }}>— {advisor.role}</span>
        <span style={{ marginLeft:'auto', fontSize:11, color:'var(--text-subtle)', background:'var(--bg-muted)', padding:'2px 8px', borderRadius:10 }}>{team.length} specialists</span>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(180px, 1fr))', gap:10 }}>
        {team.map(spec => <SpecCard key={spec.id} spec={spec} color={advisor.color} />)}
      </div>
    </div>
  )
}

/* ── Main page ───────────────────────────────────────────────────────────── */
export default function Advisors() {
  const [advisors, setAdvisors]   = useState(null)
  const [selected, setSelected]   = useState(null)
  const [teamData, setTeamData]   = useState({})

  useEffect(() => {
    fetch(API + '/advisors').then(r=>r.json()).then(d=>setAdvisors(d.advisors||[])).catch(()=>setAdvisors([]))
  }, [])

  useEffect(() => {
    const dirs = ['kai','creative','dev','devops','doc','coach']
    Promise.all(dirs.map(id => fetch(API + '/advisors/' + id + '/team').then(r=>r.json()).catch(()=>({team:[]}))))
      .then(results => {
        const map = {}
        dirs.forEach((id,i) => { map[id] = results[i].team || [] })
        setTeamData(map)
      })
  }, [])

  const byId = id => advisors?.find(a => a.id === id)
  const select = a => setSelected(s => s?.id === a.id ? null : a)

  const councilAdvisors = COUNCIL.map(id => byId(id)).filter(Boolean)
  const execAdvisors    = EXEC_TEAM.map(id => byId(id)).filter(Boolean)
  const orgAdvisors     = HAS_ORG.map(id => byId(id)).filter(a => a && (teamData[a.id]||[]).length)
  const kai = byId('kai')

  return (
    <div style={{ height:'100%', display:'flex', flexDirection:'column' }}>
      <div style={{ padding:'16px 24px 0', flexShrink:0 }}>
        <div style={{ display:'flex', alignItems:'baseline', gap:10, marginBottom:3 }}>
          <h1 style={{ margin:0, fontSize:20, fontWeight:800, color:'var(--text-primary)', letterSpacing:'-0.03em' }}>The Team</h1>
          {advisors && <span style={{ fontSize:12, color:'var(--text-tertiary)' }}>{advisors.length} members</span>}
        </div>
        <p style={{ margin:'0 0 14px', fontSize:12, color:'var(--text-tertiary)' }}>Council, executive team and specialist bench</p>
      </div>
      <div style={{ height:1, background:'var(--border)', flexShrink:0 }} />

      <div style={{ flex:1, overflowY:'auto', padding:'20px 24px' }}>
        {!advisors ? <div style={{ color:'var(--text-tertiary)', fontSize:13 }}>Loading…</div> : (
          <>
            {/* KAI hero */}
            {kai && (
              <div style={{ marginBottom: 20 }}>
                <KaiCard advisor={kai} isSelected={selected?.id === 'kai'} onClick={() => select(kai)} />
                {selected?.id === 'kai' && <RowDetail advisor={kai} onClose={() => select(kai)} />}
              </div>
            )}

            {/* Council */}
            <div style={{ fontSize:10, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.1em', color:'var(--text-subtle)', marginBottom:10 }}>The Council</div>
            <div style={{ marginBottom: 28 }}>
              <CardGrid advisors={councilAdvisors} selected={selected} onSelect={select} />
            </div>

            {/* Exec team */}
            <div style={{ fontSize:10, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.1em', color:'var(--text-subtle)', marginBottom:10 }}>Executive Team</div>
            <div style={{ marginBottom: 36 }}>
              <CardGrid advisors={execAdvisors} selected={selected} onSelect={select} isExec={true} cols={4} />
            </div>

            {/* Org sections */}
            {orgAdvisors.length > 0 && (
              <>
                <div style={{ height:1, background:'var(--border)', marginBottom:28 }} />
                <div style={{ fontSize:10, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.1em', color:'var(--text-subtle)', marginBottom:20 }}>Specialist Bench</div>
                {orgAdvisors.map(a => <OrgSection key={a.id} advisor={a} teamData={teamData} />)}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
