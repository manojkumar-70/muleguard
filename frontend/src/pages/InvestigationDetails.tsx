import { Link } from 'react-router-dom';
import { ArrowUpRight, Clock3, FileSearch, Network, ShieldAlert, Sparkles } from 'lucide-react';
import { Badge, Panel, RawResponse, SourceStamp, StatePanel } from '../components/Ui';
import type {
  AccountActivity,
  DetectionResult,
  HumanReview,
  OfflineAccount,
  PaymentInvestigation,
} from '../types/api';
import {
  formatAmount,
  formatTimestamp,
  historicalSnapshotScore,
  historicalSnapshotSummary,
  readDemoEvidence,
  riskTone,
} from '../utils/presentation';

type InvestigationBundle =
  | { kind: 'account'; id: string; account: OfflineAccount | null; payments: PaymentInvestigation[]; activity: AccountActivity[] }
  | { kind: 'payment'; id: string; payment: PaymentInvestigation; detections: DetectionResult[]; reviews: HumanReview[] }
  | { kind: 'detection'; id: string; detection: DetectionResult; payment: PaymentInvestigation | null; reviews: HumanReview[] };

export function InvestigationDetails({ bundle }: { bundle: InvestigationBundle }) {
  if (bundle.kind === 'account') return <AccountCase bundle={bundle} />;
  if (bundle.kind === 'payment') return <PaymentCase bundle={bundle} />;
  return <DetectionCase bundle={bundle} />;
}

function AccountCase({ bundle }: { bundle: Extract<InvestigationBundle, { kind: 'account' }> }) {
  const { account, payments, activity } = bundle;
  return (
    <div className="case-stack">
      <div className="case-title-row">
        <div><p className="eyebrow">ACCOUNT INVESTIGATION</p><h2 className="case-id">{bundle.id}</h2></div>
        <SourceStamp source="OFFLINE_DATASET + STREAMING_PAYMENT_SERVICE" />
      </div>

      {account ? (
        <div className="account-profile-grid">
          <Panel title="Account risk profile" subtitle="Offline dataset analysis; separate from streaming payment scores." className="account-profile-panel">
            <div className="risk-profile">
              <div className={`risk-score-ring ring-${account.risk_level.toLowerCase()}`}><strong>{account.risk_score.toFixed(2)}</strong><span>/ 100</span></div>
              <Badge tone={account.risk_level}>{account.risk_level} RISK</Badge>
              <p className="mono account-profile-id">{account.account_id}</p>
              <p className="quiet-note">This is an offline account-level aggregate. It is not a payment-level model score.</p>
            </div>
            {account.recommendation && <div className="recommendation-line"><span>Recommendation label</span><strong>{account.recommendation}</strong><small>Simulated review guidance only. No account action is performed.</small></div>}
          </Panel>

          <Panel title="Detection intelligence" subtitle="Contributions returned for this offline account result." className="account-evidence-panel">
            <div className="evidence-grid">
              <OfflineSignal account={account} name="rule_based" label="Rule Engine" icon={ShieldAlert} />
              <OfflineSignal account={account} name="graph_based" label="Graph Analysis" icon={Network} />
              <OfflineSignal account={account} name="ml_anomaly" label="ML / Anomaly Detection" icon={Sparkles} />
            </div>
          </Panel>
        </div>
      ) : (
        <StatePanel title="Offline account profile not found" description="Streaming activity may still be available. The offline analysis API did not return a profile for this account." icon={FileSearch} />
      )}

      <Panel title="Account payment activity" subtitle="Read-only records from the payment investigation API." action={<SourceStamp source="STREAMING_PAYMENT_SERVICE" />}>
        {activity.length ? (
          <div className="activity-table-wrap"><table className="data-table"><thead><tr><th>Direction</th><th>Payment</th><th>Counterparty</th><th>Amount</th><th>Payment status</th><th>Risk status</th><th>Timestamp</th></tr></thead>
            <tbody>{activity.map((item) => <tr key={`${item.payment_id}-${item.direction}`}>
              <td><Badge tone={item.direction === 'OUTGOING' ? 'intel' : 'neutral'}>{item.direction}</Badge></td>
              <td><Link className="table-link mono" to={`/investigate?id=${encodeURIComponent(item.payment_id)}`}>{item.payment_id}</Link></td>
              <td className="mono">{item.counterparty_account_id}</td>
              <td>{formatAmount(item.amount_paise, item.currency)}</td>
              <td><Badge tone="neutral">{item.payment_status}</Badge></td>
              <td><RiskStatus value={item.risk_status} /></td>
              <td className="table-time">{formatTimestamp(item.created_at)}</td>
            </tr>)}</tbody></table></div>
        ) : <StatePanel title="No account activity returned" description="No payment activity was found for this account, or the streaming investigation API is unavailable." icon={Clock3} />}
      </Panel>

      <Panel title="Payment history" subtitle={`${payments.length} payment records returned`}>
        {payments.length ? <div className="activity-table-wrap"><table className="data-table"><thead><tr><th>Payment ID</th><th>Amount</th><th>Status</th><th>Risk status</th><th>Detection result</th><th>Created</th></tr></thead>
          <tbody>{payments.map((payment) => <tr key={payment.payment_id}>
            <td><Link className="table-link mono" to={`/investigate?id=${encodeURIComponent(payment.payment_id)}`}>{payment.payment_id}</Link></td>
            <td>{formatAmount(payment.amount_paise, payment.currency)}</td>
            <td><Badge tone="neutral">{payment.status}</Badge></td>
            <td><RiskStatus value={payment.risk_status} /></td>
            <td>{payment.detection_result_id ? <Link className="table-link mono" to={`/investigate?id=${encodeURIComponent(payment.detection_result_id)}`}>{payment.detection_result_id}</Link> : 'Not linked'}</td>
            <td className="table-time">{formatTimestamp(payment.created_at)}</td>
          </tr>)}</tbody></table></div> : <StatePanel title="No payments found" description="The payment service returned no payment history for this account." icon={FileSearch} />}
      </Panel>

      {account && <RawResponse value={account} />}
    </div>
  );
}

function PaymentCase({ bundle }: { bundle: Extract<InvestigationBundle, { kind: 'payment' }> }) {
  const { payment, detections, reviews } = bundle;
  return (
    <div className="case-stack">
      <div className="case-title-row">
        <div><p className="eyebrow">PAYMENT INVESTIGATION</p><h2 className="case-id">{payment.payment_id}</h2></div>
        <SourceStamp source={payment.data_source} />
      </div>
      <Panel title="Payment record" subtitle="Payment lifecycle and risk evaluation are independent states." className="payment-record-panel">
        <div className="payment-status-strip">
          <div><span>Payment status</span><Badge tone="neutral">{payment.status}</Badge></div>
          <div><span>Risk status</span><RiskStatus value={payment.risk_status} /></div>
          <p>A linked detection does not automatically change payment status or risk status.</p>
        </div>
        <div className="detail-grid">
          <Detail label="Amount" value={formatAmount(payment.amount_paise, payment.currency)} />
          <Detail label="Currency" value={payment.currency} />
          <Detail label="Timestamp" value={formatTimestamp(payment.created_at)} />
          <Detail label="Customer" value={payment.customer_id} mono />
          <Detail label="Merchant" value={payment.merchant_id} mono />
          <Detail label="Sender account" value={payment.sender_account_id} mono />
          <Detail label="Receiver account" value={payment.receiver_account_id} mono />
          <Detail label="Detection result" value={payment.detection_result_id ?? 'No result linked'} mono />
        </div>
      </Panel>

      <DetectionSection detections={detections} />

      <ReviewList paymentId={payment.payment_id} reviews={reviews} />
      <div className="case-actions"><Link className="button-secondary" to={`/reviews?payment=${encodeURIComponent(payment.payment_id)}`}>Open review workflow <ArrowUpRight size={15} /></Link></div>
      <RawResponse value={{ payment, detections, reviews }} />
    </div>
  );
}

function DetectionCase({ bundle }: { bundle: Extract<InvestigationBundle, { kind: 'detection' }> }) {
  const { detection, payment, reviews } = bundle;
  return (
    <div className="case-stack">
      <div className="case-title-row">
        <div><p className="eyebrow">DETECTION RESULT</p><h2 className="case-id">{detection.detection_result_id}</h2></div>
        <SourceStamp source={detection.data_source} />
      </div>
      <DetectionSection detections={[detection]} />
      {payment ? (
        <PaymentSummary payment={payment} />
      ) : (
        <StatePanel title="Associated payment unavailable" description="This detection response does not include a retrievable payment record." icon={FileSearch} />
      )}
      {payment && <ReviewList paymentId={payment.payment_id} reviews={reviews} />}
      <RawResponse value={{ detection, payment, reviews }} />
    </div>
  );
}

function DetectionSection({ detections }: { detections: DetectionResult[] }) {
  if (!detections.length) {
    return <Panel title="Detection evidence" subtitle="No result is currently associated with this payment."><StatePanel title="No detection result" description="The API returned no linked detection result. Risk status remains unchanged." icon={FileSearch} /></Panel>;
  }
  return (
    <div className="case-stack">
      {detections.map((result) => {
        const demo = readDemoEvidence(result);
        const historical = historicalSnapshotSummary(demo);
        const historicalScore = historicalSnapshotScore(demo);
        return (
          <section className="detection-case" key={result.detection_result_id}>
            <div className="detection-case-header">
              <div><p className="eyebrow">DETECTION INTELLIGENCE / {result.protocol}</p><h3 className="mono">{result.detection_result_id}</h3></div>
              <Badge tone={result.risk_level}>{result.risk_level}</Badge>
            </div>

            <div className="detection-score-row">
              <div><strong>{result.risk_score.toFixed(2)}</strong><span>/ 100</span></div>
              <div className="detection-score-copy">
                <strong>{demo.accountRiskScore !== null ? 'Account-level streaming score' : 'Detection result risk score'}</strong>
                {demo.accountRiskScore !== null && <span>Not a transaction-level model score</span>}
              </div>
              <span className="detection-time">{formatTimestamp(result.created_at)}</span>
            </div>

            {demo.accountRiskScore !== null && (
              <div className="final-risk-callout">
                <span>Final account risk state</span>
                <strong>{demo.accountRiskLevel ?? result.risk_level} · {demo.accountRiskScore.toFixed(2)} / 100</strong>
                <small>Current/final score from the serialized streaming explanation.</small>
              </div>
            )}

            {historical && (
              <div className="historical-callout">
                <div><span>Historical first-alert snapshot</span><Badge tone="MEDIUM">Historical</Badge></div>
                <strong>{historicalScore !== null ? `${historicalScore.toFixed(2)} / 100` : 'Score not separately exposed'}</strong>
                <p>{historical}</p>
                <small>Context at first threshold crossing only. It is not the final risk state.</small>
              </div>
            )}

            <div className="evidence-grid detection-evidence-grid">
              <DetectionSignal result={result} name="rule_based" label="Rule Engine" icon={ShieldAlert} />
              <DetectionSignal result={result} name="graph_based" label="Graph Analysis" icon={Network} />
              <DetectionSignal result={result} name="ml_anomaly" label="ML / Anomaly Detection" icon={Sparkles} />
            </div>
            <p className="disclaimer-copy">{result.disclaimer}</p>
            <RawResponse value={result.signals} label="Advanced / Raw Detection Evidence" />
          </section>
        );
      })}
    </div>
  );
}

function OfflineSignal({ account, name, label, icon: Icon }: { account: OfflineAccount; name: string; label: string; icon: typeof ShieldAlert }) {
  const signal = account.method_contributions?.[name];
  return (
    <article className="evidence-card">
      <div className="evidence-card-title"><Icon size={16} aria-hidden="true" /><h3>{label}</h3></div>
      {signal ? <>
        <div className="evidence-contribution"><span>Contribution</span><strong>{signal.contribution.toFixed(2)}</strong></div>
        <p>{signal.explanation}</p>
        <EvidenceIndicators indicators={signal.indicators ?? []} />
      </> : <p>No signal details were returned for this method.</p>}
      <span className="evidence-source">ACCOUNT-LEVEL / OFFLINE DATASET</span>
    </article>
  );
}

function DetectionSignal({ result, name, label, icon: Icon }: { result: DetectionResult; name: string; label: string; icon: typeof ShieldAlert }) {
  const demo = readDemoEvidence(result);
  const nested = isRecord(demo.streamingExplanation?.signals) ? demo.streamingExplanation.signals : null;
  const nestedSignal = nested && isRecord(nested[name]) ? nested[name] : null;
  const direct = result.signals?.[name];
  const explanation = typeof nestedSignal?.explanation === 'string'
    ? nestedSignal.explanation
    : direct?.explanation;
  const rawIndicators = nestedSignal?.indicators ?? direct?.indicators;
  const indicators = Array.isArray(rawIndicators)
    ? rawIndicators.filter(isRecord).map((item) => typeof item.explanation === 'string' ? item.explanation : typeof item.code === 'string' ? item.code : '').filter(Boolean)
    : [];

  return (
    <article className="evidence-card">
      <div className="evidence-card-title"><Icon size={16} aria-hidden="true" /><h3>{label}</h3></div>
      {explanation ? <p>{explanation}</p> : <p>This result does not include separate {label.toLowerCase()} evidence.</p>}
      {direct && <div className="evidence-contribution"><span>Returned contribution</span><strong>{direct.contribution.toFixed(2)}</strong></div>}
      {indicators.length > 0 && <ul className="evidence-indicators">{indicators.map((indicator, index) => <li key={`${index}-${indicator}`}>{indicator}</li>)}</ul>}
      {!direct && <span className="evidence-source">INDICATOR DETAILS ONLY · NO METHOD SCORE PROVIDED</span>}
    </article>
  );
}

function EvidenceIndicators({ indicators }: { indicators: { explanation: string; code?: string; rule?: string }[] }) {
  if (!indicators.length) return <p className="evidence-empty">No individual indicators returned.</p>;
  return <ul className="evidence-indicators">{indicators.map((item, index) => <li key={`${item.code ?? item.rule ?? index}-${index}`}><strong>{item.rule ?? item.code ?? 'Indicator'}</strong>{item.explanation}</li>)}</ul>;
}

function ReviewList({ paymentId, reviews }: { paymentId: string; reviews: HumanReview[] }) {
  return (
    <Panel title="Investigator review" subtitle="Append-only review records; decisions do not alter payment or risk state." action={<Link className="text-link" to={`/reviews?payment=${encodeURIComponent(paymentId)}`}>Open workflow <ArrowUpRight size={14} /></Link>}>
      {reviews.length ? <div className="review-list">{reviews.map((review) => <article className="review-row" key={review.review_id}>
        <div className="review-avatar">{review.reviewer_id.slice(-2)}</div>
        <div className="review-body"><div><strong>{review.reviewer_id}</strong><Badge tone="neutral">{review.decision.replaceAll('_', ' ')}</Badge></div><p>{review.note || 'No notes provided.'}</p><small>{review.review_id} · {formatTimestamp(review.created_at)}</small></div>
      </article>)}</div> : <StatePanel title="No reviews recorded" description="No append-only investigator reviews are associated with this payment." icon={FileSearch} />}
    </Panel>
  );
}

function PaymentSummary({ payment }: { payment: PaymentInvestigation }) {
  return (
    <Panel title="Associated payment" subtitle="Read-only payment state from the investigation API." action={<SourceStamp source={payment.data_source} />}>
      <div className="payment-status-strip"><div><span>Payment status</span><Badge tone="neutral">{payment.status}</Badge></div><div><span>Risk status</span><RiskStatus value={payment.risk_status} /></div><p>Payment status and risk status are independent.</p></div>
      <div className="detail-grid detail-grid-compact"><Detail label="Amount" value={formatAmount(payment.amount_paise, payment.currency)} /><Detail label="Customer" value={payment.customer_id} mono /><Detail label="Merchant" value={payment.merchant_id} mono /><Detail label="Created" value={formatTimestamp(payment.created_at)} /></div>
    </Panel>
  );
}

function RiskStatus({ value }: { value: string }) {
  const tone = value === 'NOT_EVALUATED' || value === 'ERROR' ? 'warning' : riskTone(value);
  return <Badge tone={tone}>{value}</Badge>;
}

function Detail({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="detail-item"><span>{label}</span><strong className={mono ? 'mono' : ''}>{value}</strong></div>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}