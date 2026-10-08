import { ShieldAlert, Activity, GitBranch, Clock3 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, Panel, StatePanel, SourceStamp, RawResponse } from '../components/Ui';
import type { AccountActivity, OfflineAccount } from '../types/api';
import { formatAmount, formatTimestamp, riskTone } from '../utils/presentation';

/**
 * AccountInvestigation
 *
 * Renders a compact risk-profile + activity timeline for a single account.
 * Used as a sub-view inside the investigation page when an ACC- id is loaded.
 *
 * All data comes from the parent (InvestigationDetails) — no API calls here.
 */
export function AccountInvestigation({
  account,
  activity,
}: {
  account: OfflineAccount | null;
  activity: AccountActivity[];
}) {
  return (
    <div className="case-stack">
      {account ? (
        <div className="account-profile-grid">
          {/* Risk profile panel */}
          <Panel
            title="Account risk profile"
            subtitle="Offline dataset · not a streaming payment score"
            className="account-profile-panel"
          >
            <div className="risk-profile">
              <div className={`risk-score-ring ring-${account.risk_level.toLowerCase()}`}>
                <strong>{account.risk_score.toFixed(2)}</strong>
                <span>/ 100</span>
              </div>
              <Badge tone={riskTone(account.risk_level)} dot>
                {account.risk_level} RISK
              </Badge>
              <p className="mono account-profile-id">{account.account_id}</p>
              <p className="quiet-note" style={{ textAlign: 'center', maxWidth: 200 }}>
                Account-level aggregate score.
                <br />
                <em>Not a transaction-level model score.</em>
              </p>
            </div>

            {account.recommendation && (
              <div className="recommendation-line">
                <span>Recommendation</span>
                <strong>{account.recommendation}</strong>
                <small>Simulated review guidance only. No real account action is performed.</small>
              </div>
            )}
          </Panel>

          {/* Intelligence signals */}
          <Panel
            title="Detection intelligence"
            subtitle="Method contributions from the offline analysis engine"
          >
            <div className="evidence-grid">
              <OfflineSignal account={account} name="rule_based" label="Rule Engine" icon={ShieldAlert} />
              <OfflineSignal account={account} name="graph_based" label="Graph Analysis" icon={GitBranch} />
              <OfflineSignal account={account} name="ml_anomaly" label="ML / Anomaly" icon={Activity} />
            </div>
          </Panel>
        </div>
      ) : (
        <StatePanel
          title="Offline account profile not found"
          description="The offline analysis API did not return a profile for this account. Activity may still be available below."
          icon={ShieldAlert}
        />
      )}

      {/* Activity timeline */}
      <Panel
        title="Account activity"
        subtitle={`${activity.length} payment activity records`}
        action={<SourceStamp source="STREAMING_PAYMENT_SERVICE" />}
      >
        {activity.length ? (
          <div className="activity-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Direction</th>
                  <th>Payment</th>
                  <th>Counterparty</th>
                  <th>Amount</th>
                  <th>Payment status</th>
                  <th>Risk status</th>
                  <th>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {activity.map((item) => (
                  <tr key={`${item.payment_id}-${item.direction}`}>
                    <td>
                      <Badge tone={item.direction === 'OUTGOING' ? 'intel' : 'neutral'}>
                        {item.direction}
                      </Badge>
                    </td>
                    <td>
                      <Link
                        className="table-link mono"
                        to={`/investigate?id=${encodeURIComponent(item.payment_id)}`}
                      >
                        {item.payment_id}
                      </Link>
                    </td>
                    <td className="mono">{item.counterparty_account_id}</td>
                    <td>{formatAmount(item.amount_paise, item.currency)}</td>
                    <td>
                      <Badge tone="neutral">{item.payment_status}</Badge>
                    </td>
                    <td>
                      <Badge
                        tone={
                          item.risk_status === 'NOT_EVALUATED' || item.risk_status === 'ERROR'
                            ? 'warning'
                            : riskTone(item.risk_status)
                        }
                      >
                        {item.risk_status}
                      </Badge>
                    </td>
                    <td className="table-time">{formatTimestamp(item.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <StatePanel
            title="No activity returned"
            description="No payment activity was found for this account, or the streaming investigation API is unavailable."
            icon={Clock3}
          />
        )}
      </Panel>

      {account && <RawResponse value={account} />}
    </div>
  );
}

function OfflineSignal({
  account,
  name,
  label,
  icon: Icon,
}: {
  account: OfflineAccount;
  name: string;
  label: string;
  icon: typeof ShieldAlert;
}) {
  const signal = account.method_contributions?.[name];
  return (
    <article className="evidence-card">
      <div className="evidence-card-title">
        <Icon size={15} aria-hidden="true" />
        <h3>{label}</h3>
      </div>
      {signal ? (
        <>
          <div className="evidence-contribution">
            <span>Contribution</span>
            <strong>{signal.contribution.toFixed(2)}</strong>
          </div>
          <p>{signal.explanation}</p>
          {(signal.indicators ?? []).length > 0 && (
            <ul className="evidence-indicators">
              {signal.indicators.map((item, index) => (
                <li key={`${item.code ?? item.rule ?? index}-${index}`}>
                  {(item.rule ?? item.code) && <strong>{item.rule ?? item.code}</strong>}
                  {item.explanation}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p>No signal details returned for this method.</p>
      )}
      <span className="evidence-source">ACCOUNT-LEVEL / OFFLINE DATASET</span>
    </article>
  );
}
