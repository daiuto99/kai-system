import { useState, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Chat from './pages/Chat'
import Today from './pages/Today'
import Harmony from './pages/Harmony'
import ParkingLot from './pages/ParkingLot'
import Insights from './pages/Insights'
import Tasks from './pages/Tasks'
import More from './pages/More'

export default function App() {
  const [dark, setDark] = useState(() => {
    return localStorage.getItem('kai-theme') !== 'light'
  })

  useEffect(() => {
    document.documentElement.classList.toggle('light', !dark)
    localStorage.setItem('kai-theme', dark ? 'dark' : 'light')
  }, [dark])

  return (
    <div className={dark ? '' : 'light'}>
      <Layout dark={dark} onToggleTheme={() => setDark(d => !d)}>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/chat/:advisorId" element={<Chat />} />
          <Route path="/today" element={<Today />} />
          <Route path="/harmony" element={<Harmony />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/parking-lot" element={<ParkingLot />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/more" element={<More />} />
        </Routes>
      </Layout>
    </div>
  )
}
