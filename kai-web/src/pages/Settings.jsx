import { useState, useEffect } from 'react'

export default function Settings() {
  const [workingOn, setWorkingOn] = useState('')
  const [o365_1, setO365_1] = useState('')
  const [o365_2, setO365_2] = useState('')
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/settings')
      .then(r => r.json())
      .then(d => {
        setWorkingOn(d.working_on || '')
        setO365_1(d.o365_cal_1 || '')
        setO365_2(d.o365_cal_2 || '')
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function save() {
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ working_on: workingOn, o365_cal_1: o365_1, o365_cal_2: o365_2 }),
    })
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 2000) })
      .catch(() => {})
  }

  if (loading) return (
    <div className="flex items-center justify-center h-full">
      <p className="text-sm text-kai-light-subtle">Loading…</p>
    </div>
  )

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-8 space-y-8">

        <div>
          <h1 className="text-xl font-semibold text-kai-light-text">Settings</h1>
          <p className="text-sm text-kai-light-muted mt-1">Context and integrations for KAI</p>
        </div>

        {/* Current focus */}
        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold text-kai-light-text">What I'm working on</h2>
            <p className="text-xs text-kai-light-muted mt-0.5">
              KAI loads this at the start of every session as live context. Update it whenever your focus shifts.
            </p>
          </div>
          <textarea
            value={workingOn}
            onChange={e => setWorkingOn(e.target.value)}
            placeholder="e.g. Finalizing KAI Phase 2, preparing LaunchBox spring cohort, working through relationship tensions..."
            rows={4}
            className="w-full bg-white border border-kai-light-border rounded-xl px-4 py-3 text-sm text-kai-light-text placeholder:text-kai-light-subtle outline-none resize-none focus:border-kai-terra transition-colors"
            style={{ fontSize: '16px' }}
          />
        </section>

        {/* Calendar */}
        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold text-kai-light-text">Calendar subscriptions</h2>
            <p className="text-xs text-kai-light-muted mt-0.5">
              Paste your O365 iCal subscription URLs. KAI will merge these with your Google Calendar for context.
            </p>
          </div>
          <div className="space-y-2">
            <input
              value={o365_1}
              onChange={e => setO365_1(e.target.value)}
              placeholder="O365 Calendar 1 — iCal subscription URL"
              className="w-full bg-white border border-kai-light-border rounded-xl px-4 py-3 text-sm text-kai-light-text placeholder:text-kai-light-subtle outline-none focus:border-kai-terra transition-colors"
              style={{ fontSize: '16px' }}
            />
            <input
              value={o365_2}
              onChange={e => setO365_2(e.target.value)}
              placeholder="O365 Calendar 2 — iCal subscription URL"
              className="w-full bg-white border border-kai-light-border rounded-xl px-4 py-3 text-sm text-kai-light-text placeholder:text-kai-light-subtle outline-none focus:border-kai-terra transition-colors"
              style={{ fontSize: '16px' }}
            />
          </div>
          <p className="text-xs text-kai-light-subtle">Google Calendar OAuth setup — coming next session.</p>
        </section>

        <button
          onClick={save}
          className="px-6 py-2.5 bg-kai-terra hover:bg-kai-terra-light text-white text-sm font-medium rounded-xl transition-colors"
        >
          {saved ? 'Saved ✓' : 'Save settings'}
        </button>

      </div>
    </div>
  )
}
