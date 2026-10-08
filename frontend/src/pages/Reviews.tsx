import { useCallback, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ClipboardCheck, FileSearch, RefreshCw } from 'lucide-react';
import { api } from '../api/client';
import { ApiError } from '../api/client';
import { Badge, PageHeading, Panel, RawResponse, SourceStamp, StatePanel } from '../components/Ui';
import { useResource } from '../hooks/useResource';
import type { DetectionResult, HumanReview, PaymentInvestigation, ReviewDecision } from '../types/api';
import { DEMO_IDS, formatTimestamp } from '../utils/presentation';

interface ReviewContext {
  payment: PaymentInvestigation;
  detections: DetectionResult[];
  reviews: HumanReview[];
}

const decisions: { value: ReviewDecision; label: string }[] = [
  { value: 'CONFIRMED_FOR_REVIEW', label: 'Confirmed for review' },
  { value: 'NOT_SUSPICIOUS', label: 'Not suspicious' },
  { value: 'NEEDS_MORE_INFORMATION', label: 'Needs more information' },
  { value: 'UNRESOLVED', label: 'Unresolved' },
];

export function Reviews() {
  const [searchParams] = useSearchParams();
  const initialPayment = searchParams.get('payment') ?? '';
  const [draftPaymentId, setDraftPaymentId] = useState(initialPayment);
  const [paymentId, setPaymentId] = useState(initialPayment);
  const [reviewerId, setReviewerId] = useState('');
  const [decision, setDecision] = useState<ReviewDecision>('NEEDS_MORE_INFORMATION');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const loadContext = useCallback(async (): Promise<ReviewContext> => {
    const [payment, detections, reviews] = await Promise.all([
      api.investigation.getPayment(paymentId),
      api.investigation.getPaymentDetections(paymentId),
      api.investigation.getPaymentReviews(paymentId),
    ]);
    return { payment, detections, reviews };
  }, [paymentId]);
  const { state, refresh } = useResource(paymentId ? loadContext : null, [paymentId]);
  const context = state.status === 'success' ? state.data : null;

  function selectPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitMessage(null);
    setSubmitError(null);
    setPaymentId(draftPaymentId.trim());
  }

  async function submitReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!context) return;
    const targetDetection = context.detections.find((item) => item.detection_result_id === selectedDetectionId);
    if (!targetDetection) {
      setSubmitError('Choose a detection result before recording a review.');
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    setSubmitMessage(null);
    try {
      await api.investigation.createReview(context.payment.payment_id, {
        detection_result_id: targetDetection.detection_result_id,
        reviewer_id: reviewerId.trim(),
        decision,
        note: note.trim() || null,
      });
      setSubmitMessage('Review recorded as a new append-only entry.');
      setNote('');
      refresh();
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error.message : 'The review could not be recorded.');
    } finally {
      setSubmitting(false);
    }
  }

  const [selectedDetectionId, setSelectedDetectionId] = useState('');

  return (
    <div className="page-stack">
      <PageHeading eyebrow="GOVERNANCE / APPEND-ONLY RECORDS" title="Investigator Reviews" description="Record and inspect investigative decisions attached to returned detection results." action={<Badge tone="intel">Append-only</Badge>} />

      <Panel title="Review target" subtitle="Reviews require a payment and one of its associated detection result IDs." className="query-panel">
        <form className="lookup-form" onSubmit={selectPayment}>
          <label className="field-label" htmlFor="review-payment">Payment ID</label>
          <div className="lookup-controls">
            <input id="review-payment" value={draftPaymentId} onChange={(event) => setDraftPaymentId(event.target.value)} placeholder="PAY-..." />
            <button className="button-primary" type="submit"><FileSearch size={15} />Load review context</button>
            <button className="button-secondary demo-button" type="button" onClick={() => { setDraftPaymentId(DEMO_IDS.payment); setPaymentId(DEMO_IDS.payment); }}>Phase 7.2 payment</button>
          </div>
        </form>
      </Panel>

      {!paymentId && <StatePanel title="No payment selected" description="Load a payment to inspect existing reviews and any eligible detection results." icon={ClipboardCheck} />}
      {state.status === 'loading' && <StatePanel title="Loading review context" description="Requesting the payment, detections, and current append-only reviews." icon={RefreshCw} tone="loading" />}
      {state.status === 'error' && <StatePanel title="Review context unavailable" description={state.error.message} icon={FileSearch} tone="error" />}

      {context && (
        <>
          <Panel title="Payment and risk state" subtitle="Review records never modify these values." action={<SourceStamp source={context.payment.data_source} />}>
            <div className="review-context-grid">
              <div><span>Payment</span><strong className="mono">{context.payment.payment_id}</strong></div>
              <div><span>Payment status</span><Badge tone="neutral">{context.payment.status}</Badge></div>
              <div><span>Risk status</span><Badge tone={context.payment.risk_status === 'NOT_EVALUATED' ? 'warning' : 'intel'}>{context.payment.risk_status}</Badge></div>
              <div><span>Detection results</span><strong>{context.detections.length}</strong></div>
            </div>
          </Panel>

          <div className="reviews-layout">
            <Panel title="Review history" subtitle={`${context.reviews.length} existing review${context.reviews.length === 1 ? '' : 's'}`} action={<button className="icon-button" type="button" aria-label="Refresh review history" title="Refresh review history" onClick={refresh}><RefreshCw size={16} /></button>}>
              {context.reviews.length ? <div className="review-list">{context.reviews.map((review) => <article className="review-row" key={review.review_id}>
                <div className="review-avatar">{review.reviewer_id.slice(-2)}</div><div className="review-body"><div><strong>{review.reviewer_id}</strong><Badge tone="neutral">{review.decision.replaceAll('_', ' ')}</Badge></div><p>{review.note || 'No notes provided.'}</p><small>{review.review_id} · {formatTimestamp(review.created_at)}</small><small>Detection: <span className="mono">{review.detection_result_id}</span></small></div>
              </article>)}</div> : <StatePanel title="No reviews recorded" description="The API returned no review entries for this payment." icon={ClipboardCheck} />}
            </Panel>

            <Panel title="Record a review" subtitle="Creates a new review entry. Existing entries cannot be edited or removed.">
              {context.detections.length ? <form className="review-form" onSubmit={submitReview}>
                <label className="field-label" htmlFor="review-detection">Detection result</label>
                <select id="review-detection" value={selectedDetectionId || context.detections[0].detection_result_id} onChange={(event) => setSelectedDetectionId(event.target.value)}>
                  {context.detections.map((result) => <option value={result.detection_result_id} key={result.detection_result_id}>{result.detection_result_id} · {result.risk_level} · {result.risk_score.toFixed(2)}</option>)}
                </select>
                <label className="field-label" htmlFor="reviewer-id">Reviewer ID</label>
                <input id="reviewer-id" value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="USER-..." pattern="USER-[A-Za-z0-9-]{1,68}" required />
                <label className="field-label" htmlFor="review-decision">Decision</label>
                <select id="review-decision" value={decision} onChange={(event) => setDecision(event.target.value as ReviewDecision)}>
                  {decisions.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
                </select>
                <label className="field-label" htmlFor="review-notes">Investigation notes <span className="field-optional">Optional · 4,000 characters max</span></label>
                <textarea id="review-notes" maxLength={4000} rows={5} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Record evidence reviewed and rationale." />
                <div className="form-footer"><span>{note.length} / 4,000</span><button className="button-primary" type="submit" disabled={submitting || !reviewerId.trim()}><ClipboardCheck size={15} />{submitting ? 'Recording…' : 'Record review'}</button></div>
                {submitMessage && <p className="form-success" role="status">{submitMessage}</p>}
                {submitError && <p className="form-error" role="alert">{submitError}</p>}
              </form> : <StatePanel title="Detection result required" description="No detection result is associated with this payment, so the API cannot attach a review yet." icon={FileSearch} />}
            </Panel>
          </div>
          <RawResponse value={context} />
        </>
      )}
    </div>
  );
}