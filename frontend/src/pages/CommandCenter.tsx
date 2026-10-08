import { Activity, ArrowUpRight, Database, ShieldAlert, Users } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api } from '../api/client';
import { Badge, Metric, PageHeading, Panel, RawResponse, SourceStamp, StatePanel } from '../components/Ui';
import { useResource } from '../hooks/useResource';

export function CommandCenter() {
  const summary = useResource(api.getSummary);
  const alerts = useResource(api.getAlerts);
  const summaryData = summary.state.status === 'success' ? summary.state.data : null;
  const alertData = alerts.state.status === 'success' ? alerts.state.data : null;
  const riskCounts = summaryData?.risk.risk_level_counts;
  const riskData = riskCounts
    ? [
        { name: 'LOW', count: riskCounts.LOW ?? 0, color: '#748178' },
        { name: 'MEDIUM', count: riskCounts.MEDIUM ?? 0, color: '#a98a4a' },
        { name: 'HIGH', count: riskCounts.HIGH ?? 0, color: '#ff2a3d' },
      ]
    : [];
  const topAccounts = [...(alertData?.alerts ?? [])]
    .sort((left, right) => right.risk_score - left.risk_score)
    .slice(0, 7);

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="OPERATIONS OVERVIEW / OFFLINE DATASET"
        title="Command Center"
        description="Account-level threat signals across the currently available synthetic dataset."
        action={<SourceStamp source="OFFLINE_DATASET" />}
      />

      <div className="metric-grid">
        <Metric label="Monitored accounts" value={summaryData?.risk.account_count ?? '—'} icon={Users} detail="Offline dataset" />
        <Metric label="Active alerts" value={alertData?.count ?? '—'} icon={ShieldAlert} detail="MEDIUM + HIGH accounts" tone="threat" />
        <Metric label="High-risk accounts" value={riskCounts?.HIGH ?? '—'} icon={Activity} detail="Account-level risk" tone="threat" />
        <Metric label="Payments analyzed" value={summaryData?.transaction_count ?? '—'} icon={Database} detail="Synthetic transactions" tone="intel" />
      </div>

      {(summary.state.status === 'error' || alerts.state.status === 'error') && (
        <StatePanel
          title="Some command-center data is unavailable"
          description={[summary.state.status === 'error' && `Summary: ${summary.state.error.message}`, alerts.state.status === 'error' && `Alerts: ${alerts.state.error.message}`].filter(Boolean).join(' · ')}
          icon={Database}
          tone="error"
        />
      )}

      <div className="dashboard-grid dashboard-grid-main">
        <Panel title="Risk distribution" subtitle="Account counts by final aggregate risk level" className="panel-chart">
          {summary.state.status === 'loading' ? (
            <StatePanel title="Loading risk distribution" description="Requesting the offline analysis summary." icon={Activity} tone="loading" />
          ) : riskData.length ? (
            <div className="chart-split">
              <div className="donut-wrap">
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie data={riskData} dataKey="count" nameKey="name" innerRadius={67} outerRadius={91} paddingAngle={3} stroke="none">
                      {riskData.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                    </Pie>
                    <Tooltip contentStyle={tooltipStyle} itemStyle={{ color: '#f5f5f5' }} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="donut-center"><strong>{summaryData?.risk.account_count ?? '—'}</strong><span>accounts</span></div>
              </div>
              <div className="chart-legend">
                {riskData.map((item) => (
                  <div className="legend-row" key={item.name}>
                    <span className="legend-swatch" style={{ background: item.color }} />
                    <span>{item.name}</span>
                    <strong>{item.count.toLocaleString()}</strong>
                  </div>
                ))}
                <p className="chart-footnote">Scores are illustrative review indicators, not proof of activity.</p>
              </div>
            </div>
          ) : <StatePanel title="Risk distribution unavailable" description="The API did not return a usable risk summary." icon={Database} tone="error" />}
        </Panel>

        <Panel title="Highest-risk accounts" subtitle="Ranked by current offline account score" action={<Link className="text-link" to="/alerts">All alerts <ArrowUpRight size={14} /></Link>}>
          {alerts.state.status === 'loading' ? (
            <StatePanel title="Loading alerts" description="Requesting current flagged accounts." icon={Activity} tone="loading" />
          ) : topAccounts.length ? (
            <div className="rank-list">
              {topAccounts.slice(0, 5).map((account, index) => (
                <Link to={`/investigate?account=${encodeURIComponent(account.account_id)}`} className="rank-row" key={account.account_id}>
                  <span className="rank-index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="rank-account"><strong>{account.account_id}</strong><small>{account.risk_score.toFixed(2)} / 100</small></span>
                  <Badge tone={account.risk_level}>{account.risk_level}</Badge>
                </Link>
              ))}
            </div>
          ) : alerts.state.status === 'error' ? (
            <StatePanel title="Alerts unavailable" description={alerts.state.error.message} icon={ShieldAlert} tone="error" />
          ) : (
            <StatePanel title="No active alerts" description="No accounts currently meet the MEDIUM or HIGH review thresholds." icon={ShieldAlert} />
          )}
        </Panel>
      </div>

      <div className="dashboard-grid dashboard-grid-lower">
        <Panel title="Risk activity" subtitle="Relative scores for the highest flagged accounts; not a time series">
          {topAccounts.length ? (
            <div className="bar-chart-wrap">
              <ResponsiveContainer width="100%" height={230}>
                <BarChart data={topAccounts} layout="vertical" margin={{ top: 4, right: 20, left: 8, bottom: 4 }}>
                  <CartesianGrid stroke="#25252d" horizontal={false} />
                  <XAxis type="number" domain={[0, 100]} tick={{ fill: '#8b8b96', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey="account_id" width={128} tick={{ fill: '#a4a4ad', fontSize: 10, fontFamily: 'IBM Plex Mono' }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(value) => [`${Number(value).toFixed(2)} / 100`, 'Account risk']} />
                  <Bar dataKey="risk_score" radius={[0, 3, 3, 0]} maxBarSize={15}>
                    {topAccounts.map((account) => <Cell key={account.account_id} fill={account.risk_level === 'HIGH' ? '#ff2a3d' : account.risk_level === 'MEDIUM' ? '#a98a4a' : '#6366f1'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : <StatePanel title="No score series available" description="Risk comparisons appear when the alerts endpoint returns accounts." icon={Activity} />}
        </Panel>

        <Panel title="Detection intelligence" subtitle="How the offline account score is assembled">
          <div className="intelligence-summary">
            <div className="intelligence-mark"><Activity size={18} aria-hidden="true" /></div>
            <div><strong>Rule + graph + anomaly analysis</strong><p>Signals are combined into an account-level aggregate. Individual account evidence is available in Investigations.</p></div>
          </div>
          <div className="intelligence-meta">
            <span>Detection status</span>
            <Badge tone={summary.state.status === 'success' ? 'success' : 'warning'} dot>{summary.state.status === 'success' ? 'Summary available' : 'Unavailable'}</Badge>
          </div>
          <p className="quiet-note">The offline summary does not expose a payment-level model score or per-event streaming phase.</p>
          <Link className="button-secondary" to="/investigate">Open investigation workspace <ArrowUpRight size={15} /></Link>
        </Panel>
      </div>

      {summaryData && <RawResponse value={summaryData} />}
    </div>
  );
}

const tooltipStyle = {
  backgroundColor: '#111115',
  border: '1px solid #303039',
  borderRadius: 4,
  color: '#f5f5f5',
  fontSize: 12,
};

