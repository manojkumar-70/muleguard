import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';

export function DashboardLayout() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="app-shell">
      <Sidebar open={menuOpen} onNavigate={() => setMenuOpen(false)} />
      {menuOpen && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
      <div className="app-column">
        <Header onMenu={() => setMenuOpen((value) => !value)} />
        <div className="environment-banner">
          <span className="environment-mark" aria-hidden="true" />
          <strong>Synthetic Environment</strong>
          <span className="environment-divider" />
          <span>No Real Financial Actions</span>
          <span className="environment-spacer" />
          <span className="console-label">Financial Threat Intelligence Console</span>
        </div>
        <main className="main-content">
          <Outlet />
        </main>
        <footer className="app-footer">
          <span>Illustrative signals only. Risk indicators are not proof of criminal activity.</span>
          <span>Detect. Investigate. Explain.</span>
        </footer>
      </div>
    </div>
  );
}

