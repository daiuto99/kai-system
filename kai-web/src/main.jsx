import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

// Apply saved theme before first render (prevents flash)
const saved = localStorage.getItem('kai-theme') || 'dark'
document.documentElement.dataset.theme = saved

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)

// P5 (KAI-1459) — register the service worker so KAI installs as a PWA (app-shell
// cache; live data always hits the network — see public/sw.js). Best-effort: a
// failed registration never blocks the app.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* PWA optional */ })
  })
}
