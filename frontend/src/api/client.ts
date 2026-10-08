import type {
  AccountActivity,
  AlertsResponse,
  CreateReviewRequest,
  DetectionResult,
  HealthResponse,
  HumanReview,
  OfflineAccount,
  PaymentInvestigation,
  SummaryResponse,
} from '../types/api';

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000')
  .replace(/\/$/, '');

export class ApiError extends Error {
  status?: number;
  code?: string;
  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError('Could not connect to the local FastAPI service.');
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError('The API returned malformed JSON.', response.status);
  }

  if (!response.ok) {
    const body = isRecord(payload) ? payload : {};
    const error = isRecord(body.error) ? body.error : {};
    const message = typeof error.message === 'string'
      ? error.message
      : typeof body.detail === 'string'
        ? body.detail
        : `The API returned HTTP ${response.status}.`;
    throw new ApiError(
      message,
      response.status,
      typeof error.code === 'string' ? error.code : undefined,
    );
  }

  return payload as T;
}

function requireRecord<T>(value: unknown, path: string): T {
  if (!isRecord(value)) {
    throw new ApiError(`Malformed API response from ${path}.`);
  }
  return value as T;
}

function requireRecordList<T>(value: unknown, path: string): T[] {
  if (!Array.isArray(value) || value.some((item) => !isRecord(item))) {
    throw new ApiError(`Malformed API response from ${path}.`);
  }
  return value as T[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export const api = {
  getHealth: async () => requireRecord<HealthResponse>(await request('/health'), '/health'),
  getSummary: async () => requireRecord<SummaryResponse>(await request('/summary'), '/summary'),
  getAlerts: async () => requireRecord<AlertsResponse>(await request('/alerts'), '/alerts'),
  getAccount: async (accountId: string) => requireRecord<OfflineAccount>(
    await request(`/accounts/${encodeURIComponent(accountId)}`),
    '/accounts/{account_id}',
  ),
  investigation: {
    getPayment: async (paymentId: string) => requireRecord<PaymentInvestigation>(
      await request(`/v1/investigation/payments/${encodeURIComponent(paymentId)}`),
      '/v1/investigation/payments/{payment_id}',
    ),
    getAccountPayments: async (accountId: string, limit = 50) => requireRecordList<PaymentInvestigation>(
      await request(`/v1/investigation/accounts/${encodeURIComponent(accountId)}/payments?limit=${limit}`),
      '/v1/investigation/accounts/{account_id}/payments',
    ),
    getAccountActivity: async (accountId: string, limit = 50) => requireRecordList<AccountActivity>(
      await request(`/v1/investigation/accounts/${encodeURIComponent(accountId)}/activity?limit=${limit}`),
      '/v1/investigation/accounts/{account_id}/activity',
    ),
    getDetectionResult: async (detectionId: string) => requireRecord<DetectionResult>(
      await request(`/v1/investigation/detection-results/${encodeURIComponent(detectionId)}`),
      '/v1/investigation/detection-results/{detection_result_id}',
    ),
    getPaymentDetections: async (paymentId: string) => requireRecordList<DetectionResult>(
      await request(`/v1/investigation/payments/${encodeURIComponent(paymentId)}/detection-results`),
      '/v1/investigation/payments/{payment_id}/detection-results',
    ),
    getPaymentReviews: async (paymentId: string) => requireRecordList<HumanReview>(
      await request(`/v1/investigation/payments/${encodeURIComponent(paymentId)}/reviews`),
      '/v1/investigation/payments/{payment_id}/reviews',
    ),
    createReview: async (paymentId: string, review: CreateReviewRequest) => requireRecord<HumanReview>(
      await request(`/v1/investigation/payments/${encodeURIComponent(paymentId)}/reviews`, {
        method: 'POST',
        body: JSON.stringify(review),
      }),
      '/v1/investigation/payments/{payment_id}/reviews',
    ),
  },
};

export const apiBaseUrl = API_BASE;

