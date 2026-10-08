import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: (string | undefined | null | false)[]) {
  return twMerge(clsx(inputs));
}

export function Card({ className, children }: { className?: string, children: React.ReactNode }) {
  return (
    <div className={cn("bg-surface border border-border rounded-lg shadow-sm overflow-hidden", className)}>
      {children}
    </div>
  );
}

export function CardHeader({ title, subtitle, action }: { title: string, subtitle?: string, action?: React.ReactNode }) {
  return (
    <div className="px-5 py-4 border-b border-border flex justify-between items-start">
      <div>
        <h3 className="text-base font-semibold text-text-primary">{title}</h3>
        {subtitle && <p className="text-sm text-text-muted mt-1">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function CardContent({ className, children }: { className?: string, children: React.ReactNode }) {
  return (
    <div className={cn("p-5", className)}>
      {children}
    </div>
  );
}
