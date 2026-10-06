"""End-to-end validation of the local payment investigation workflow."""

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient

AI_ENGINE_PATH = Path(__file__).resolve().parent
DASHBOARD_PATH = AI_ENGINE_PATH.parent / "dashboard"
for path in (str(AI_ENGINE_PATH), str(DASHBOARD_PATH)):
    if path not in sys.path:
        sys.path.insert(0, path)

import api
from app import DATA_SOURCE_MODES, OFFLINE_DATASET_MODE
from investigation_client import InvestigationAPIClient
from payment_service.config import PaymentAPISettings
from payment_service.investigation_schemas import InvestigationDataSource
from payment_service.schemas import (
    DetectionResult,
    RiskSignal,
    SyntheticCustomer,
    SyntheticMerchant,
)
from payment_service.sqlite_repository import SQLitePaymentRepository


class _FastAPITestClientTransport(httpx.BaseTransport):
    """Adapt the real FastAPI TestClient for the dashboard HTTP client."""

    def __init__(self, client: TestClient):
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._client.request(
            request.method,
            str(request.url),
            headers=dict(request.headers),
            content=request.read(),
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            request=request,
        )

    def close(self) -> None:
        pass


class InvestigationEndToEndTests(unittest.TestCase):
    def test_complete_local_payment_investigation_and_review_workflow(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "phase-6-6.sqlite3"
            repository = SQLitePaymentRepository(database_path)
            _seed_entities(repository)

            application = api.create_app(
                PaymentAPISettings(
                    enabled=True,
                    database_path=database_path,
                    repository_backend="sqlite",
                )
            )
            with TestClient(application) as api_client:
                created_payment = api_client.post(
                    "/v1/payments",
                    headers={"Idempotency-Key": "phase-6-6-e2e-payment"},
                    json={
                        "customer_id": "CUST-E2E",
                        "merchant_id": "MER-E2E",
                        "amount_paise": 12500,
                        "currency": "INR",
                    },
                )
                self.assertEqual(
                    created_payment.status_code,
                    201,
                    created_payment.text,
                )
                payment_id = created_payment.json()["payment_id"]
                initial_payment = repository.get_payment(payment_id)
                self.assertIsNotNone(initial_payment)
                self.assertEqual(initial_payment.payment_status.value, "CAPTURED")
                self.assertEqual(
                    initial_payment.risk_status.value,
                    "NOT_EVALUATED",
                )

                detection_result = _detection_result(payment_id)
                repository.save_detection_result(detection_result)
                repository.update_payment(
                    initial_payment.model_copy(
                        update={
                            "detection_protocol": detection_result.protocol,
                            "detection_result_id": (
                                detection_result.detection_result_id
                            ),
                        }
                    )
                )
                payment_before_investigation = repository.get_payment(payment_id)
                detection_before_investigation = (
                    repository.get_detection_result(
                        detection_result.detection_result_id
                    )
                )

                payment_view = api_client.get(
                    f"/v1/investigation/payments/{payment_id}"
                )
                self.assertEqual(payment_view.status_code, 200, payment_view.text)
                self.assertEqual(
                    payment_view.json()["data_source"],
                    InvestigationDataSource.STREAMING_PAYMENT_SERVICE.value,
                )
                self.assertEqual(
                    payment_view.json()["detection_result_id"],
                    detection_result.detection_result_id,
                )

                account_payments = api_client.get(
                    "/v1/investigation/accounts/ACC-E2E-CUSTOMER/payments"
                )
                self.assertEqual(
                    account_payments.status_code,
                    200,
                    account_payments.text,
                )
                self.assertEqual(len(account_payments.json()), 1)
                self.assertEqual(
                    account_payments.json()[0]["payment_id"],
                    payment_id,
                )
                self.assertEqual(
                    account_payments.json()[0]["data_source"],
                    InvestigationDataSource.STREAMING_PAYMENT_SERVICE.value,
                )

                activity = api_client.get(
                    "/v1/investigation/accounts/ACC-E2E-CUSTOMER/activity"
                )
                self.assertEqual(activity.status_code, 200, activity.text)
                self.assertEqual(len(activity.json()), 1)
                self.assertEqual(activity.json()[0]["direction"], "OUTGOING")
                self.assertEqual(activity.json()[0]["payment_id"], payment_id)
                self.assertEqual(
                    activity.json()[0]["data_source"],
                    InvestigationDataSource.STREAMING_PAYMENT_SERVICE.value,
                )

                payment_detections = api_client.get(
                    f"/v1/investigation/payments/{payment_id}/detection-results"
                )
                self.assertEqual(
                    payment_detections.status_code,
                    200,
                    payment_detections.text,
                )
                self.assertEqual(len(payment_detections.json()), 1)
                self.assertEqual(
                    payment_detections.json()[0]["detection_result_id"],
                    detection_result.detection_result_id,
                )
                self.assertEqual(
                    payment_detections.json()[0]["data_source"],
                    InvestigationDataSource.STREAMING_PAYMENT_SERVICE.value,
                )

                detection_detail = api_client.get(
                    "/v1/investigation/detection-results/"
                    f"{detection_result.detection_result_id}"
                )
                self.assertEqual(
                    detection_detail.status_code,
                    200,
                    detection_detail.text,
                )
                self.assertEqual(
                    detection_detail.json()["data_source"],
                    InvestigationDataSource.STREAMING_PAYMENT_SERVICE.value,
                )

                missing_payment = api_client.get(
                    "/v1/investigation/payments/PAY-MISSING"
                )
                missing_detection = api_client.get(
                    "/v1/investigation/detection-results/DET-MISSING"
                )
                invalid_review = api_client.post(
                    f"/v1/investigation/payments/{payment_id}/reviews",
                    json={
                        "detection_result_id": detection_result.detection_result_id,
                        "reviewer_id": "USER-E2E",
                        "decision": "BLOCK_PAYMENT",
                        "note": "Invalid synthetic review decision.",
                    },
                )
                empty_activity = api_client.get(
                    "/v1/investigation/accounts/ACC-E2E-EMPTY/activity"
                )
                self.assertEqual(missing_payment.status_code, 404)
                self.assertEqual(missing_detection.status_code, 404)
                self.assertEqual(invalid_review.status_code, 422)
                self.assertEqual(empty_activity.status_code, 200)
                self.assertEqual(empty_activity.json(), [])

                review_payloads = (
                    {
                        "detection_result_id": detection_result.detection_result_id,
                        "reviewer_id": "USER-E2E-1",
                        "decision": "NEEDS_MORE_INFORMATION",
                        "note": "Synthetic review one.",
                    },
                    {
                        "detection_result_id": detection_result.detection_result_id,
                        "reviewer_id": "USER-E2E-2",
                        "decision": "UNRESOLVED",
                        "note": "Synthetic review two.",
                    },
                )
                created_reviews = [
                    api_client.post(
                        f"/v1/investigation/payments/{payment_id}/reviews",
                        json=payload,
                    )
                    for payload in review_payloads
                ]
                self.assertTrue(
                    all(response.status_code == 201 for response in created_reviews),
                    [response.text for response in created_reviews],
                )
                reviews = api_client.get(
                    f"/v1/investigation/payments/{payment_id}/reviews"
                )
                self.assertEqual(reviews.status_code, 200, reviews.text)
                self.assertEqual(len(reviews.json()), 2)
                self.assertEqual(
                    {review["reviewer_id"] for review in reviews.json()},
                    {"USER-E2E-1", "USER-E2E-2"},
                )

                dashboard_api_client = InvestigationAPIClient(
                    base_url="http://127.0.0.1:8000",
                    transport=_FastAPITestClientTransport(api_client),
                )
                try:
                    dashboard_payment = dashboard_api_client.get_payment(payment_id)
                    dashboard_activity = (
                        dashboard_api_client.list_account_activity(
                            "ACC-E2E-CUSTOMER"
                        )
                    )
                    dashboard_detection = (
                        dashboard_api_client.get_detection_result(
                            detection_result.detection_result_id
                        )
                    )
                    dashboard_reviews = (
                        dashboard_api_client.list_payment_reviews(payment_id)
                    )
                finally:
                    dashboard_api_client.close()

                self.assertEqual(dashboard_payment.payment_id, payment_id)
                self.assertEqual(len(dashboard_activity), 1)
                self.assertEqual(
                    dashboard_detection.detection_result_id,
                    detection_result.detection_result_id,
                )
                self.assertEqual(len(dashboard_reviews), 2)
                self.assertEqual(
                    dashboard_payment.data_source,
                    InvestigationDataSource.STREAMING_PAYMENT_SERVICE,
                )

                offline_response = api_client.post(
                    "/analyze",
                    json={
                        "transactions": [
                            _offline_transaction(
                                "TXN-E2E-1",
                                "ACC-OFFLINE-1",
                                "ACC-OFFLINE-2",
                            ),
                            _offline_transaction(
                                "TXN-E2E-2",
                                "ACC-OFFLINE-2",
                                "ACC-OFFLINE-3",
                            ),
                        ]
                    },
                )
                self.assertEqual(
                    offline_response.status_code,
                    200,
                    offline_response.text,
                )
                self.assertEqual(
                    offline_response.json()["transaction_count"],
                    2,
                )
                self.assertEqual(DATA_SOURCE_MODES[0], OFFLINE_DATASET_MODE)
                self.assertEqual(
                    InvestigationDataSource.OFFLINE_DATASET.value,
                    "OFFLINE_DATASET",
                )
                self.assertNotIn("data_source", offline_response.json())

                final_payment = repository.get_payment(payment_id)
                final_detection = repository.get_detection_result(
                    detection_result.detection_result_id
                )
                self.assertEqual(
                    final_payment.payment_status,
                    payment_before_investigation.payment_status,
                )
                self.assertEqual(
                    final_payment.risk_status,
                    payment_before_investigation.risk_status,
                )
                self.assertEqual(
                    final_payment.model_dump(),
                    payment_before_investigation.model_dump(),
                )
                self.assertEqual(
                    final_detection.model_dump(),
                    detection_before_investigation.model_dump(),
                )
                self.assertEqual(
                    len(repository.list_human_reviews_for_payment(payment_id)),
                    2,
                )

                print(
                    "\nInvestigation E2E validation: "
                    f"payments={len(account_payments.json())}, "
                    f"activity={len(activity.json())}, "
                    f"detection_results={len(payment_detections.json())}, "
                    f"reviews={len(reviews.json())}; "
                    "payment status/risk_status unchanged."
                )


def _seed_entities(repository: SQLitePaymentRepository) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repository.save_customer(
        SyntheticCustomer(
            customer_id="CUST-E2E",
            display_name="Synthetic E2E Customer",
            account_id="ACC-E2E-CUSTOMER",
            created_at=created_at,
        )
    )
    repository.save_merchant(
        SyntheticMerchant(
            merchant_id="MER-E2E",
            display_name="Synthetic E2E Merchant",
            account_id="ACC-E2E-MERCHANT",
            created_at=created_at,
        )
    )


def _detection_result(payment_id: str) -> DetectionResult:
    return DetectionResult(
        detection_result_id="DET-E2E-1",
        payment_id=payment_id,
        transaction_id=payment_id,
        protocol="stream_frozen_model",
        risk_score=23.5,
        risk_status="LOW",
        signals={
            "synthetic_test": RiskSignal(
                contribution=10,
                explanation="Synthetic integration-test signal.",
            )
        },
        created_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        disclaimer="Synthetic test result; not a production assessment.",
    )


def _offline_transaction(transaction_id, sender, receiver):
    return {
        "transaction_id": transaction_id,
        "sender": sender,
        "receiver": receiver,
        "amount_paise": 12500,
        "currency": "INR",
        "timestamp": "2026-01-01T12:00:00Z",
        "device_id": "DEV-E2E-OFFLINE",
        "ip_address": "192.0.2.10",
        "status": "SUCCESS",
        "scenario_label": "NORMAL",
    }


if __name__ == "__main__":
    unittest.main()
