import { useCallback } from 'react';
import { Activity, Database, Network, Server, ShieldAlert } from 'lucide-react';
import { api } from '../api/client';
import { Badge, PageHeading, Panel, RawResponse, StatePanel } from '../components/Ui';
import { useResource } from '../hooks/useResource';
import { DEMO_IDS } from '../utils/presentation';

export function SystemStatus() {
  const health = useResource(api.getHealth);
  const offlineSummary = useResource(api.getSummary);
  const loadInvestigationProbe = useCallback(
    () => api.investigation.getAccountPayments(DEMO_IDS.account, 1),
    [],
  );
  const investigation = useResource(loadInvestigationProbe);
  const healthData = health.state.status === 'success' ? health.state.data : null;
  const investigationData = investigation.state.status === 'success' ? investigation.state.data : null;
  const investigationSource = investigationData?.[0]?.data_source;

  return (
    <div className="page-stack">
      <PageHeading eyebrow="PLATFORM / SERVICE VISIBILITY" title="System / API Status" description="Health indicators derived from existing endpoint responses. Unexposed services are identified as such." />
      <div className="status-grid">
        <StatusCard title="FastAPI" detail="GET /health" icon={Server} state={health.state.status === 'success' && healthData?.status === 'ok' ? 'available' : health.state.status === 'loading' ? 'checking' : 'unavailable'} description={health.state.status === 'success' ? `Mode: ${healthData?.mode ?? 'unknown'}` : health.state.status === 'error' ? health.state.error.message : 'Checking the local API.'} />
        <StatusCard title="Investigation API" detail="Account payments read" icon={Activity} state={investigation.state.status === 'success' ? 'available' : investigation.state.status === 'loading' ? 'checking' : 'unavailable'} description={investigation.state.status === 'success' ? 'Read request accepted.' : investigation.state.status === 'error' ? investigation.state.error.message : 'Checking the configured route.'} />
        <StatusCard title="Detection service" detail="No health route exposed" icon={ShieldAlert} state="not-exposed" description="The API does not provide an independent detection-service health endpoint." />
        <StatusCard title="Offline data source" detail="GET /summary" icon={Database} state={offlineSummary.state.status === 'success' ? 'available' : offlineSummary.state.status === 'loading' ? 'checking' : 'unavailable'} description={offlineSummary.state.status === 'success' ? `${offlineSummary.state.data.transaction_count.toLocaleString()} transactions in summary.` : offlineSummary.state.status === 'error' ? offlineSummary.state.error.message : 'Checking the analysis summary.'} />
      </div>

      <Panel title="Data source status" subtitle="Source labels are taken from returned records where the API provides them.">
        <div className="source-status-list">
          <div><span className="source-symbol source-indigo"><Network size={17} /></span><div><strong>OFFLINE_DATASET</strong><p>Existing synthetic transaction dataset used by summary and alert endpoints.</p></div><StatusBadge state={offlineSummary.state.status === 'success' ? 'available' : offlineSummary.state.status === 'loading' ? 'checking' : 'unavailable'} /></div>
          <div><span className="source-symbol source-indigo"><Activity size={17} /></span><div><strong>{investigationSource ?? 'STREAMING_PAYMENT_SERVICE'}</strong><p>Investigation responses identify this source when matching payment data is returned.</p></div><StatusBadge state={investigation.state.status === 'success' ? 'available' : investigation.state.status === 'loading' ? 'checking' : 'unavailable'} /></div>
        </div>
        {investigation.state.status === 'success' && investigationData?.length === 0 && <p className="quiet-note">The investigation route responded successfully with no payment rows for the sample account. The source is reachable, but no STREAMING_PAYMENT_SERVICE record was returned.</p>}
      </Panel>

      {health.state.status === 'error' && <StatePanel title="FastAPI unavailable" description={health.state.error.message} icon={Server} tone="error" />}
      <div className="status-disclaimer"><Badge tone="warning">No production integrations</Badge><p>This console is configured for local synthetic analysis only. It does not connect to banks, payment rails, or account-action systems.</p></div>
      {health.state.status === 'success' && <RawResponse value={{ health: healthData, offlineSummary: offlineSummary.state.status === 'success' ? offlineSummary.state.data : null, investigationRecordSource: investigationSource ?? null }} />}
    </div>
  );
}

function StatusCard({ title, detail, icon: Icon, state, description }: { title: string; detail: string; icon: typeof Server; state: 'available' | 'unavailable' | 'checking' | 'not-exposed'; description: string }) {
  return (
    <article className={`service-card service-${state}`}>
      <div className="service-card-top"><span className="service-icon"><Icon size={18} aria-hidden="true" /></span><StatusBadge state={state} /></div>
      <h2>{title}</h2><p className="service-route">{detail}</p><p className="service-description">{description}</p>
    </article>
  );
}

function StatusBadge({ state }: { state: 'available' | 'unavailable' | 'checking' | 'not-exposed' }) {
  const config = {
    available: { label: 'Available', tone: 'intel' as const },
    unavailable: { label: 'Unavailable', tone: 'critical' as const },
    checking: { label: 'Checking', tone: 'intel' as const },
    'not-exposed': { label: 'Not exposed', tone: 'neutral' as const },
  }[state];
  return <Badge tone={config.tone} dot>{config.label}</Badge>;
}