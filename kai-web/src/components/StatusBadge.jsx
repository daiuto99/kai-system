const LABELS = { green: 'Green', yellow: 'Yellow', red: 'Red' }

export function StatusBadge({ status }) {
  return <span className={`status-${status}`}>{LABELS[status]}</span>
}

export function StatusDot({ status, size = 8 }) {
  const colors = {
    green:  'bg-kai-green',
    yellow: 'bg-kai-yellow',
    red:    'bg-kai-red',
  }
  return (
    <span
      className={`inline-block rounded-full flex-shrink-0 ${colors[status]}`}
      style={{ width: size, height: size }}
    />
  )
}

export function StatusToggle({ status, onChange }) {
  const options = ['green', 'yellow', 'red']
  const styles = {
    green:  'border-kai-green  text-kai-green  bg-kai-green-dim',
    yellow: 'border-kai-yellow text-kai-yellow bg-kai-yellow-dim',
    red:    'border-kai-red    text-kai-red    bg-kai-red-dim',
  }
  const inactive = 'border-white/10 text-white/30 hover:border-white/20 hover:text-white/50'

  return (
    <div className="flex gap-1">
      {options.map(s => (
        <button
          key={s}
          onClick={() => onChange(s)}
          className={`text-xs px-2 py-0.5 rounded-full border font-medium transition-all
            ${status === s ? styles[s] : inactive}`}
        >
          {s[0].toUpperCase()}
        </button>
      ))}
    </div>
  )
}
