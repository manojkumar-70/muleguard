const API_BASE = 'http://127.0.0.1:8000';

export class ApiClient {
  static async get<T>(path: string): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`);
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  static async post<T>(path: string, body: any): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    return response.json();
  }
}

export const api = {
  getHealth: () => ApiClient.get<any>('/health'),
  getSummary: () => ApiClient.get<any>('/summary'),
  getAlerts: () => ApiClient.get<any>('/alerts'),
  getAccount: (accountId: string) => ApiClient.get<any>(`/accounts/${accountId}`),
  
  investigation: {
    getPayment: (paymentId: string) => ApiClient.get<any>(`/v1/investigation/payments/${paymentId}`),
    getAccountPayments: (accountId: string) => ApiClient.get<any[]>(`/v1/investigation/accounts/${accountId}/payments`),
    getAccountActivity: (accountId: string) => ApiClient.get<any[]>(`/v1/investigation/accounts/${accountId}/activity`),
    getDetectionResult: (detectionId: string) => ApiClient.get<any>(`/v1/investigation/detection-results/${detectionId}`),
    getPaymentDetections: (paymentId: string) => ApiClient.get<any[]>(`/v1/investigation/payments/${paymentId}/detection-results`),
    getPaymentReviews: (paymentId: string) => ApiClient.get<any[]>(`/v1/investigation/payments/${paymentId}/reviews`),
    createReview: (paymentId: string, review: any) => ApiClient.post<any>(`/v1/investigation/payments/${paymentId}/reviews`, review),
  }
};
