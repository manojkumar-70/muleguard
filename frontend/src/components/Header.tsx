import React from 'react';

export function Header() {
  return (
    <header className="h-16 bg-surface border-b border-border flex items-center justify-between px-6 shrink-0">
      <h2 className="text-sm font-semibold tracking-wide text-text-muted uppercase">Financial Threat Intelligence Console</h2>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-intel animate-pulse" />
          <span className="text-xs text-text-muted font-medium">System Online</span>
        </div>
      </div>
    </header>
  );
}
