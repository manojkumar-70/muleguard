import type { LucideIcon } from 'lucide-react';
import { Metric } from './Ui';

interface StatCardProps {
  title: string;
  value: string | number;
  icon: LucideIcon;
  tone?: 'neutral' | 'threat' | 'intel';
  detail?: string;
}

/** Thin compat wrapper around Metric — kept for import compatibility. */
export function StatCard({ title, value, icon, tone = 'neutral', detail }: StatCardProps) {
  return <Metric label={title} value={value} icon={icon} tone={tone} detail={detail} />;
}
