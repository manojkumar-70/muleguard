import { useCallback, useState, type FormEvent } from 'react';
import { Activity, AlertTriangle, CircleHelp, Clock3, Radio, RefreshCw } from 'lucide-react';
import { api } from '../api/client';
import { Badge, PageHeading, Panel, RawResponse, SourceStamp, StatePanel } from '../components/Ui';
import { useResource } from '../hooks/useResource';

import { DEMO_IDS, formatAmount, formatTimestamp } from '../utils/presentation';

export function LiveDetection() {
  const [draftAccountId, setDraftAccountId] = useState<string>(DEMO_IDS.account);
  const [accountId, setAccountId] = useState('');
  const loadTimeline = useCallback(async () => {
    if (!accountId) return [];
    const payments = await api.investigation.getAccountPayments(accountId, 100);
    return Promise.all(payments.map(async (payment) => {
      if (!payment.detection_result_id) return { payment, detections: [] };
      const detections = await api.investigation.getPaymentDetections(payment.payment_id);
      return { payment, detections };
    }));
  }, [accountId]);
  const { state, refresh } = useResource(accountId ? loadTimeline : null, [accountId]);
  const events = state.status === 'success' ? [...state.data].sort(
    (left, right) => Date.parse(right.payment.created_at) - Date.parse(left.payment.created_at),
  ) : [];

  function loadAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAccountId(draftAccountId.trim());
  }

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="STREAMING PAYMENT SERVICE / ACCOUNT SNAPSHOT"
        title="Live Detection"
        description="A server snapshot of synthetic payment activity and linked detection results."
        action={accountId ? <SourceStamp source="STREAMING_PAYMENT_SERVICE" /> : undefined}
      />

      <Panel title="Event source" subtitle="The current API exposes account-scoped history, not a global event-stream endpoint." className="query-panel">
        <form className="lookup-form" onSubmit={loadAccount}>
          <label className="field-label" htmlFor="live-account">Account ID</label>
          <div className="lookup-controls">
            <input id="live-account" value={draftAccountId} onChange={(event) => setDraftAccountId(event.target.value)} placeholder="ACC-..." />
            <button className="button-primary" type="submit"><Radio size={16} />Load activity</button>
            {accountId && <button className="button-secondary button-icon-text" type="button" onClick={refresh}><RefreshCw size={15} />Refresh</button>}
          </div>
        </form>
      </Panel>

      <div className="phase-legend" aria-label="Detection event categories">
        <div><Badge tone="neutral">Warm-up</Badge><span>Per-payment warm-up labels are not returned by the current API.</span></div>
        <div><Badge tone="intel">Scored</Badge><span>Payment has a linked detection result.</span></div>
        <div><Badge tone="critical">Alert</Badge><span>Linked result is MEDIUM or HIGH.</span></div>
      </div>

      {!accountId && (
        <StatePanel title="No account selected" description="Load an account to review its synthetic payment history. Use the verified Phase 7.2 account ID or enter another account." icon={Activity} />
      )}
      {state.status === 'loading' && <StatePanel title="Loading event history" description="Requesting account payments and linked detection records." icon={RefreshCw} tone="loading" />}
      {state.status === 'error' && <StatePanel title="Event history unavailable" description={state.error.message} icon={CircleHelp} tone="error" />}
      {state.status === 'success' && events.length === 0 && <StatePanel title="No payment events found" description={`No payment history was returned for ${accountId}.`} icon={Clock3} />}

      {state.status === 'success' && events.length > 0 && (
        <Panel title="Event timeline" subtitle={`${events.length} server-returned payments · newest first`} action={<SourceStamp source="STREAMING_PAYMENT_SERVICE" />}>
          <div className="timeline-list">
            {events.map(({ payment, detections }, index) => {
              const primaryDetection = detections[0];
              const isAlert = detections.some((result) => result.risk_level === 'HIGH' || result.risk_level === 'MEDIUM');
              const phase = isAlert ? 'alert' : primaryDetection ? 'scored' : 'unscored';
              return (
                <article className={`timeline-event timeline-event-${phase}`} key={payment.payment_id} style={{ animationDelay: `${Math.min(index, 8) * 45}ms` }}>
                  <div className="timeline-rail"><span className={`timeline-node timeline-node-${phase}`} /></div>
                  <div className="timeline-content">
                    <div className="timeline-topline">
                      <div className="timeline-labels">
                        <Badge tone={phase === 'alert' ? 'critical' : phase === 'scored' ? 'intel' : 'neutral'}>
                          {phase === 'alert' ? 'Alert' : phase === 'scored' ? 'Scored' : 'No linked detection'}
                        </Badge>
                        <Badge tone="neutral">{payment.status}</Badge>
                      </div>
                      <time>{formatTimestamp(payment.created_at)}</time>
                    </div>
                    <div className="timeline-details">
                      <div><span>Payment ID</span><strong className="mono">{payment.payment_id}</strong></div>
                      <div><span>Account</span><strong className="mono">{payment.sender_account_id === accountId ? payment.sender_account_id : payment.receiver_account_id}</strong></div>
                      <div><span>Amount</span><strong>{formatAmount(payment.amount_paise, payment.currency)}</strong></div>
                      <div><span>Detection state</span><strong>{primaryDetection ? `${primaryDetection.risk_level} · ${primaryDetection.risk_score.toFixed(2)}/100` : 'No linked result'}</strong></div>
                    </div>
                    {detections.length > 1 && <p className="quiet-note">{detections.length} detection results are linked to this payment.</p>}
                  </div>
                </article>
              );
            })}
          </div>
          <div className="timeline-disclaimer"><AlertTriangle size={14} /><span>Warm-up/scored phase metadata is not exposed per payment. Unlinked payments are not assumed to be warm-up events.</span></div>
        </Panel>
      )}

      {state.status === 'success' && <RawResponse value={state.data} />}
    </div>
  );
}