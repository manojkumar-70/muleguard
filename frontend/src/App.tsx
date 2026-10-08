import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { DashboardLayout } from './layouts/DashboardLayout';
import { CommandCenter } from './pages/CommandCenter';
import { Investigations } from './pages/Investigations';
import { LiveDetection } from './pages/LiveDetection';
import { NetworkGraph } from './pages/NetworkGraph';
import { Alerts } from './pages/Alerts';
import { Reviews } from './pages/Reviews';
import { SystemStatus } from './pages/SystemStatus';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<DashboardLayout />}>
          <Route index element={<CommandCenter />} />
          <Route path="live" element={<LiveDetection />} />
          <Route path="investigate" element={<Investigations />} />
          <Route path="network" element={<NetworkGraph />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="reviews" element={<Reviews />} />
          <Route path="status" element={<SystemStatus />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
