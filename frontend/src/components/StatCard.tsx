import React from 'react';
import { Card, CardContent } from './Card';
import { LucideIcon } from 'lucide-react';

interface StatCardProps {
  title: string;
  value: string | number;
  icon: LucideIcon;
  trend?: string;
  trendUp?: boolean;
}

export function StatCard({ title, value, icon: Icon, trend, trendUp }: StatCardProps) {
  return (
    <Card>
      <CardContent className="flex items-center p-6">
        <div className="p-3 rounded bg-elevated text-intel">
          <Icon className="w-6 h-6" />
        </div>
        <div className="ml-4 flex-1">
          <p className="text-sm font-medium text-text-muted">{title}</p>
          <div className="flex items-baseline gap-2">
            <h3 className="text-2xl font-bold text-text-primary mt-1">{value}</h3>
            {trend && (
              <span className={`text-xs font-medium ${trendUp ? 'text-threat' : 'text-green-400'}`}>
                {trend}
              </span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
