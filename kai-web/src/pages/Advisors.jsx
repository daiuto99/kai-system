import React, { useState, useEffect } from 'react'

const API = '/api'

// Tier groupings derived from API data — do not hardcode

const STATUS_BADGE = {
  active:      { label: 'Active',      bg: '#10b98118', color: '#34d399', border: '#10b98130' },
  spec_needed: { label: 'Spec Needed', bg: '#f59e0b18', color: '#fbbf24', border: '#f59e0b30' },
}
const MODEL_OPTIONS = ['claude-sonnet-4-6','claude-opus-4-6','claude-haiku-4-5-20251001','gpt-4o','gpt-4o-mini','qwen2.5:3b']

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

const INTAKE_CATS = [
  { id: 'web_design', label: 'Web Design' },
  { id: 'ui_ux', label: 'UI / UX' },
  { id: 'typography', label: 'Typography' },
  { id: 'logo', label: 'Logo' },
  { id: 'marketing', label: 'Marketing' },
  { id: 'color_palette', label: 'Color Palette' },
  { id: 'tone_voice',    label: 'Tone / Voice' },
  { id: 'content_copy',  label: 'Content & Copy' },
  { id: 'positioning',   label: 'Positioning' },
]

function IntakeIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
      <path d="M6 1v6M3.5 3.5L6 1l2.5 2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <rect x="1.5" y="8.5" width="9" height="2" rx="0.5" fill="currentColor" opacity="0.7"/>
    </svg>
  )
}

function IntakeModal({ advisor, onClose }) {
  const [files, setFiles] = React.useState(null)
  const [selectedFile, setSelectedFile] = React.useState(null)
  const [stage, setStage] = React.useState('idle')
  const [verdict, setVerdict] = React.useState(null)
  const [selCats, setSelCats] = React.useState([])
  const [notes, setNotes] = React.useState('')
  const [clarifyQ, setClarifyQ] = React.useState('')
  const [clarifyIdx, setClarifyIdx] = React.useState(0)
  const [clarifyTotal, setClarifyTotal] = React.useState(0)
  const [clarifyAns, setClarifyAns] = React.useState('')
  const [summary, setSummary] = React.useState(null)
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState('')

  const color = advisor.color

  React.useEffect(() => {
    fetch(`${API}/intake/resources/${advisor.id}`)
      .then(r => r.json())
      .then(d => setFiles(d.files || []))
      .catch(() => setFiles([]))
  }, [advisor.id])

  async function startFile(file) {
    setSelectedFile(file)
    setStage('q1')
    setVerdict(null); setSelCats([]); setNotes(''); setSummary(null); setError('')
    await fetch(`${API}/intake/start/${advisor.id}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name }),
    })
  }

  async function sendReply(text) {
    setLoading(true); setError('')
    try {
      const r = await fetch(`${API}/intake/reply/${advisor.id}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      const d = await r.json()
      if (!d.ok && d.error) { setError(d.error); setLoading(false); return }
      if (d.stage === 'q2') setStage('q2')
      else if (d.stage === 'q3') setStage('q3')
      else if (d.stage === 'clarifying') {
        setClarifyQ(d.current_question); setClarifyIdx(d.question_index)
        setClarifyTotal(d.question_total); setClarifyAns(''); setStage('clarifying')
      } else if (d.stage === 'done') {
        setSummary(d.summary); setStage('done')
        fetch(`${API}/intake/resources/${advisor.id}`).then(r => r.json()).then(d => setFiles(d.files || []))
      }
    } catch(e) { setError('Network error') }
    setLoading(false)
  }

  const stageNum = { idle:0, q1:1, q2:2, q3:3, clarifying:4, done:5 }
  const stepLabels = ['File','Verdict','Category','Notes','Review','Done']

  const ext = (f) => (f.ext || '').replace('.', '').toUpperCase()
  const isImg = (f) => ['.png','.jpg','.jpeg','.gif','.webp'].includes(f.ext)
  const toggleCat = (id) => setSelCats(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])

  return (
    <div onClick={onClose} style={{ position:'fixed', inset:0, zIndex:1000, background:'rgba(0,0,0,0.72)', display:'flex', alignItems:'center', justifyContent:'center', padding:20 }}>
      <div onClick={e=>e.stopPropagation()} style={{ width:'100%', maxWidth:760, maxHeight:'88vh', display:'flex', flexDirection:'column', background:'var(--bg-card)', borderRadius:18, border:'1px solid var(--border)', borderTop:'3px solid '+color, overflow:'hidden', boxShadow:'0 32px 80px rgba(0,0,0,0.6)' }}>

        {/* Header */}
        <div style={{ padding:'16px 22px 14px', display:'flex', alignItems:'center', gap:10, borderBottom:'1px solid var(--border)', flexShrink:0 }}>
          <div style={{ width:8, height:8, borderRadius:'50%', background:color, flexShrink:0 }}/>
          <div style={{ flex:1 }}>
            <div style={{ fontSize:14, fontWeight:800, color:'var(--text-primary)', letterSpacing:'-0.02em' }}>{advisor.name} — Knowledge Intake</div>
            <div style={{ fontSize:11, color:'var(--text-tertiary)', marginTop:1 }}>Drop files into <code style={{ fontSize:10, background:'var(--bg-muted)', padding:'1px 4px', borderRadius:3 }}>~/vault/60_Council/{advisor.id}/resources/</code></div>
          </div>
          <button onClick={onClose} style={{ background:'none', border:'none', cursor:'pointer', color:'var(--text-tertiary)', fontSize:20, lineHeight:1, padding:'2px 6px' }}>×</button>
        </div>

        {/* Step bar */}
        <div style={{ display:'flex', alignItems:'center', padding:'14px 22px 10px', flexShrink:0 }}>
          {stepLabels.map((lbl,i) => {
            const cur = stageNum[stage] ?? 0
            const done = i < cur, active = i === cur
            return (
              <React.Fragment key={lbl}>
                <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:4, minWidth:46 }}>
                  <div style={{ width:24, height:24, borderRadius:'50%', display:'flex', alignItems:'center', justifyContent:'center', fontSize:10, fontWeight:700, background: done ? color : active ? color+'22' : 'var(--bg-muted)', border: active ? '2px solid '+color : done ? 'none' : '1px solid var(--border)', color: done ? '#fff' : active ? color : 'var(--text-subtle)', transition:'all 0.2s' }}>{done?'✓':i+1}</div>
                  <div style={{ fontSize:8, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.05em', color: active ? color : done ? 'var(--text-secondary)' : 'var(--text-subtle)' }}>{lbl}</div>
                </div>
                {i < stepLabels.length-1 && <div style={{ flex:1, height:2, background: i<cur ? color : 'var(--border)', transition:'background 0.3s', marginBottom:16 }}/>}
              </React.Fragment>
            )
          })}
        </div>

        {/* Body */}
        <div style={{ flex:1, overflowY:'auto', display:'grid', gridTemplateColumns:'220px 1fr', gap:0, minHeight:0 }}>
          {/* File list */}
          <div style={{ borderRight:'1px solid var(--border)', padding:'16px 16px', overflowY:'auto' }}>
            <div style={{ fontSize:9, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.08em', color:'var(--text-subtle)', marginBottom:10 }}>Unprocessed</div>
            {!files ? <div style={{ fontSize:12, color:'var(--text-tertiary)' }}>Loading…</div>
            : files.length === 0 ? (
              <div style={{ textAlign:'center', padding:'20px 10px' }}>
                <div style={{ fontSize:20, opacity:0.3, marginBottom:8 }}>📁</div>
                <div style={{ fontSize:11, color:'var(--text-tertiary)', lineHeight:1.5 }}>No files yet.</div>
              </div>
            ) : files.map(f => (
              <div key={f.name} onClick={() => { if(stage==='idle'||stage==='done') startFile(f) }} style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 12px', borderRadius:8, border:'1.5px solid '+(selectedFile?.name===f.name&&stage!=='idle' ? color : 'var(--border)'), background: selectedFile?.name===f.name&&stage!=='idle' ? color+'10' : 'transparent', cursor:'pointer', marginBottom:6, transition:'all 0.15s' }}
                onMouseEnter={e=>{ if(selectedFile?.name!==f.name) e.currentTarget.style.borderColor=color+'50' }}
                onMouseLeave={e=>{ if(selectedFile?.name!==f.name) e.currentTarget.style.borderColor='var(--border)' }}
              >
                <div style={{ width:28, height:28, borderRadius:6, flexShrink:0, background: isImg(f) ? color+'20' : 'var(--bg-muted)', border:'1px solid '+(isImg(f)?color+'30':'var(--border)'), display:'flex', alignItems:'center', justifyContent:'center', fontSize:8, fontWeight:800, color: isImg(f)?color:'var(--text-tertiary)' }}>{ext(f)}</div>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontSize:11, fontWeight:600, color:'var(--text-primary)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{f.name}</div>
                  <div style={{ fontSize:10, color:'var(--text-tertiary)' }}>{Math.round(f.size/1024)}KB</div>
                </div>
              </div>
            ))}
          </div>

          {/* Wizard */}
          <div style={{ padding:'22px 24px', overflowY:'auto' }}>
            {stage === 'idle' && (
              <div style={{ textAlign:'center', padding:'40px 0', color:'var(--text-tertiary)' }}>
                <div style={{ fontSize:32, marginBottom:10, opacity:0.25 }}>◈</div>
                <div style={{ fontSize:13, fontWeight:600, color:'var(--text-secondary)', marginBottom:6 }}>Select a file to begin</div>
                <div style={{ fontSize:12, lineHeight:1.6 }}>{advisor.name} will ask a few questions, then store your preferences in the knowledge base.</div>
              </div>
            )}

            {stage === 'q1' && (
              <div>
                <div style={{ fontSize:14, fontWeight:700, color:'var(--text-primary)', marginBottom:6 }}>Is this a reference or an avoid?</div>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:20, lineHeight:1.6 }}>Reference = direction to follow. Avoid = what not to do.</div>
                <div style={{ display:'flex', gap:10, marginBottom:24 }}>
                  {[{v:'reference',l:'Reference',i:'↑',d:'Direction to follow'},{v:'avoid',l:'Avoid',i:'✕',d:'What not to do'}].map(opt=>(
                    <div key={opt.v} onClick={()=>setVerdict(opt.v)} style={{ flex:1, padding:'16px 18px', borderRadius:10, cursor:'pointer', border:'2px solid '+(verdict===opt.v?color:'var(--border)'), background: verdict===opt.v?color+'12':'var(--bg-screen)', transition:'all 0.15s' }}
                      onMouseEnter={e=>{ if(verdict!==opt.v) e.currentTarget.style.borderColor=color+'50' }}
                      onMouseLeave={e=>{ if(verdict!==opt.v) e.currentTarget.style.borderColor='var(--border)' }}
                    >
                      <div style={{ fontSize:20, marginBottom:6, color: verdict===opt.v?color:'var(--text-tertiary)' }}>{opt.i}</div>
                      <div style={{ fontSize:13, fontWeight:700, color:'var(--text-primary)', marginBottom:2 }}>{opt.l}</div>
                      <div style={{ fontSize:11, color:'var(--text-tertiary)' }}>{opt.d}</div>
                    </div>
                  ))}
                </div>
                <button disabled={!verdict||loading} onClick={()=>sendReply(verdict)} style={{ padding:'9px 22px', borderRadius:8, border:'none', fontFamily:'inherit', background:verdict?color:'var(--bg-muted)', color:verdict?'#fff':'var(--text-subtle)', fontSize:12, fontWeight:600, cursor:verdict?'pointer':'default' }}>Continue →</button>
              </div>
            )}

            {stage === 'q2' && (
              <div>
                <div style={{ fontSize:14, fontWeight:700, color:'var(--text-primary)', marginBottom:6 }}>What category applies?</div>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:18 }}>Pick all that apply.</div>
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8, marginBottom:22 }}>
                  {INTAKE_CATS.map(cat=>{
                    const on=selCats.includes(cat.id)
                    return (
                      <div key={cat.id} onClick={()=>toggleCat(cat.id)} style={{ padding:'10px 12px', borderRadius:8, cursor:'pointer', border:'1.5px solid '+(on?color:'var(--border)'), background:on?color+'14':'var(--bg-screen)', display:'flex', alignItems:'center', gap:7, transition:'all 0.15s' }}
                        onMouseEnter={e=>{ if(!on) e.currentTarget.style.borderColor=color+'50' }}
                        onMouseLeave={e=>{ if(!on) e.currentTarget.style.borderColor='var(--border)' }}
                      >
                        <div style={{ width:12, height:12, borderRadius:3, flexShrink:0, border:'1.5px solid '+(on?color:'var(--border)'), background:on?color:'transparent', display:'flex', alignItems:'center', justifyContent:'center' }}>
                          {on&&<div style={{ width:5, height:5, borderRadius:1, background:'#fff' }}/>}
                        </div>
                        <span style={{ fontSize:11, fontWeight:600, color:on?'var(--text-primary)':'var(--text-secondary)' }}>{cat.label}</span>
                      </div>
                    )
                  })}
                </div>
                <button disabled={!selCats.length||loading} onClick={()=>sendReply(selCats.join(','))} style={{ padding:'9px 22px', borderRadius:8, border:'none', fontFamily:'inherit', background:selCats.length?color:'var(--bg-muted)', color:selCats.length?'#fff':'var(--text-subtle)', fontSize:12, fontWeight:600, cursor:selCats.length?'pointer':'default' }}>Continue →</button>
              </div>
            )}

            {stage === 'q3' && (
              <div>
                <div style={{ fontSize:14, fontWeight:700, color:'var(--text-primary)', marginBottom:6 }}>Walk me through it</div>
                <div style={{ fontSize:12, color:'var(--text-secondary)', marginBottom:16, lineHeight:1.6 }}>What specifically do you like or not like? Elements, sections, specific decisions — be direct.</div>
                <textarea value={notes} onChange={e=>setNotes(e.target.value)} autoFocus placeholder="e.g. Love the whitespace and type scale. The color palette is too saturated…"
                  style={{ width:'100%', minHeight:110, background:'var(--bg-input)', color:'var(--text-primary)', border:'1px solid var(--border)', borderRadius:8, outline:'none', resize:'vertical', fontFamily:'inherit', fontSize:12, lineHeight:1.7, padding:'10px 12px', boxSizing:'border-box', marginBottom:18 }}
                  onFocus={e=>e.target.style.borderColor=color} onBlur={e=>e.target.style.borderColor='var(--border)'}/>
                <button disabled={!notes.trim()||loading} onClick={()=>sendReply(notes)} style={{ padding:'9px 22px', borderRadius:8, border:'none', fontFamily:'inherit', background:notes.trim()?color:'var(--bg-muted)', color:notes.trim()?'#fff':'var(--text-subtle)', fontSize:12, fontWeight:600, cursor:notes.trim()?'pointer':'default' }}>{loading?`${advisor.name} is thinking…`:'Continue →'}</button>
              </div>
            )}

            {stage === 'clarifying' && (
              <div>
                <div style={{ fontSize:10, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.06em', color, marginBottom:8 }}>{advisor.name} — Follow-up {clarifyIdx+1} of {clarifyTotal}</div>
                <div style={{ fontSize:14, fontWeight:700, color:'var(--text-primary)', marginBottom:18, lineHeight:1.5 }}>{clarifyQ}</div>
                <textarea value={clarifyAns} onChange={e=>setClarifyAns(e.target.value)} autoFocus placeholder="Your answer…"
                  style={{ width:'100%', minHeight:90, background:'var(--bg-input)', color:'var(--text-primary)', border:'1px solid var(--border)', borderRadius:8, outline:'none', resize:'vertical', fontFamily:'inherit', fontSize:12, lineHeight:1.7, padding:'10px 12px', boxSizing:'border-box', marginBottom:18 }}
                  onFocus={e=>e.target.style.borderColor=color} onBlur={e=>e.target.style.borderColor='var(--border)'}/>
                <button disabled={!clarifyAns.trim()||loading} onClick={()=>{ sendReply(clarifyAns) }} style={{ padding:'9px 22px', borderRadius:8, border:'none', fontFamily:'inherit', background:clarifyAns.trim()?color:'var(--bg-muted)', color:clarifyAns.trim()?'#fff':'var(--text-subtle)', fontSize:12, fontWeight:600, cursor:clarifyAns.trim()?'pointer':'default' }}>{loading?'Saving…': clarifyIdx<clarifyTotal-1?'Next →':'Finish'}</button>
              </div>
            )}

            {stage === 'done' && summary && (
              <div>
                <div style={{ fontSize:20, marginBottom:6 }}>✓</div>
                <div style={{ fontSize:14, fontWeight:700, color:'var(--text-primary)', marginBottom:4 }}>{summary.filename} saved</div>
                <div style={{ fontSize:12, color:'var(--text-tertiary)', marginBottom:18 }}>
                  Logged as <strong style={{ color }}>{summary.verdict}</strong> · {INTAKE_CATS.filter(c=>summary.categories?.includes(c.id)).map(c=>c.label).join(', ')||'general'}
                </div>
                {['positive','negative'].map(sent => {
                  const items = summary.annotations?.[sent] || []
                  if (!items.length) return null
                  const isPos = sent==='positive'
                  return (
                    <div key={sent} style={{ marginBottom:10, background: isPos?'#10b98110':'#ef444410', border:'1px solid '+(isPos?'#10b98130':'#ef444430'), borderRadius:8, padding:'10px 14px' }}>
                      <div style={{ fontSize:9, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.06em', color: isPos?'#34d399':'#f87171', marginBottom:6 }}>{sent}</div>
                      {items.map((p,i)=><div key={i} style={{ fontSize:11, color:'var(--text-secondary)', lineHeight:1.5 }}>· {p}</div>)}
                    </div>
                  )
                })}
                <div style={{ fontSize:11, color:'var(--text-tertiary)', margin:'14px 0 18px', lineHeight:1.5 }}>
                  {advisor.name} will apply these notes in future conversations.
                  {summary.queue_remaining>0 && <span> <strong>{summary.queue_remaining} more file{summary.queue_remaining>1?'s':''}</strong> ready.</span>}
                </div>
                <div style={{ display:'flex', gap:8 }}>
                  {summary.queue_remaining>0 && <button onClick={()=>{ setStage('idle'); setSelectedFile(null); setSummary(null) }} style={{ padding:'8px 18px', borderRadius:8, border:'none', fontFamily:'inherit', background:color, color:'#fff', fontSize:12, fontWeight:600, cursor:'pointer' }}>Next File →</button>}
                  <button onClick={onClose} style={{ padding:'8px 18px', borderRadius:8, border:'1px solid var(--border)', fontFamily:'inherit', background:'none', color:'var(--text-secondary)', fontSize:12, cursor:'pointer' }}>Done</button>
                </div>
              </div>
            )}

            {error && <div style={{ marginTop:12, padding:'8px 12px', borderRadius:6, background:'#ef444415', border:'1px solid #ef444430', fontSize:11, color:'#f87171' }}>{error}</div>}

            {selectedFile && stage!=='idle' && stage!=='done' && (
              <div style={{ marginTop:20, paddingTop:14, borderTop:'1px solid var(--border)', display:'flex', alignItems:'center', justifyContent:'space-between' }}>
                <div style={{ fontSize:10, color:'var(--text-subtle)' }}>Processing: <span style={{ color:'var(--text-tertiary)', fontWeight:600 }}>{selectedFile.name}</span></div>
                <button onClick={async()=>{ await fetch(`${API}/intake/cancel/${advisor.id}`,{method:'DELETE'}); setStage('idle'); setSelectedFile(null); setError('') }} style={{ fontSize:10, color:'var(--text-subtle)', background:'none', border:'none', cursor:'pointer', fontFamily:'inherit' }}>Cancel</button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── Photo-forward portrait card ─────────────────────────────────────────── */
function AdvisorCard({ advisor, isSelected, onClick, onIntake, isExec = false }) {
  const [imgErr, setImgErr] = useState(false)
  const hasOrg = advisor.tier === 'director' || advisor.tier === 'orchestrator'

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

      {/* Intake button */}
      <button onClick={e => { e.stopPropagation(); onIntake && onIntake(advisor) }}
        title="Knowledge Intake"
        style={{ position: 'absolute', top: 10, left: 10, display: 'flex', alignItems: 'center', gap: 3, padding: '3px 8px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.25)', background: 'rgba(0,0,0,0.45)', cursor: 'pointer', color: '#fff', fontSize: 10, fontWeight: 600, backdropFilter: 'blur(6px)' }}>
        <IntakeIcon /> Intake
      </button>
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
function KaiCard({ advisor, isSelected, onClick, onIntake }) {
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
      {/* Intake button */}
      <button onClick={e => { e.stopPropagation(); onIntake && onIntake(advisor) }}
        title="Knowledge Intake"
        style={{ position: 'absolute', top: 14, left: 14, display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 7, border: '1px solid rgba(255,255,255,0.25)', background: 'rgba(0,0,0,0.45)', cursor: 'pointer', color: '#fff', fontSize: 11, fontWeight: 600, backdropFilter: 'blur(6px)' }}>
        <IntakeIcon /> Intake
      </button>
      {true && (
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
function CardGrid({ advisors, selected, onSelect, onIntake, isExec = false, cols = 3 }) {
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
                <AdvisorCard key={a.id} advisor={a} isSelected={selected?.id === a.id} onClick={() => onSelect(a)} onIntake={onIntake} isExec={isExec} />
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
function SpecCard({ spec, color, onIntake }) {
  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', transition: 'all 0.15s', position: 'relative' }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = color + '40'; e.currentTarget.style.transform = 'translateY(-1px)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = 'none' }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 6, marginBottom: 3 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{spec.name}</div>
        <button
          onClick={e => { e.stopPropagation(); onIntake && onIntake({ id: spec.id, name: spec.name, color, role: spec.domain }) }}
          title="Knowledge Intake"
          style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 3, padding: '2px 6px', borderRadius: 4, border: '1px solid ' + color + '40', background: color + '12', cursor: 'pointer', color, fontSize: 9, fontWeight: 700, letterSpacing: '0.03em' }}
        >
          <IntakeIcon /> intake
        </button>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.4 }}>{spec.domain}</div>
      {!spec.has_persona && <div style={{ marginTop: 7 }}><Badge label="Spec Needed" bg="#f59e0b18" color="#fbbf24" border="#f59e0b30" /></div>}
    </div>
  )
}

/* ── Org section ─────────────────────────────────────────────────────────── */
function OrgSection({ advisor, teamData, onIntake }) {
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
        {team.map(spec => <SpecCard key={spec.id} spec={spec} color={advisor.color} onIntake={onIntake} />)}
      </div>
    </div>
  )
}

/* ── Main page ───────────────────────────────────────────────────────────── */
export default function Advisors() {
  const [advisors, setAdvisors]   = useState(null)
  const [selected, setSelected]   = useState(null)
  const [teamData, setTeamData]   = useState({})
  const [intakeAdvisor, setIntakeAdvisor] = useState(null)

  useEffect(() => {
    fetch(API + '/advisors').then(r=>r.json()).then(d=>setAdvisors(d.advisors||[])).catch(()=>setAdvisors([]))
  }, [])

  useEffect(() => {
    if (!advisors || advisors.length === 0) return
    const dirs = advisors.filter(a => a.tier === 'director' || a.id === 'kai').map(a => a.id)
    Promise.all(dirs.map(id => fetch(API + '/advisors/' + id + '/team').then(r=>r.json()).catch(()=>({team:[]}))))
      .then(results => {
        const map = {}
        dirs.forEach((id,i) => { map[id] = results[i].team || [] })
        setTeamData(map)
      })
  }, [advisors])

  const byId = id => advisors?.find(a => a.id === id)
  const select = a => setSelected(s => s?.id === a.id ? null : a)

  const directorAdvisors = (advisors || []).filter(a => a.tier === 'director')
  const advisorTierMembers = (advisors || []).filter(a => a.tier === 'advisor')
  const orgAdvisors = (advisors || []).filter(a => (a.tier === 'director' || a.id === 'kai') && (teamData[a.id]||[]).length > 0)
  const kai = byId('kai')

  return (
    <>
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
                <KaiCard advisor={kai} isSelected={selected?.id === 'kai'} onClick={() => select(kai)} onIntake={setIntakeAdvisor} />
                {selected?.id === 'kai' && <RowDetail advisor={kai} onClose={() => select(kai)} />}
              </div>
            )}

            {/* Directors */}
            <div style={{ fontSize:10, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.1em', color:'var(--text-subtle)', marginBottom:10 }}>Directors</div>
            <div style={{ marginBottom: 28 }}>
              <CardGrid advisors={directorAdvisors} selected={selected} onSelect={select} onIntake={setIntakeAdvisor} isExec={true} cols={4} />
            </div>

            {/* Advisors */}
            <div style={{ fontSize:10, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.1em', color:'var(--text-subtle)', marginBottom:10 }}>Advisors</div>
            <div style={{ marginBottom: 36 }}>
              <CardGrid advisors={advisorTierMembers} selected={selected} onSelect={select} onIntake={setIntakeAdvisor} />
            </div>

            {/* Org sections */}
            {orgAdvisors.length > 0 && (
              <>
                <div style={{ height:1, background:'var(--border)', marginBottom:28 }} />
                <div style={{ fontSize:10, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.1em', color:'var(--text-subtle)', marginBottom:20 }}>Specialist Bench</div>
                {orgAdvisors.map(a => <OrgSection key={a.id} advisor={a} teamData={teamData} onIntake={setIntakeAdvisor} />)}
              </>
            )}
          </>
        )}
      </div>
    </div>
    {intakeAdvisor && <IntakeModal advisor={intakeAdvisor} onClose={() => setIntakeAdvisor(null)} />}
    </>
  )
}