import { useState, useRef, useEffect } from 'react'
import { X, Send } from 'lucide-react'
import { api } from '../lib/api'

export default function CaptureModal({ onClose }) {
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [done, setDone] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    ref.current?.focus()
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    if (!text.trim() || saving) return
    setSaving(true)
    try {
      await api.quickCapture(text.trim())
      setDone(true)
      setTimeout(onClose, 800)
    } catch {
      setSaving(false)
    }
  }

  function handleKey(e) {
    if (e.key === 'Escape') onClose()
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit(e)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center justify-center"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Sheet */}
      <div className="relative w-full md:max-w-lg md:mx-4 kai-card rounded-t-2xl md:rounded-2xl p-5 z-10"
        style={{ paddingBottom: 'max(20px, env(safe-area-inset-bottom))' }}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold">Quick Capture</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-white/8 transition-colors">
            <X size={16} className="kai-text-subtle" />
          </button>
        </div>

        {done ? (
          <div className="py-4 text-center">
            <p className="text-sm text-kai-green font-medium">Captured ✓</p>
          </div>
        ) : (
          <form onSubmit={handleSubmit}>
            <textarea
              ref={ref}
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={handleKey}
              placeholder="What do you want to capture? Paste a link, idea, or note..."
              rows={4}
              className="w-full bg-transparent text-sm resize-none outline-none kai-text-secondary placeholder:text-white/20 leading-relaxed"
            />
            <div className="flex items-center justify-between mt-3 pt-3 border-t kai-divider">
              <p className="text-xs kai-text-subtle">⌘↵ to save</p>
              <button
                type="submit"
                disabled={!text.trim() || saving}
                className="btn-primary flex items-center gap-2 disabled:opacity-40"
              >
                <Send size={13} />
                {saving ? 'Saving…' : 'Capture'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
