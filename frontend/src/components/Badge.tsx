import React from 'react';
import { cn } from './Card';

type BadgeProps = {
  variant?: 'default' | 'high' | 'medium' | 'low' | 'neutral' | 'intel';
  children: React.ReactNode;
  className?: string;
};

export function Badge({ variant = 'default', children, className }: BadgeProps) {
  const variants = {
    default: 'bg-elevated text-text-primary border-border',
    high: 'bg-threat/10 text-threat border-threat/20 font-bold',
    medium: 'bg-orange-500/10 text-orange-400 border-orange-500/20 font-bold',
    low: 'bg-green-500/10 text-green-400 border-green-500/20 font-bold',
    neutral: 'bg-elevated text-text-muted border-border',
    intel: 'bg-intel/10 text-intel border-intel/20 font-bold',
  };

  return (
    <span className={cn(
      "inline-flex items-center px-2 py-0.5 rounded text-xs border tracking-wide uppercase",
      variants[variant],
      className
    )}>
      {children}
    </span>
  );
}
