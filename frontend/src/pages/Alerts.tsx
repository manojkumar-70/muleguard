import { useMemo, useState } from 'react';
import { AlertTriangle, ArrowDownAZ, ArrowUpDown, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, IconButton, PageHeading, Panel, SourceStamp, StatePanel } from '../components/Ui';
import { useResource } from '../hooks/useResource';
import type { RiskLevel } from '../types/api';
import { riskTone } from '../utils/presentation';

type LevelFilter = 'ALL' | RiskLevel;
type SortOrder = 'SCORE_DESC' | 'SCORE_ASC' | 'ACCOUNT_ASC';

export function Alerts() {
  const { state, refresh } = useResource(api.getAlerts);
  const [level, setLevel] = useState<LevelFilter>('ALL');
  const [sort, setSort] = useState<SortOrder>('SCORE_DESC');
  const alerts = state.status === 'success' ? state.data.alerts : [];
  const visibleAlerts = useMemo(() => {
    const filtered = alerts.filter((item) => level === 'ALL' || item.risk_level === level);
    return filtered.sort((left, right) => {
      if (sort === 'SCORE_ASC') return left.risk_score - right.risk_score;
      if (sort === 'ACCOUNT_ASC') return left.account_id.localeCompare(right.account_id);
      return right.risk_score - left.risk_score;
    });
  }, [alerts, level, sort]);

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="THREAT QUEUE / ACCOUNT ALERTS"
        title="Alerts"
        description="Offline account risk alerts returned by the analysis API. Alert records are review indicators only."
        action={<SourceStamp source="OFFLINE_DATASET" />}
      />

      <div className="alert-summary-strip">
        <div><AlertTriangle size={18} /><strong>{state.status === 'success' ? state.data.count : '—'}</strong><span>active alert accounts</span></div>
        <span className="alert-summary-note">No payment action is triggered by an alert.</span>
        <IconButton icon={RefreshCw} label="Refresh alerts" onClick={refresh} disabled={state.status === 'loading'} />
      </div>

      <Panel title="Alert queue" subtitle={state.status === 'success' ? `${visibleAlerts.length} of ${alerts.length} accounts` : 'Current response from /alerts'}>
        <div className="table-toolbar">
          <label className="filter-field"><span>Risk level</span><select value={level} onChange={(event) => setLevel(event.target.value as LevelFilter)}><option value="ALL">All levels</option><option value="HIGH">High</option><option value="MEDIUM">Medium</option><option value="LOW">Low</option></select></label>
          <label className="filter-field"><span>Sort by</span><select value={sort} onChange={(event) => setSort(event.target.value as SortOrder)}><option value="SCORE_DESC">Risk score: high to low</option><option value="SCORE_ASC">Risk score: low to high</option><option value="ACCOUNT_ASC">Account ID</option></select></label>
          <span className="table-toolbar-spacer" />
          <span className="table-meta">{state.status === 'success' ? 'Updated from API' : 'Awaiting API'}</span>
        </div>

        {state.status === 'loading' && <StatePanel title="Loading alerts" description="Requesting flagged accounts from the offline analysis API." icon={ArrowUpDown} tone="loading" />}
        {state.status === 'error' && <StatePanel title="Alerts unavailable" description={state.error.message} icon={AlertTriangle} tone="error" />}
        {state.status === 'success' && visibleAlerts.length === 0 && <StatePanel title={alerts.length ? 'No alerts match these filters' : 'No active alerts'} description={alerts.length ? 'Change the selected risk level to see more accounts.' : 'No account crossed the MEDIUM or HIGH review threshold.'} icon={AlertTriangle} />}

        {state.status === 'success' && visibleAlerts.length > 0 && (
          <div className="activity-table-wrap"><table className="data-table"><thead><tr><th>Account</th><th>Risk level</th><th>Account score</th><th>Review guidance</th><th>Investigation</th></tr></thead><tbody>
            {visibleAlerts.map((alert) => <tr key={alert.account_id} className={alert.risk_level === 'HIGH' ? 'alert-row-high' : ''}>
              <td><Link className="table-link mono" to={`/investigate?id=${encodeURIComponent(alert.account_id)}`}>{alert.account_id}</Link></td>
              <td><Badge tone={riskTone(alert.risk_level)} dot>{alert.risk_level}</Badge></td>
              <td><span className={alert.risk_level === 'HIGH' ? 'score-high' : 'score-normal'}>{alert.risk_score.toFixed(2)}</span><span className="score-denominator"> / 100</span></td>
              <td>{alert.recommendation ?? 'Review indicated'}</td>
              <td><Link className="table-link" to={`/investigate?id=${encodeURIComponent(alert.account_id)}`}>Review account <ArrowDownAZ size={13} /></Link></td>
            </tr>)}
          </tbody></table></div>
        )}
      </Panel>
    </div>
  );
}