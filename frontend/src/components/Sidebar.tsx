import { NavLink } from 'react-router-dom';
import {
  Activity,
  Bell,
  Boxes,
  CircleDot,
  ClipboardCheck,
  Command,
  Network,
  Search,
  ShieldAlert,
} from 'lucide-react';

const navItems = [
  { name: 'Command Center', path: '/', icon: Command, group: 'OPERATIONS' },
  { name: 'Live Detection', path: '/live', icon: Activity, group: 'OPERATIONS' },
  { name: 'Investigations', path: '/investigate', icon: Search, group: 'INTELLIGENCE' },
  { name: 'Network Graph', path: '/network', icon: Network, group: 'INTELLIGENCE' },
  { name: 'Alerts', path: '/alerts', icon: Bell, group: 'INTELLIGENCE' },
  { name: 'Reviews', path: '/reviews', icon: ClipboardCheck, group: 'GOVERNANCE' },
  { name: 'System / API Status', path: '/status', icon: Boxes, group: 'GOVERNANCE' },
];

export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  let activeGroup = '';

  return (
    <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
      <div className="brand-lockup">
        <div className="brand-icon"><ShieldAlert size={20} aria-hidden="true" /></div>
        <div>
          <p className="brand-name">MuleGuard <span>AI</span></p>
          <p className="brand-tagline">Detect. Investigate. Explain.</p>
        </div>
      </div>

      <nav className="side-navigation" aria-label="Main navigation">
        {navItems.map((item) => {
          const showGroup = activeGroup !== item.group;
          activeGroup = item.group;
          return (
            <div key={item.path}>
              {showGroup && <p className="nav-group-label">{item.group}</p>}
              <NavLink
                to={item.path}
                end={item.path === '/'}
                onClick={onNavigate}
                className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
              >
                <item.icon size={17} strokeWidth={1.8} aria-hidden="true" />
                <span>{item.name}</span>
                {item.path === '/alerts' && <CircleDot className="nav-alert-mark" size={10} aria-hidden="true" />}
              </NavLink>
            </div>
          );
        })}
      </nav>

      <div className="sidebar-bottom">
        <div className="sidebar-status-line"><span className="status-indicator status-indicator-indigo" />Local synthetic workspace</div>
        <div className="sidebar-version">RESEARCH CONSOLE <span>PHASE 7.2</span></div>
      </div>
    </aside>
  );
}

