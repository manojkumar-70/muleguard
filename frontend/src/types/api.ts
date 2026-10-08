export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';
export type PaymentStatus =
  | 'CREATED'
  | 'PENDING'
  | 'AUTHORIZED'
  | 'CAPTURED'
  | 'DECLINED'
  | 'FAILED'
  | 'CANCELLED'
  | 'EXPIRED';
export type PaymentRiskStatus = 'NOT_EVALUATED' | RiskLevel | 'ERROR';

export interface RiskIndicator {
  code?: string;
  rule?: string;
  explanation: string;
}

export interface RiskSignal {
  contribution: number;
  explanation: string;
  indicators: RiskIndicator[];
}

export interface RiskAccount {
  account_id: string;
  risk_score: number;
  risk_level: RiskLevel;
  recommendation?: string;
  method_contributions?: Record<string, RiskSignal>;
  disclaimer?: string;
}

export interface HealthResponse {
  status: string;
  mode: string;
}

export interface SummaryResponse {
  transaction_count: number;
  graph: Record<string, unknown>;
  risk: {
    account_count: number;
    risk_level_counts: Record<RiskLevel, number>;
    thresholds?: Record<string, number>;
    disclaimer?: string;
  };
}

export interface AlertsResponse {
  count: number;
  alerts: RiskAccount[];
}

export interface OfflineAccount extends RiskAccount {
  rule_based_features?: Record<string, unknown>;
  anomaly_detection?: {
    prediction?: number;
    is_anomaly?: boolean;
    anomaly_score?: number | null;
  };
  graph_indicators?: RiskIndicator[];
}

export interface PaymentInvestigation {
  payment_id: string;
  customer_id: string;
  merchant_id: string;
  sender_account_id: string;
  receiver_account_id: string;
  amount_paise: number;
  currency: 'INR';
  status: PaymentStatus;
  created_at: string;
  detection_result_id: string | null;
  risk_status: PaymentRiskStatus;
  data_source: 'OFFLINE_DATASET' | 'STREAMING_PAYMENT_SERVICE';
}

export interface AccountActivity {
  account_id: string;
  direction: 'INCOMING' | 'OUTGOING';
  payment_id: string;
  counterparty_account_id: string;
  amount_paise: number;
  currency: 'INR';
  payment_status: PaymentStatus;
  created_at: string;
  risk_status: PaymentRiskStatus;
  data_source: 'STREAMING_PAYMENT_SERVICE';
}

export interface DetectionResult {
  detection_result_id: string;
  protocol: 'stream_frozen_model';
  transaction_id: string;
  payment_id: string | null;
  risk_score: number;
  risk_level: RiskLevel;
  signals: Record<string, RiskSignal>;
  created_at: string;
  disclaimer: string;
  data_source: 'STREAMING_PAYMENT_SERVICE';
}

export type ReviewDecision =
  | 'CONFIRMED_FOR_REVIEW'
  | 'NOT_SUSPICIOUS'
  | 'NEEDS_MORE_INFORMATION'
  | 'UNRESOLVED';

export interface HumanReview {
  review_id: string;
  detection_result_id: string;
  reviewer_id: string;
  decision: ReviewDecision;
  note: string | null;
  created_at: string;
}

export interface CreateReviewRequest {
  detection_result_id: string;
  reviewer_id: string;
  decision: ReviewDecision;
  note: string | null;
}