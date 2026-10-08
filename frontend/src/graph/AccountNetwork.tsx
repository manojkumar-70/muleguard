import { useMemo } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import { Badge } from '../components/Ui';
import type { AccountActivity, OfflineAccount, RiskLevel } from '../types/api';

interface AccountNodeData extends Record<string, unknown> {
  accountId: string;
  role: 'FOCAL ACCOUNT' | 'SOURCE ACCOUNT' | 'DESTINATION ACCOUNT';
  riskLevel: RiskLevel | 'UNKNOWN';
  score?: number;
}

const nodeTypes = { account: AccountNode };

export function AccountNetwork({
  accountId,
  activity,
  offlineProfile,
}: {
  accountId: string;
  activity: AccountActivity[];
  offlineProfile: OfflineAccount | null;
}) {
  const { nodes, edges } = useMemo(() => buildGraph(accountId, activity, offlineProfile), [accountId, activity, offlineProfile]);

  return (
    <div className="network-canvas" aria-label="Account relationship graph">
      {!activity.length && <div className="network-empty-label">No account relationships returned for this focal account.</div>}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.22, minZoom: 0.45, maxZoom: 1 }}
        minZoom={0.25}
        maxZoom={1.6}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="#282830" />
        <MiniMap pannable zoomable nodeColor={minimapColor} maskColor="rgba(5, 5, 5, .72)" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}

function buildGraph(accountId: string, activity: AccountActivity[], profile: OfflineAccount | null) {
  const incoming = activity.filter((item) => item.direction === 'INCOMING');
  const outgoing = activity.filter((item) => item.direction === 'OUTGOING');
  const sourceAccounts = [...new Set(incoming.map((item) => item.counterparty_account_id))];
  const destinationAccounts = [...new Set(outgoing.map((item) => item.counterparty_account_id))];
  const focalRisk = profile?.risk_level ?? 'UNKNOWN';
  const nodes: Node<AccountNodeData>[] = [
    {
      id: accountId,
      type: 'account',
      position: { x: 330, y: 208 },
      data: { accountId, role: 'FOCAL ACCOUNT', riskLevel: focalRisk, score: profile?.risk_score },
    },
    ...sourceAccounts.map((id, index) => ({
      id,
      type: 'account' as const,
      position: { x: 36, y: 62 + index * 128 },
      data: { accountId: id, role: 'SOURCE ACCOUNT' as const, riskLevel: 'UNKNOWN' as const },
    })),
    ...destinationAccounts.map((id, index) => ({
      id,
      type: 'account' as const,
      position: { x: 640, y: 62 + index * 128 },
      data: { accountId: id, role: 'DESTINATION ACCOUNT' as const, riskLevel: 'UNKNOWN' as const },
    })),
  ];
  const edges: Edge[] = activity.map((item, index) => {
    const source = item.direction === 'INCOMING' ? item.counterparty_account_id : accountId;
    const target = item.direction === 'INCOMING' ? accountId : item.counterparty_account_id;
    const suspiciousStatus = item.risk_status === 'HIGH';
    const color = suspiciousStatus ? '#ff2a3d' : '#6366f1';
    return {
      id: `${item.payment_id}-${index}`,
      source,
      target,
      label: item.payment_id,
      type: 'smoothstep',
      markerEnd: { type: MarkerType.ArrowClosed, color },
      style: { stroke: color, strokeWidth: suspiciousStatus ? 2 : 1.5 },
      labelStyle: { fill: '#b7b7c0', fontSize: 10, fontFamily: 'IBM Plex Mono' },
      labelBgStyle: { fill: '#0d0d10', fillOpacity: 0.94 },
      labelBgPadding: [5, 3],
      labelBgBorderRadius: 3,
      data: { paymentId: item.payment_id, amountPaise: item.amount_paise, status: item.payment_status },
    };
  });
  return { nodes, edges };
}

function AccountNode({ data }: NodeProps<Node<AccountNodeData>>) {
  const riskClass = data.riskLevel === 'HIGH' ? 'node-risk-high' : data.riskLevel === 'MEDIUM' ? 'node-risk-medium' : 'node-risk-unknown';
  return (
    <div className={`account-node ${data.role === 'FOCAL ACCOUNT' ? 'account-node-focal' : ''} ${riskClass}`}>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <span className="account-node-role">{data.role}</span>
      <strong>{data.accountId}</strong>
      <div className="account-node-meta">
        <Badge tone={data.riskLevel === 'UNKNOWN' ? 'neutral' : data.riskLevel}>{data.riskLevel === 'UNKNOWN' ? 'RISK UNKNOWN' : data.riskLevel}</Badge>
        {typeof data.score === 'number' && <span>{data.score.toFixed(2)} / 100 · offline</span>}
      </div>
    </div>
  );
}

function minimapColor(node: Node) {
  const data = node.data as AccountNodeData;
  if (data.riskLevel === 'HIGH') return '#ff2a3d';
  if (data.role === 'FOCAL ACCOUNT') return '#6366f1';
  return '#7a7a85';
}