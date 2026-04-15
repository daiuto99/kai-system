/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        kai: {
          // Dark (Chat) profile
          'dark-bg':      '#060E1F',
          'dark-card':    '#0D1829',
          'dark-card2':   '#112236',
          'dark-border':  'rgba(255,255,255,0.08)',
          'dark-divider': 'rgba(255,255,255,0.12)',
          // Light (Command Center) profile
          'light-bg':     '#F4F3F0',
          'light-card':   '#FFFFFF',
          'light-border': '#E8E5E0',
          'light-divider':'#EAE7E2',
          'light-text':   '#1A1918',
          'light-muted':  '#6B6866',
          'light-subtle': '#9B9490',
          // Terracotta accent
          'terra':        '#C0512A',
          'terra-light':  '#D4623A',
          'terra-dim':    'rgba(192,81,42,0.10)',
          // Accents (keep for chat)
          'blue':         '#3882F6',
          'blue-dim':     '#1D4ED8',
          'blue-glow':    'rgba(56,130,246,0.15)',
          // Status
          'green':        '#10B981',
          'green-dim':    'rgba(16,185,129,0.15)',
          'yellow':       '#F59E0B',
          'yellow-dim':   'rgba(245,158,11,0.15)',
          'red':          '#EF4444',
          'red-dim':      'rgba(239,68,68,0.15)',
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Menlo', 'monospace'],
      },
      boxShadow: {
        'widget': '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)',
        'widget-hover': '0 4px 12px rgba(0,0,0,0.10)',
        'card-light': '0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06)',
      }
    },
  },
  plugins: [],
}
