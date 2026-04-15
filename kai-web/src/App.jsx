import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Chat from './pages/Chat'
import Today from './pages/Today'
import Harmony from './pages/Harmony'
import ParkingLot from './pages/ParkingLot'
import Insights from './pages/Insights'
import Tasks from './pages/Tasks'
import More from './pages/More'
import Settings from './pages/Settings'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/today" replace />} />
        <Route path="/today" element={<Today />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/chat/:advisorId" element={<Chat />} />
        <Route path="/harmony" element={<Harmony />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/parking-lot" element={<ParkingLot />} />
        <Route path="/insights" element={<Insights />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/more" element={<More />} />
      </Routes>
    </Layout>
  )
}
