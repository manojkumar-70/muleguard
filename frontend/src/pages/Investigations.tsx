import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CircleHelp, Search } from 'lucide-react';
import { api } from '../api/client';
import { Badge, PageHeading, Panel, StatePanel } from '../components/Ui';
import { useResource } from '../hooks/useResource';
import type {
  AccountActivity,
  DetectionResult,
  HumanReview,
  OfflineAccount,
  PaymentInvestigation,
} from '../types/api';
import { DEMO_IDS } from '../utils/presentation';
import { InvestigationDetails } from './InvestigationDetails';

type InvestigationBundle =
  | { kind: 'account'; id: string; account: OfflineAccount | null; payments: PaymentInvestigation[]; activity: AccountActivity[] }
  | { kind: 'payment'; id: string; payment: PaymentInvestigation; detections: DetectionResult[]; reviews: HumanReview[] }
  | { kind: 'detection'; id: string; detection: DetectionResult; payment: PaymentInvestigation | null; reviews: HumanReview[] };

async function loadInvestigation(id: string): Promise<InvestigationBundle> {
  if (id.startsWith('ACC-')) {
    const [accountResult, paymentsResult, activityResult] = await Promise.allSettled([
      api.getAccount(id),
      api.investigation.getAccountPayments(id),
      api.investigation.getAccountActivity(id),
    ]);
    if (accountResult.status === 'rejected' && paymentsResult.status === 'rejected' && activityResult.status === 'rejected') {
      throw accountResult.reason;
    }
    return {
      kind: 'account',
      id,
      account: accountResult.status === 'fulfilled' ? accountResult.value : null,
      payments: paymentsResult.status === 'fulfilled' ? paymentsResult.value : [],
      activity: activityResult.status === 'fulfilled' ? activityResult.value : [],
    };
  }

  if (id.startsWith('PAY-')) {
    const [paymentResult, detectionsResult, reviewsResult] = await Promise.allSettled([
      api.investigation.getPayment(id),
      api.investigation.getPaymentDetections(id),
      api.investigation.getPaymentReviews(id),
    ]);
    if (paymentResult.status === 'rejected') throw paymentResult.reason;
    return {
      kind: 'payment',
      id,
      payment: paymentResult.value,
      detections: detectionsResult.status === 'fulfilled' ? detectionsResult.value : [],
      reviews: reviewsResult.status === 'fulfilled' ? reviewsResult.value : [],
    };
  }

  if (id.startsWith('DET-')) {
    const detection = await api.investigation.getDetectionResult(id);
    const [paymentResult, reviewsResult] = await Promise.allSettled([
      detection.payment_id ? api.investigation.getPayment(detection.payment_id) : Promise.resolve(null),
      detection.payment_id ? api.investigation.getPaymentReviews(detection.payment_id) : Promise.resolve([]),
    ]);
    return {
      kind: 'detection',
      id,
      detection,
      payment: paymentResult.status === 'fulfilled' ? paymentResult.value : null,
      reviews: reviewsResult.status === 'fulfilled' ? reviewsResult.value : [],
    };
  }

  throw new Error('Enter an account ID (ACC-...), payment ID (PAY-...), or detection result ID (DET-...).');
}

export function Investigations() {
  const [searchParams] = useSearchParams();
  const initialId = searchParams.get('id') ?? searchParams.get('account') ?? searchParams.get('payment') ?? searchParams.get('detection') ?? '';
  const [searchInput, setSearchInput] = useState(initialId);
  const [activeId, setActiveId] = useState(initialId);
  const load = useCallback(() => loadInvestigation(activeId), [activeId]);
  const { state, refresh } = useResource(activeId ? load : null, [activeId]);

  function handleSearch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActiveId(searchInput.trim());
  }

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="CASE WORKSPACE / READ-ONLY INVESTIGATION"
        title="Investigations"
        description="Review account context, payment state, and detection evidence from the connected local APIs."
        action={<Badge tone="intel" dot>Read-only analysis</Badge>}
      />

      <Panel title="Find an entity" subtitle="Search by account, payment, or detection result identifier." className="query-panel">
        <form onSubmit={handleSearch} className="lookup-form">
          <label className="field-label" htmlFor="investigation-query">Entity identifier</label>
          <div className="lookup-controls">
            <div className="input-with-icon"><Search size={16} aria-hidden="true" /><input id="investigation-query" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="ACC-..., PAY-..., DET-..." /></div>
            <button className="button-primary" type="submit"><Search size={15} />Investigate</button>
            <button className="button-secondary demo-button" type="button" onClick={() => { setSearchInput(DEMO_IDS.payment); setActiveId(DEMO_IDS.payment); }}>Load Phase 7.2 payment</button>
          </div>
        </form>
        <p className="query-footnote">Demo identifiers are shortcuts only. Results load from the configured API; they are not bundled frontend data.</p>
      </Panel>

      {!activeId && <StatePanel title="No investigation selected" description="Enter an account ID, payment ID, or detection result ID to open a case workspace." icon={CircleHelp} />}
      {state.status === 'loading' && <StatePanel title="Loading investigation" description={`Requesting current API records for ${activeId}.`} icon={Search} tone="loading" />}
      {state.status === 'error' && <StatePanel title="Investigation unavailable" description={state.error.message} icon={CircleHelp} tone="error" action={<button type="button" className="text-button" onClick={refresh}>Retry request</button>} />}
      {state.status === 'success' && <InvestigationDetails bundle={state.data} />}
    </div>
  );
}

