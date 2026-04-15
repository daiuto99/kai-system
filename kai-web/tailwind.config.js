/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        kai: {
          // ── Light / Command Center (exact from reference) ──
          'bg':           '#f8f9fa',
          'surface':      '#ffffff',
          'inset':        '#fafbfc',
          'border':       '#e8ecf1',
          'border-sub':   'rgba(0,0,0,0.04)',
          'text':         '#1f2937',
          'muted':        '#6b7280',
          'subtle':       '#9ca3af',
          // Accent — terracotta
          'accent':       '#c2410c',
          'accent-deep':  '#9a3412',
          'accent-pale':  '#fff7ed',
          'title':        '#7c2d12',
          // Status
          'green':        '#10b981',
          'green-dim':    'rgba(16,185,129,0.12)',
          'yellow':       '#f59e0b',
          'yellow-dim':   'rgba(245,158,11,0.12)',
          'red':          '#ef4444',
          'red-dim':      'rgba(239,68,68,0.12)',
          // ── Dark / Chat ──
          'dark-bg':      '#060E1F',
          'dark-card':    '#0D1829',
          'dark-card2':   '#112236',
          'dark-border':  'rgba(255,255,255,0.08)',
          'dark-divider': 'rgba(255,255,255,0.12)',
          'blue':         '#3882F6',
        }
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
      },
      fontSize: {
        'greeting': ['22px', { fontWeight: '300', letterSpacing: '-0.02em', lineHeight: '1.3' }],
        'section':  ['14px', { fontWeight: '600', letterSpacing: '0.05em', lineHeight: '1' }],
        'item':     ['13px', { lineHeight: '1.4' }],
        'badge':    ['10px', { fontWeight: '600', letterSpacing: '0.04em', lineHeight: '1' }],
      },
      borderRadius: {
        'card':  '20px',
        'inner': '16px',
        'item':  '10px',
        'badge': '5px',
      },
      boxShadow: {
        'card':   '0 4px 20px rgba(0,0,0,0.06)',
        'card-sm':'0 2px 12px rgba(0,0,0,0.04)',
        'accent': '0 2px 8px rgba(194,65,12,0.2)',
        'accent-hover': '0 4px 12px rgba(194,65,12,0.3)',
      },
    },
  },
  plugins: [],
}
