import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import api
from payment_service.config import PaymentAPISettings
from payment_service.errors import RepositoryDatabaseError
from payment_service.http_schemas import CreatePaymentRequest, PublicPayment
from payment_service.provider import FakePaymentProvider, ProviderResult
from payment_service.schemas import (
    Payment,
    PaymentStatus,
    SyntheticCustomer,
    SyntheticMerchant,
)
from payment_service.routes import get_payment_service
from payment_service.service import SyntheticPaymentService
from payment_service.sqlite_database import DEFAULT_DATABASE_PATH
from payment_service.sqlite_repository import SQLitePaymentRepository


class SyntheticPaymentAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "payment-api-test.sqlite3"
        self.repository = SQLitePaymentRepository(self.database_path)
        self.repository.save_customer(_customer())
        self.repository.save_merchant(_merchant())
        self.service = SyntheticPaymentService(
            self.repository,
            FakePaymentProvider(),
            clock=lambda: datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        self.app = api.create_app(
            PaymentAPISettings(enabled=True, database_path=self.database_path)
        )
        self.app.dependency_overrides[get_payment_service] = lambda: self.service
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.temp_dir.cleanup()

    def test_payment_api_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            app = api.create_app()

        paths = set(app.openapi()["paths"])
        self.assertNotIn("/v1/payments", paths)
        self.assertNotIn("/v1/payments/{payment_id}", paths)
        self.assertEqual(TestClient(app).get("/health").status_code, 200)
        self.assertEqual(TestClient(app).get("/v1/payments/PAY-missing").status_code, 404)

    def test_existing_routes_work_while_payment_api_is_disabled(self):
        app = api.create_app(PaymentAPISettings(enabled=False))
        client = TestClient(app)

        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/summary").status_code, 200)
        self.assertEqual(client.get("/alerts").status_code, 200)
        self.assertEqual(client.get("/accounts/ACC-M-000001").status_code, 200)
        analyze_response = client.post(
            "/analyze",
            json={"transactions": [_transaction()]},
        )
        self.assertEqual(analyze_response.status_code, 200, analyze_response.text)
        self.assertEqual(
            set(analyze_response.json()),
            {"transaction_count", "summary", "graph_summary", "accounts"},
        )

    def test_enabled_payment_api_registers_only_two_payment_routes(self):
        paths = set(self.app.openapi()["paths"])
        self.assertIn("/v1/payments", paths)
        self.assertIn("/v1/payments/{payment_id}", paths)
        self.assertEqual(
            {path for path in paths if path.startswith("/v1/payments")},
            {"/v1/payments", "/v1/payments/{payment_id}"},
        )
        self.assertTrue(
            {"/health", "/summary", "/alerts", "/accounts/{account_id}", "/analyze"}
            .issubset(paths)
        )

    def test_payment_dependency_override_uses_temporary_database(self):
        response = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "temporary-db-key"},
            json=_request(),
        )

        self.assertEqual(response.status_code, 201, response.text)
        stored = SQLitePaymentRepository(self.database_path).get_payment(
            response.json()["payment_id"]
        )
        self.assertIsNotNone(stored)

    def test_repository_selection_defaults_to_sqlite_and_reads_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = PaymentAPISettings.from_environment()
        self.assertEqual(settings.repository_backend, "sqlite")

        with patch.dict(
            os.environ,
            {
                "MULEGUARD_PAYMENT_REPOSITORY": "mongodb",
                "MONGODB_URI": "mongodb://test.invalid",
                "MONGODB_DATABASE": "payments_test",
            },
            clear=True,
        ):
            settings = PaymentAPISettings.from_environment()
        self.assertEqual(settings.repository_backend, "mongodb")

    def test_missing_mongodb_configuration_fails_without_exposing_values(self):
        with patch.dict(
            os.environ,
            {"MULEGUARD_PAYMENT_REPOSITORY": "mongodb"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                ValueError, "MONGODB_URI.*MONGODB_DATABASE"
            ) as raised:
                PaymentAPISettings.from_environment()
        self.assertNotIn("mongodb://", str(raised.exception))

        settings = PaymentAPISettings(
            enabled=True,
            repository_backend="mongodb",
        )
        app = api.create_app(settings)
        response = TestClient(app).post(
            "/v1/payments",
            headers={"Idempotency-Key": "missing-mongo-config"},
            json=_request(),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "payment_storage_unavailable",
        )
        self.assertNotIn("mongodb://", response.text)

    def test_sqlite_repository_selection_uses_configured_sqlite_repository(self):
        app = api.create_app(
            PaymentAPISettings(
                enabled=True,
                database_path=self.database_path,
                repository_backend="sqlite",
            )
        )
        client = TestClient(app)
        with (
            patch(
                "payment_service.routes.SQLitePaymentRepository",
                return_value=self.repository,
            ) as sqlite_repository,
            patch("payment_service.routes.MongoPaymentRepository") as mongo_repository,
        ):
            response = client.post(
                "/v1/payments",
                headers={"Idempotency-Key": "selected-sqlite"},
                json=_request(),
            )

        self.assertEqual(response.status_code, 201, response.text)
        sqlite_repository.assert_called_once_with(path=self.database_path)
        mongo_repository.assert_not_called()
        self.assertIsNotNone(
            self.repository.get_payment(response.json()["payment_id"])
        )

    def test_mongodb_repository_selection_serves_api_and_preserves_idempotency(self):
        app = api.create_app(
            PaymentAPISettings(
                enabled=True,
                repository_backend="mongodb",
            )
        )
        client = TestClient(app)
        headers = {"Idempotency-Key": "selected-mongodb"}
        with (
            patch("payment_service.routes.MongoPaymentRepository") as mongo_repository,
            patch("payment_service.routes.SQLitePaymentRepository") as sqlite_repository,
        ):
            mongo_repository.return_value = self.repository
            first = client.post("/v1/payments", headers=headers, json=_request())
            repeated = client.post("/v1/payments", headers=headers, json=_request())
            retrieved = client.get(
                f"/v1/payments/{first.json()['payment_id']}"
            )

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), first.json())
        self.assertEqual(retrieved.status_code, 200, retrieved.text)
        self.assertEqual(retrieved.json(), first.json())
        self.assertEqual(mongo_repository.call_count, 3)
        sqlite_repository.assert_not_called()

    def test_default_database_is_not_constructed_when_disabled_or_overridden(self):
        existed_before = DEFAULT_DATABASE_PATH.exists()
        modified_before = (
            DEFAULT_DATABASE_PATH.stat().st_mtime_ns if existed_before else None
        )
        with patch("payment_service.routes.SQLitePaymentRepository") as repository:
            disabled = api.create_app(PaymentAPISettings(enabled=False))
            TestClient(disabled).get("/health")

            enabled = api.create_app(
                PaymentAPISettings(enabled=True, database_path=self.database_path)
            )
            enabled.dependency_overrides[get_payment_service] = lambda: self.service
            TestClient(enabled).post(
                "/v1/payments",
                headers={"Idempotency-Key": "override-key"},
                json=_request(),
            )
            repository.assert_not_called()

        self.assertEqual(DEFAULT_DATABASE_PATH.exists(), existed_before)
        if existed_before:
            self.assertEqual(
                DEFAULT_DATABASE_PATH.stat().st_mtime_ns, modified_before
            )
        enabled.dependency_overrides.clear()

    def test_successful_creation_retrieval_and_public_response_fields(self):
        raw_key = "private-idempotency-key"
        created = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": raw_key},
            json=_request(),
        )

        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertEqual(
            set(body),
            {
                "payment_id",
                "customer_id",
                "merchant_id",
                "amount_paise",
                "currency",
                "payment_status",
                "risk_status",
                "created_at",
                "updated_at",
                "provider_status_updated_at",
            },
        )
        self.assertEqual(body["payment_status"], "CAPTURED")
        self.assertEqual(body["risk_status"], "NOT_EVALUATED")
        self.assertNotIn(raw_key, created.text)
        self.assertNotIn("idempotency_key_digest", created.text)
        self.assertNotIn("request_digest", created.text)
        self.assertNotIn("provider_payment_reference", body)

        retrieved = self.client.get(f"/v1/payments/{body['payment_id']}")
        self.assertEqual(retrieved.status_code, 200, retrieved.text)
        self.assertEqual(retrieved.json(), body)

    def test_identical_idempotent_retry_returns_http_200_and_same_payment(self):
        headers = {"Idempotency-Key": "repeat-key"}
        first = self.client.post("/v1/payments", headers=headers, json=_request())
        second = self.client.post("/v1/payments", headers=headers, json=_request())

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(self.service._provider.calls, 1)

    def test_changed_amount_or_merchant_with_same_key_returns_conflict(self):
        headers = {"Idempotency-Key": "conflicting-key"}
        created = self.client.post("/v1/payments", headers=headers, json=_request())
        changed_amount = self.client.post(
            "/v1/payments",
            headers=headers,
            json=_request(amount_paise=15000),
        )
        self.repository.save_merchant(
            _merchant(merchant_id="MER-0002", account_id="ACC-M-0002")
        )
        changed_merchant = self.client.post(
            "/v1/payments",
            headers=headers,
            json=_request(merchant_id="MER-0002"),
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(changed_amount.status_code, 409)
        self.assertEqual(changed_merchant.status_code, 409)
        self.assertEqual(changed_amount.json()["error"]["code"], "idempotency_conflict")
        self.assertNotIn("digest", changed_amount.text)

    def test_missing_or_invalid_idempotency_key_is_rejected_safely(self):
        missing = self.client.post("/v1/payments", json=_request())
        invalid = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "contains space"},
            json=_request(),
        )

        self.assertEqual(missing.status_code, 422)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(missing.json()["error"]["code"], "invalid_request")
        self.assertNotIn("contains space", invalid.text)

    def test_invalid_ids_amount_currency_and_extra_fields_are_rejected(self):
        invalid_requests = (
            _request(customer_id="customer-1"),
            _request(merchant_id="merchant-1"),
            _request(amount_paise=0),
            _request(amount_paise=1.5),
            _request(currency="USD"),
            _request(scenario_label="SYNTHETIC_SUSPICIOUS"),
            _request(evaluation_role="FOCAL_SUSPICIOUS"),
            _request(api_key="do-not-echo"),
        )
        for body in invalid_requests:
            with self.subTest(body=body):
                response = self.client.post(
                    "/v1/payments",
                    headers={"Idempotency-Key": "validation-key"},
                    json=body,
                )
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("do-not-echo", response.text)
                self.assertNotIn("SYNTHETIC_SUSPICIOUS", response.text)

    def test_missing_customer_or_merchant_returns_not_found(self):
        missing_customer = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "missing-customer"},
            json=_request(customer_id="CUST-MISSING"),
        )
        missing_merchant = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "missing-merchant"},
            json=_request(merchant_id="MER-MISSING"),
        )

        self.assertEqual(missing_customer.status_code, 404)
        self.assertEqual(missing_merchant.status_code, 404)
        self.assertEqual(
            missing_customer.json()["error"]["code"],
            "synthetic_entity_not_found",
        )

    def test_inactive_customer_or_merchant_returns_conflict(self):
        self.repository.save_customer(
            _customer(customer_id="CUST-OFF", account_id="ACC-C-OFF", status="DISABLED")
        )
        self.repository.save_merchant(
            _merchant(merchant_id="MER-OFF", account_id="ACC-M-OFF", status="DISABLED")
        )
        inactive_customer = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "inactive-customer"},
            json=_request(customer_id="CUST-OFF"),
        )
        inactive_merchant = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "inactive-merchant"},
            json=_request(merchant_id="MER-OFF"),
        )

        self.assertEqual(inactive_customer.status_code, 409)
        self.assertEqual(inactive_merchant.status_code, 409)

    def test_declined_payment_and_controlled_provider_failure_are_public_statuses(self):
        self._override_service(
            SyntheticPaymentService(
                self.repository,
                FakePaymentProvider(outcome=PaymentStatus.DECLINED),
            )
        )
        declined = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "declined-key"},
            json=_request(),
        )
        self.assertEqual(declined.status_code, 201)
        self.assertEqual(declined.json()["payment_status"], "DECLINED")
        self.assertEqual(declined.json()["risk_status"], "NOT_EVALUATED")

        self._override_service(
            SyntheticPaymentService(
                self.repository,
                FakePaymentProvider(failure_code="SIMULATED_UNAVAILABLE"),
            )
        )
        failed = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "controlled-failure"},
            json=_request(amount_paise=20000),
        )
        self.assertEqual(failed.status_code, 201)
        self.assertEqual(failed.json()["payment_status"], "FAILED")
        self.assertEqual(failed.json()["failure_code"], "SIMULATED_UNAVAILABLE")
        self.assertEqual(failed.json()["risk_status"], "NOT_EVALUATED")

    def test_invalid_provider_outcome_returns_sanitized_502(self):
        class InvalidOutcomeProvider:
            provider_name = "fake"

            def create_payment(self, payment: Payment):
                return ProviderResult(
                    provider_payment_reference="FAKE-PAY-0123456789abcdef",
                    payment_status=PaymentStatus.CREATED,
                )

        self._override_service(
            SyntheticPaymentService(self.repository, InvalidOutcomeProvider())
        )
        response = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "invalid-outcome"},
            json=_request(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "invalid_provider_outcome")
        self.assertNotIn("traceback", response.text.lower())

    def test_unexpected_provider_failure_is_sanitized_and_not_success_shaped(self):
        class ExplodingProvider:
            provider_name = "fake"

            def create_payment(self, _payment):
                raise RuntimeError("provider-secret-must-not-leak")

        self._override_service(
            SyntheticPaymentService(self.repository, ExplodingProvider())
        )
        response = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "unexpected-provider-failure"},
            json=_request(),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "synthetic_provider_error")
        self.assertNotIn("provider-secret-must-not-leak", response.text)
        self.assertNotIn("payment_status", response.json())

    def test_database_failure_returns_sanitized_503(self):
        with patch.object(
            self.repository,
            "get_customer",
            side_effect=RepositoryDatabaseError("private-database-path"),
        ):
            response = self.client.post(
                "/v1/payments",
                headers={"Idempotency-Key": "database-failure"},
                json=_request(),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"], "payment_storage_unavailable"
        )
        self.assertNotIn("private-database-path", response.text)
        self.assertNotIn("payment_status", response.json())

    def test_unknown_payment_returns_sanitized_not_found(self):
        response = self.client.get("/v1/payments/PAY-unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "payment_not_found")

    def test_payment_request_and_response_models_exclude_internal_fields(self):
        with self.assertRaises(ValidationError):
            CreatePaymentRequest(**(_request() | {"scenario_label": "NORMAL"}))

        response = self.client.post(
            "/v1/payments",
            headers={"Idempotency-Key": "model-boundary"},
            json=_request(),
        )
        public = PublicPayment.model_validate(response.json())
        self.assertEqual(public.risk_status.value, "NOT_EVALUATED")
        self.assertNotIn("idempotency_key_digest", PublicPayment.model_fields)
        self.assertNotIn("provider_payment_reference", PublicPayment.model_fields)

    def test_payment_api_does_not_invoke_detection_or_analysis_modules(self):
        with (
            patch("api.analyze_accounts") as rules,
            patch("api.detect_account_anomalies") as anomalies,
            patch("api.analyze_transaction_graph") as graph,
            patch("api.aggregate_risk") as aggregation,
            patch("api._run_analysis") as full_analysis,
            patch("synthetic_replay.replay_transactions") as replay,
            patch("synthetic_streaming.run_synthetic_stream") as streaming,
        ):
            response = self.client.post(
                "/v1/payments",
                headers={"Idempotency-Key": "no-detector-call"},
                json=_request(),
            )

        self.assertEqual(response.status_code, 201, response.text)
        for mocked in (
            rules,
            anomalies,
            graph,
            aggregation,
            full_analysis,
            replay,
            streaming,
        ):
            mocked.assert_not_called()

    def _override_service(self, service):
        self.service = service
        self.app.dependency_overrides[get_payment_service] = lambda: service


def _timestamp():
    return datetime(2025, 1, 1, tzinfo=timezone.utc)


def _customer(**overrides):
    values = {
        "customer_id": "CUST-0001",
        "display_name": "Synthetic Customer",
        "account_id": "ACC-C-0001",
        "status": "ACTIVE",
        "created_at": _timestamp(),
    }
    values.update(overrides)
    return SyntheticCustomer(**values)


def _merchant(**overrides):
    values = {
        "merchant_id": "MER-0001",
        "display_name": "Synthetic Merchant",
        "account_id": "ACC-M-0001",
        "currency": "INR",
        "status": "ACTIVE",
        "created_at": _timestamp(),
    }
    values.update(overrides)
    return SyntheticMerchant(**values)


def _request(**overrides):
    values = {
        "customer_id": "CUST-0001",
        "merchant_id": "MER-0001",
        "amount_paise": 12500,
        "currency": "INR",
    }
    values.update(overrides)
    return values


def _transaction():
    return {
        "transaction_id": "TXN-API-001",
        "sender": "ACC-TEST-001",
        "receiver": "ACC-TEST-002",
        "amount_paise": 12500,
        "currency": "INR",
        "timestamp": "2025-01-01T12:00:00Z",
        "device_id": "DEV-API-001",
        "ip_address": "192.0.2.10",
        "status": "SUCCESS",
        "scenario_label": "NORMAL",
    }


if __name__ == "__main__":
    unittest.main()
