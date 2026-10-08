import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import type { RiskLevel } from '../types/api';

export function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {action && <div className="page-heading-action">{action}</div>}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  action,
  className = '',
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action && <div className="panel-action">{action}</div>}
      </div>
      {children}
    </section>
  );
}

export function Badge({
  children,
  tone = 'neutral',
  dot = false,
}: {
  children: ReactNode;
  tone?: RiskLevel | 'intel' | 'neutral' | 'warning' | 'success' | 'critical';
  dot?: boolean;
}) {
  return (
    <span className={`badge badge-${tone.toLowerCase()}`}>
      {dot && <span className="badge-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

export function Metric({
  label,
  value,
  icon: Icon,
  detail,
  tone = 'neutral',
}: {
  label: string;
  value: string | number;
  icon: LucideIcon;
  detail?: string;
  tone?: 'neutral' | 'threat' | 'intel';
}) {
  return (
    <article className="metric-card">
      <div className={`metric-icon metric-icon-${tone}`}><Icon size={18} aria-hidden="true" /></div>
      <p className="metric-label">{label}</p>
      <p className="metric-value">{value}</p>
      {detail && <p className="metric-detail">{detail}</p>}
    </article>
  );
}

export function StatePanel({
  title,
  description,
  icon: Icon,
  tone = 'neutral',
  action,
}: {
  title: string;
  description: string;
  icon: LucideIcon;
  tone?: 'neutral' | 'error' | 'loading';
  action?: ReactNode;
}) {
  return (
    <div className={`state-panel state-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <Icon className={tone === 'loading' ? 'loading-icon' : ''} size={20} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{description}</p>
        {action && <div className="state-action">{action}</div>}
      </div>
    </div>
  );
}

export function SourceStamp({ source }: { source: string }) {
  return <span className="source-stamp"><span />{source}</span>;
}

export function RawResponse({ value, label = 'Advanced / Raw Response' }: { value: unknown; label?: string }) {
  return (
    <details className="raw-response">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function IconButton({
  icon: Icon,
  label,
  onClick,
  disabled = false,
  type = 'button',
}: {
  icon: LucideIcon;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  type?: 'button' | 'submit';
}) {
  return (
    <button className="icon-button" type={type} aria-label={label} title={label} onClick={onClick} disabled={disabled}>
      <Icon size={17} aria-hidden="true" />
    </button>
  );
}