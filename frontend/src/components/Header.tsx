import { Menu } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { api } from '../api/client';
import { useResource } from '../hooks/useResource';

const routeNames: Record<string, string> = {
  '/': 'Command Center',
  '/live': 'Live Detection',
  '/investigate': 'Investigations',
  '/network': 'Network Graph',
  '/alerts': 'Alerts',
  '/reviews': 'Reviews',
  '/status': 'System / API Status',
};

export function Header({ onMenu }: { onMenu: () => void }) {
  const { pathname } = useLocation();
  const { state } = useResource(api.getHealth);
  const reachable = state.status === 'success' && state.data.status === 'ok';

  const apiLabel =
    state.status === 'loading'
      ? 'Checking API'
      : reachable
      ? 'FastAPI reachable'
      : 'FastAPI unavailable';

  const dotClass = reachable
    ? 'status-indicator-green'
    : state.status === 'loading'
    ? 'status-indicator-indigo'
    : 'status-indicator-red';

  return (
    <header className="app-header">
      <button
        className="header-menu-button"
        type="button"
        aria-label="Open navigation"
        onClick={onMenu}
      >
        <Menu size={18} aria-hidden="true" />
      </button>

      <p className="header-title">{routeNames[pathname] ?? 'MuleGuard AI'}</p>

      <div className="header-status">
        <span className={`status-indicator ${dotClass}`} />
        {apiLabel}
      </div>
    </header>
  );
}
