import React from 'react';
import { NavLink } from 'react-router-dom';
import { ShieldAlert, Activity, Search, Network, Bell, CheckSquare, Server } from 'lucide-react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: (string | undefined | null | false)[]) {
  return twMerge(clsx(inputs));
}

const navItems = [
  { name: 'Command Center', path: '/', icon: ShieldAlert },
  { name: 'Live Detection', path: '/live', icon: Activity },
  { name: 'Investigations', path: '/investigate', icon: Search },
  { name: 'Network Graph', path: '/network', icon: Network },
  { name: 'Alerts', path: '/alerts', icon: Bell },
  { name: 'Reviews', path: '/reviews', icon: CheckSquare },
  { name: 'System Status', path: '/status', icon: Server },
];

export function Sidebar() {
  return (
    <aside className="w-64 bg-surface border-r border-border h-full flex flex-col">
      <div className="p-6">
        <div className="flex items-center gap-3 text-text-primary">
          <ShieldAlert className="w-8 h-8 text-threat" />
          <div>
            <h1 className="font-bold text-lg tracking-tight">MuleGuard AI</h1>
            <p className="text-xs text-text-muted font-medium uppercase tracking-wider">Detect. Investigate. Explain.</p>
          </div>
        </div>
      </div>
      
      <nav className="flex-1 px-4 space-y-1 mt-4">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-3 py-2.5 rounded-md text-sm font-medium transition-colors',
                isActive 
                  ? 'bg-elevated text-intel font-semibold' 
                  : 'text-text-muted hover:text-text-primary hover:bg-elevated/50'
              )
            }
          >
            <item.icon className="w-5 h-5" />
            {item.name}
          </NavLink>
        ))}
      </nav>
      
      <div className="p-4 m-4 rounded border border-border bg-elevated/30">
        <p className="text-xs text-text-muted text-center leading-relaxed">
          <span className="text-threat font-semibold block mb-1">Synthetic Environment</span>
          No Real Financial Actions
        </p>
      </div>
    </aside>
  );
}
