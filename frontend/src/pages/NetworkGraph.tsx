import { useCallback, useState, type FormEvent } from 'react';
import { CircleHelp, Network, RefreshCw } from 'lucide-react';
import { api } from '../api/client';
import { Badge, PageHeading, Panel, RawResponse, SourceStamp, StatePanel } from '../components/Ui';
import { useResource } from '../hooks/useResource';
import { AccountNetwork } from '../graph/AccountNetwork';
import type { AccountActivity, OfflineAccount } from '../types/api';
import { DEMO_IDS } from '../utils/presentation';

interface GraphContext {
  activity: AccountActivity[];
  offlineProfile: OfflineAccount | null;
}

export function NetworkGraph() {
  const [draftId, setDraftId] = useState<string>(DEMO_IDS.account);
  const [accountId, setAccountId] = useState('');
  const loadGraph = useCallback(async (): Promise<GraphContext> => {
    const [activityResult, accountResult] = await Promise.allSettled([
      api.investigation.getAccountActivity(accountId, 100),
      api.getAccount(accountId),
    ]);
    if (activityResult.status === 'rejected') throw activityResult.reason;
    return {
      activity: activityResult.value,
      offlineProfile: accountResult.status === 'fulfilled' ? accountResult.value : null,
    };
  }, [accountId]);
  const { state, refresh } = useResource(accountId ? loadGraph : null, [accountId]);
  const graph = state.status === 'success' ? state.data : null;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAccountId(draftId.trim());
  }

  return (
    <div className="page-stack">
      <PageHeading eyebrow="RELATIONSHIP INTELLIGENCE / ONE-HOP VIEW" title="Network Graph" description="Account relationships derived from payment-service activity around one focal account." action={accountId ? <SourceStamp source="STREAMING_PAYMENT_SERVICE" /> : undefined} />
      <Panel title="Focal account" subtitle="Edges and direction come from account activity; neighbor risk is not returned by this endpoint." className="query-panel">
        <form className="lookup-form" onSubmit={submit}>
          <label className="field-label" htmlFor="graph-account">Account ID</label>
          <div className="lookup-controls"><input id="graph-account" value={draftId} onChange={(event) => setDraftId(event.target.value)} placeholder="ACC-..." /><button className="button-primary" type="submit"><Network size={15} />Build graph</button>{accountId && <button className="button-secondary button-icon-text" type="button" onClick={refresh}><RefreshCw size={15} />Refresh</button>}</div>
        </form>
      </Panel>

      <div className="graph-key"><span><i className="graph-key-indigo" />Network relationship</span><span><i className="graph-key-red" />Returned high-risk/payment signal</span><span><i className="graph-key-neutral" />Risk not returned</span><Badge tone="intel">Focal account</Badge></div>

      {!accountId && <StatePanel title="No focal account selected" description="Enter an account ID to load its real payment relationships." icon={CircleHelp} />}
      {state.status === 'loading' && <StatePanel title="Building account graph" description="Loading payment-service activity and optional offline account context." icon={RefreshCw} tone="loading" />}
      {state.status === 'error' && <StatePanel title="Network data unavailable" description={state.error.message} icon={CircleHelp} tone="error" />}
      {graph && <>
        <Panel title={`Relationship map · ${accountId}`} subtitle={`${graph.activity.length} payment activity records · focal profile risk is sourced separately from OFFLINE_DATASET`} className="graph-panel" action={<Badge tone="intel">One hop</Badge>}>
          <AccountNetwork accountId={accountId} activity={graph.activity} offlineProfile={graph.offlineProfile} />
        </Panel>
        {!graph.activity.length && <StatePanel title="No relationships returned" description="The focal account is shown as the center node; no incoming or outgoing payment activity was returned." icon={Network} />}
        <RawResponse value={graph.activity} />
      </>}
    </div>
  );
}