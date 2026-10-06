import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

AI_ENGINE_PATH = Path(__file__).resolve().parent
if str(AI_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_PATH))

from fastapi.testclient import TestClient

import api
from payment_service.config import PaymentAPISettings
from payment_service.investigation_routes import get_investigation_repository
from payment_service.mongo_repository import MongoPaymentRepository
from payment_service.provider import FakePaymentProvider
from payment_service.routes import get_payment_service
from payment_service.schemas import (
    DetectionResult,
    Payment,
    PaymentStatus,
    RiskSignal,
    RiskStatus,
    SyntheticCustomer,
    SyntheticMerchant,
)
from payment_service.service import SyntheticPaymentService
from payment_service.sqlite_repository import SQLitePaymentRepository
from test_mongo_repository import _FakeMongoClient, _FakeMongoCollection


class InvestigationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "investigation-api.sqlite3"
        self.sqlite = SQLitePaymentRepository(self.database_path)
        _seed(self.sqlite)
        settings = PaymentAPISettings(
            enabled=True,
            database_path=self.database_path,
        )
        self.app = api.create_app(settings)
        self.app.dependency_overrides[get_payment_service] = lambda: (
            SyntheticPaymentService(
                self.sqlite,
                FakePaymentProvider(),
                clock=lambda: _timestamp(10),
            )
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.temp_dir.cleanup()

    def test_payment_lookup_success_and_not_found(self):
        response = self.client.get(
            "/v1/investigation/payments/PAY-0002"
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["payment_id"], "PAY-0002")
        self.assertEqual(body["detection_result_id"], "DET-0001")
        self.assertEqual(body["data_source"], "STREAMING_PAYMENT_SERVICE")
        self.assertNotIn("provider_payment_reference", body)
        self.assertNotIn("idempotency_key_digest", body)
        missing = self.client.get(
            "/v1/investigation/payments/PAY-MISSING"
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["error"]["code"],
            "payment_not_found",
        )

    def test_account_payments_and_activity_success(self):
        payments = self.client.get(
            "/v1/investigation/accounts/ACC-A/payments"
        )
        activity = self.client.get(
            "/v1/investigation/accounts/ACC-A/activity"
        )

        self.assertEqual(payments.status_code, 200, payments.text)
        self.assertEqual(
            [item["payment_id"] for item in payments.json()],
            ["PAY-0003", "PAY-0002", "PAY-0001"],
        )
        self.assertEqual(activity.status_code, 200, activity.text)
        self.assertEqual(
            [item["direction"] for item in activity.json()],
            ["OUTGOING", "INCOMING", "OUTGOING"],
        )
        self.assertTrue(
            all(
                item["data_source"] == "STREAMING_PAYMENT_SERVICE"
                for item in activity.json()
            )
        )

    def test_limits_are_bounded_and_rejected_cleanly(self):
        for value in ("0", "-1", "501", "not-a-number"):
            with self.subTest(limit=value):
                response = self.client.get(
                    "/v1/investigation/accounts/ACC-A/activity",
                    params={"limit": value},
                )
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("mongodb://", response.text)
        default = self.client.get(
            "/v1/investigation/accounts/ACC-A/payments"
        )
        explicit_default = self.client.get(
            "/v1/investigation/accounts/ACC-A/payments",
            params={"limit": "50"},
        )
        self.assertEqual(default.json(), explicit_default.json())
        excessive = self.client.get(
            "/v1/investigation/accounts/ACC-A/payments",
            params={"limit": "501"},
        )
        self.assertEqual(excessive.status_code, 422)

    def test_detection_result_lookup_and_not_found(self):
        found = self.client.get(
            "/v1/investigation/detection-results/DET-0001"
        )

        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(found.json()["transaction_id"], "PAY-0002")
        self.assertEqual(
            found.json()["data_source"],
            "STREAMING_PAYMENT_SERVICE",
        )
        missing = self.client.get(
            "/v1/investigation/detection-results/DET-MISSING"
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["error"]["code"],
            "detection_result_not_found",
        )

    def test_payment_detection_results_and_empty_account(self):
        response = self.client.get(
            "/v1/investigation/payments/PAY-0002/detection-results"
        )
        empty = self.client.get(
            "/v1/investigation/accounts/ACC-UNKNOWN/activity"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["detection_result_id"] for item in response.json()],
            ["DET-0001"],
        )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json(), [])

    def test_reads_do_not_mutate_payment_or_risk_state(self):
        before = {
            payment_id: self.sqlite.get_payment(payment_id).model_dump()
            for payment_id in ("PAY-0001", "PAY-0002", "PAY-0003")
        }
        for path in (
            "/v1/investigation/payments/PAY-0002",
            "/v1/investigation/accounts/ACC-A/payments",
            "/v1/investigation/accounts/ACC-A/activity",
            "/v1/investigation/detection-results/DET-0001",
            "/v1/investigation/payments/PAY-0002/detection-results",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
        after = {
            payment_id: self.sqlite.get_payment(payment_id).model_dump()
            for payment_id in ("PAY-0001", "PAY-0002", "PAY-0003")
        }
        self.assertEqual(before, after)

    def test_investigation_endpoints_disabled_with_payment_service(self):
        app = api.create_app(PaymentAPISettings(enabled=False))
        client = TestClient(app)

        paths = set(app.openapi()["paths"])
        self.assertFalse(
            any(path.startswith("/v1/investigation/") for path in paths)
        )
        self.assertEqual(
            client.get("/v1/investigation/accounts/ACC-A/activity").status_code,
            404,
        )
        self.assertEqual(client.get("/health").status_code, 200)

    def test_existing_payment_creation_endpoint_remains_functional(self):
        response = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "investigation-api-test"},
            json={
                "customer_id": "CUST-NEW",
                "merchant_id": "MER-NEW",
                "amount_paise": 1000,
                "currency": "INR",
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["payment_status"], "CAPTURED")
        self.assertEqual(response.json()["risk_status"], "NOT_EVALUATED")

    def test_fake_mongo_repository_endpoint_matches_sqlite(self):
        client = _FakeMongoClient()
        database = client["investigation_api"]
        mongo_repository = MongoPaymentRepository(client=client, database=database)
        patcher = patch.object(
            _FakeMongoCollection,
            "find",
            _fake_find,
            create=True,
        )
        patcher.start()
        try:
            _seed(mongo_repository)
            mongo_app = api.create_app(
                PaymentAPISettings(enabled=True, repository_backend="mongodb")
            )
            mongo_app.dependency_overrides[get_investigation_repository] = (
                lambda: mongo_repository
            )
            response = TestClient(mongo_app).get(
                "/v1/investigation/accounts/ACC-A/activity"
            )
            sqlite_response = self.client.get(
                "/v1/investigation/accounts/ACC-A/activity"
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), sqlite_response.json())
            mongo_app.dependency_overrides.clear()
        finally:
            patcher.stop()


class _FakeCursor:
    def __init__(self, documents):
        self.documents = copy.deepcopy(documents)

    def sort(self, sort_spec):
        for field, direction in reversed(sort_spec):
            self.documents.sort(
                key=lambda document: document[field],
                reverse=direction == -1,
            )
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(copy.deepcopy(self.documents))


def _fake_find(collection, query):
    alternatives = query.get("$or", [])
    matches = [
        document
        for document in collection.documents
        if any(
            all(document.get(key) == value for key, value in clause.items())
            for clause in alternatives
        )
    ]
    return _FakeCursor(matches)


def _timestamp(minute):
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minute)


def _seed(repository):
    repository.save_customer(
        SyntheticCustomer(
            customer_id="CUST-A",
            display_name="Synthetic customer A",
            account_id="ACC-A",
            created_at=_timestamp(0),
        )
    )
    repository.save_customer(
        SyntheticCustomer(
            customer_id="CUST-C",
            display_name="Synthetic customer C",
            account_id="ACC-C",
            created_at=_timestamp(0),
        )
    )
    for merchant_id, account_id in (
        ("MER-A", "ACC-A"),
        ("MER-B", "ACC-B"),
        ("MER-NEW", "ACC-NEW"),
    ):
        repository.save_merchant(
            SyntheticMerchant(
                merchant_id=merchant_id,
                display_name="Synthetic merchant",
                account_id=account_id,
                created_at=_timestamp(0),
            )
        )
    repository.save_customer(
        SyntheticCustomer(
            customer_id="CUST-NEW",
            display_name="Synthetic new customer",
            account_id="ACC-NEW-C",
            created_at=_timestamp(0),
        )
    )
    payment_specs = (
        ("PAY-0001", "CUST-A", "MER-B", 0),
        ("PAY-0002", "CUST-C", "MER-A", 1),
        ("PAY-0003", "CUST-A", "MER-B", 2),
    )
    for payment_id, customer_id, merchant_id, minute in payment_specs:
        payment = Payment(
            payment_id=payment_id,
            customer_id=customer_id,
            merchant_id=merchant_id,
            sender_account_id={"CUST-A": "ACC-A", "CUST-C": "ACC-C"}[customer_id],
            receiver_account_id={"MER-A": "ACC-A", "MER-B": "ACC-B"}[merchant_id],
            amount_paise=1000 + minute,
            currency="INR",
            payment_status=PaymentStatus.CAPTURED,
            risk_status=RiskStatus.NOT_EVALUATED,
            provider_name="fake",
            provider_payment_reference=f"FAKE-{payment_id}",
            idempotency_key_digest=f"{int(payment_id[-4:], 16) + 1:064x}",
            created_at=_timestamp(minute),
            updated_at=_timestamp(minute),
        )
        repository.get_or_create_payment(
            f"test:{payment_id}",
            payment.idempotency_key_digest,
            "e" * 64,
            payment,
        )
    result = DetectionResult(
        detection_result_id="DET-0001",
        payment_id="PAY-0002",
        transaction_id="PAY-0002",
        protocol="stream_frozen_model",
        risk_score=35,
        risk_status="MEDIUM",
        signals={
            "rule_based": RiskSignal(
                contribution=20,
                explanation="Synthetic investigation test.",
            )
        },
        created_at=_timestamp(3),
        disclaimer="Synthetic test data.",
    )
    repository.save_detection_result(result)
    linked = repository.get_payment("PAY-0002").model_copy(
        update={"detection_result_id": result.detection_result_id}
    )
    repository.update_payment(linked)


if __name__ == "__main__":
    unittest.main()
