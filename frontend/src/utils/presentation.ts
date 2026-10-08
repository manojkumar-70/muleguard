import type { DetectionResult, RiskLevel, RiskSignal } from '../types/api';

export const DEMO_IDS = {
  account: 'ACC-M-000001',
  payment: 'PAY-000000000000003c',
  detection: 'DET-PHASE72-0001',
} as const;

export function formatAmount(amountPaise: number, currency = 'INR') {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(amountPaise / 100);
}

export function formatTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Timestamp unavailable';
  return new Intl.DateTimeFormat('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
    timeZone: 'UTC',
  }).format(date) + ' UTC';
}

export function riskTone(level?: string): RiskLevel | 'neutral' | 'warning' {
  if (level === 'LOW' || level === 'MEDIUM' || level === 'HIGH') return level;
  if (level === 'NOT_EVALUATED' || level === 'ERROR') return 'warning';
  return 'neutral';
}

export function isRiskLevel(value: string): value is RiskLevel {
  return value === 'LOW' || value === 'MEDIUM' || value === 'HIGH';
}

export interface DemoEvidence {
  accountId: string | null;
  accountRiskScore: number | null;
  accountRiskLevel: string | null;
  projectionMethod: string | null;
  streamingExplanation: Record<string, unknown> | null;
  firstAlertSnapshot: Record<string, unknown> | null;
  sourceSignal: RiskSignal | null;
}

export function readDemoEvidence(result: DetectionResult): DemoEvidence {
  const sourceSignal = result.signals?.demo_account_level_projection ?? null;
  if (!sourceSignal) {
    return {
      accountId: null,
      accountRiskScore: null,
      accountRiskLevel: null,
      projectionMethod: null,
      streamingExplanation: null,
      firstAlertSnapshot: null,
      sourceSignal: null,
    };
  }

  try {
    const payload = JSON.parse(sourceSignal.explanation) as Record<string, unknown>;
    const streaming = isRecord(payload.streaming_explanation)
      ? payload.streaming_explanation
      : null;
    const snapshot = streaming && isRecord(streaming.first_alert_snapshot)
      ? streaming.first_alert_snapshot
      : null;
    return {
      accountId: typeof payload.account_id === 'string' ? payload.account_id : null,
      accountRiskScore: finiteNumber(payload.account_risk_score),
      accountRiskLevel:
        typeof payload.account_risk_level === 'string'
          ? payload.account_risk_level
          : null,
      projectionMethod:
        typeof payload.projection_method === 'string'
          ? payload.projection_method
          : null,
      streamingExplanation: streaming,
      firstAlertSnapshot: snapshot,
      sourceSignal,
    };
  } catch {
    return {
      accountId: null,
      accountRiskScore: null,
      accountRiskLevel: null,
      projectionMethod: null,
      streamingExplanation: null,
      firstAlertSnapshot: null,
      sourceSignal,
    };
  }
}

export function historicalSnapshotSummary(evidence: DemoEvidence) {
  const summary = evidence.firstAlertSnapshot?.summary;
  return typeof summary === 'string' ? summary : null;
}

export function historicalSnapshotScore(evidence: DemoEvidence) {
  const summary = historicalSnapshotSummary(evidence);
  if (!summary) return null;
  const match = summary.match(/\b(?:MEDIUM|HIGH)\s+at\s+(\d+(?:\.\d+)?)\/100\b/i);
  return match ? Number(match[1]) : null;
}

function finiteNumber(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}