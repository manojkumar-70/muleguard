import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { DashboardLayout } from './layouts/DashboardLayout';
import { CommandCenter } from './pages/CommandCenter';

// Placeholder pages for now
const LiveDetection = () => <div className="p-4 text-text-muted">Live Detection - Comming soon</div>;
const Investigations = () => <div className="p-4 text-text-muted">Investigations - Comming soon</div>;
const NetworkGraph = () => <div className="p-4 text-text-muted">Network Graph - Comming soon</div>;
const Alerts = () => <div className="p-4 text-text-muted">Alerts - Comming soon</div>;
const Reviews = () => <div className="p-4 text-text-muted">Reviews - Comming soon</div>;
const SystemStatus = () => <div className="p-4 text-text-muted">System Status - Comming soon</div>;

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
