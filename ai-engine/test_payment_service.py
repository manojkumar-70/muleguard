import hashlib
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from payment_service.errors import (
    IdempotencyConflictError,
    InvalidPaymentTransitionError,
    PaymentNotFoundError,
    RepositoryDatabaseError,
)
from payment_service.provider import (
    ControlledProviderFailure,
    FakePaymentProvider,
    ProviderResult,
)
from payment_service.schemas import (
    Payment,
    PaymentStatus,
    RiskStatus,
    SyntheticCustomer,
    SyntheticMerchant,
)
from payment_service.service import (
    CreatePaymentCommand,
    InactiveSyntheticEntityError,
    InvalidProviderOutcomeError,
    SyntheticEntityNotFoundError,
    SyntheticPaymentService,
)
from payment_service.sqlite_repository import SQLitePaymentRepository


class SyntheticPaymentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "service-test.sqlite3"
        self.repository = SQLitePaymentRepository(self.database_path)
        self.customer = _customer()
        self.merchant = _merchant()
        self.repository.save_customer(self.customer)
        self.repository.save_merchant(self.merchant)
        self.provider = FakePaymentProvider()
        self.service = SyntheticPaymentService(
            self.repository,
            self.provider,
            id_factory=_id_factory(),
            clock=lambda: _timestamp(),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_successful_synthetic_payment_creation_and_retrieval(self):
        payment = self.service.create_payment(_command(), "key-one")

        self.assertEqual(payment.payment_status, PaymentStatus.CAPTURED)
        self.assertEqual(payment.risk_status, RiskStatus.NOT_EVALUATED)
        self.assertEqual(payment.sender_account_id, self.customer.account_id)
        self.assertEqual(payment.receiver_account_id, self.merchant.account_id)
        self.assertEqual(payment.provider_name, "fake")
        self.assertTrue(payment.provider_payment_reference.startswith("FAKE-PAY-"))
        self.assertEqual(self.service.get_payment(payment.payment_id), payment)
        self.assertEqual(self.provider.calls, 1)

    def test_configured_decline_is_persisted_without_changing_risk_status(self):
        provider = FakePaymentProvider(outcome=PaymentStatus.DECLINED)
        service = self._service(provider)

        payment = service.create_payment(_command(), "decline-key")

        self.assertEqual(payment.payment_status, PaymentStatus.DECLINED)
        self.assertEqual(payment.risk_status, RiskStatus.NOT_EVALUATED)
        self.assertEqual(self.repository.get_payment(payment.payment_id), payment)

    def test_identical_retry_returns_original_and_avoids_second_provider_call(self):
        original = self.service.create_payment(_command(), "same-key")
        retry = self.service.create_payment(_command(), "same-key")

        self.assertEqual(retry, original)
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self._payment_count(), 1)

    def test_reusing_key_with_changed_amount_or_merchant_conflicts(self):
        self.service.create_payment(_command(), "conflict-key")
        changed_amount = _command(amount_paise=13000)

        with self.assertRaises(IdempotencyConflictError):
            self.service.create_payment(changed_amount, "conflict-key")

        self.repository.save_merchant(
            _merchant(
                merchant_id="MER-0002",
                account_id="ACC-M-0002",
            )
        )
        changed_merchant = _command(merchant_id="MER-0002")
        with self.assertRaises(IdempotencyConflictError):
            self.service.create_payment(changed_merchant, "conflict-key")

        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self._payment_count(), 1)

    def test_same_key_in_distinct_customer_scopes_creates_distinct_payments(self):
        other_customer = _customer(
            customer_id="CUST-0002",
            account_id="ACC-C-0002",
        )
        self.repository.save_customer(other_customer)
        first = self.service.create_payment(_command(), "shared-key")
        second_service = self._service(
            FakePaymentProvider(),
            id_factory=_id_factory(2),
        )
        second = second_service.create_payment(
            _command(customer_id=other_customer.customer_id),
            "shared-key",
        )

        self.assertNotEqual(first.payment_id, second.payment_id)
        self.assertEqual(first.idempotency_key_digest, second.idempotency_key_digest)
        self.assertEqual(self._payment_count(), 2)

    def test_payment_persists_across_repository_instances(self):
        payment = self.service.create_payment(_command(), "persistent-key")
        reopened = SQLitePaymentRepository(self.database_path)

        self.assertEqual(reopened.get_payment(payment.payment_id), payment)

    def test_missing_customer_or_merchant_rejects_before_provider(self):
        with self.assertRaises(SyntheticEntityNotFoundError):
            self.service.create_payment(
                _command(customer_id="CUST-MISSING"),
                "missing-customer",
            )
        with self.assertRaises(SyntheticEntityNotFoundError):
            self.service.create_payment(
                _command(merchant_id="MER-MISSING"),
                "missing-merchant",
            )
        self.assertEqual(self.provider.calls, 0)
        self.assertEqual(self._payment_count(), 0)

    def test_inactive_customer_or_merchant_rejects_before_provider(self):
        self.repository.save_customer(
            _customer(
                customer_id="CUST-0003",
                account_id="ACC-C-0003",
                status="DISABLED",
            )
        )
        self.repository.save_merchant(
            _merchant(
                merchant_id="MER-0003",
                account_id="ACC-M-0003",
                status="DISABLED",
            )
        )
        with self.assertRaises(InactiveSyntheticEntityError):
            self.service.create_payment(
                _command(customer_id="CUST-0003"),
                "inactive-customer",
            )
        with self.assertRaises(InactiveSyntheticEntityError):
            self.service.create_payment(
                _command(merchant_id="MER-0003"),
                "inactive-merchant",
            )
        self.assertEqual(self.provider.calls, 0)
        self.assertEqual(self._payment_count(), 0)

    def test_controlled_provider_failure_persists_bounded_failure_code(self):
        provider = FakePaymentProvider(failure_code="SIMULATED_UNAVAILABLE")
        service = self._service(provider)

        payment = service.create_payment(_command(), "provider-failure")

        self.assertEqual(payment.payment_status, PaymentStatus.FAILED)
        self.assertEqual(payment.failure_code, "SIMULATED_UNAVAILABLE")
        self.assertEqual(payment.risk_status, RiskStatus.NOT_EVALUATED)
        self.assertNotIn("secret", str(payment).lower())

    def test_database_update_failure_is_propagated_without_success_result(self):
        service = self._service(self.provider)
        with patch.object(
            self.repository,
            "update_payment",
            side_effect=RepositoryDatabaseError("injected persistence failure"),
        ):
            with self.assertRaisesRegex(
                RepositoryDatabaseError,
                "injected persistence failure",
            ):
                service.create_payment(_command(), "persistence-failure")
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(self._payment_count(), 1)
        self.assertEqual(self._get_only_payment().payment_status, PaymentStatus.CREATED)

    def test_noncontrolled_provider_failure_leaves_created_for_safe_retry(self):
        class FailThenReturnProvider:
            provider_name = "fake"

            def __init__(self):
                self.calls = 0

            def create_payment(self, payment):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient synthetic test failure")
                return ProviderResult(
                    provider_payment_reference="FAKE-PAY-0123456789abcdef",
                    payment_status=PaymentStatus.CAPTURED,
                )

        provider = FailThenReturnProvider()
        service = self._service(provider)
        with self.assertRaisesRegex(RuntimeError, "transient synthetic"):
            service.create_payment(_command(), "retry-stranded")

        created = self._get_only_payment()
        self.assertEqual(created.payment_status, PaymentStatus.CREATED)
        completed = service.create_payment(_command(), "retry-stranded")

        self.assertEqual(completed.payment_status, PaymentStatus.CAPTURED)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(self._payment_count(), 1)

    def test_invalid_provider_outcome_is_rejected_without_state_change(self):
        provider = FakePaymentProvider(outcome=PaymentStatus.EXPIRED)
        service = self._service(provider)

        with self.assertRaises(InvalidProviderOutcomeError):
            service.create_payment(_command(), "invalid-outcome")

        self.assertEqual(self._get_only_payment().payment_status, PaymentStatus.CREATED)
        self.assertEqual(provider.calls, 1)

    def test_invalid_fake_outcome_and_failure_code_are_rejected(self):
        with self.assertRaises(ValueError):
            FakePaymentProvider(outcome=PaymentStatus.CREATED)
        with self.assertRaises(ValueError):
            FakePaymentProvider(failure_code="contains unsafe details")
        with self.assertRaises(ValidationError):
            ProviderResult(
                provider_payment_reference="real-provider-reference",
                payment_status=PaymentStatus.CAPTURED,
            )

    def test_service_returns_typed_not_found_for_unknown_payment(self):
        with self.assertRaises(PaymentNotFoundError):
            self.service.get_payment("PAY-0000000000000000")

    def test_idempotency_key_is_only_hashed_and_never_sent_to_provider(self):
        payment = self.service.create_payment(_command(), "unique-secret-key")

        self.assertEqual(
            payment.idempotency_key_digest,
            hashlib.sha256(b"unique-secret-key").hexdigest(),
        )
        self.assertNotIn("unique-secret-key", repr(payment))
        with self.repository._database.connection() as connection:
            payment_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(payments)")
            }
        self.assertNotIn("idempotency_key", payment_columns)

    def test_provider_receives_no_labels_roles_credentials_or_raw_payload(self):
        class CapturingProvider:
            provider_name = "fake"

            def __init__(self):
                self.payment = None

            def create_payment(self, payment):
                self.payment = payment
                return ProviderResult(
                    provider_payment_reference="FAKE-PAY-0123456789abcdef",
                    payment_status=PaymentStatus.CAPTURED,
                )

        provider = CapturingProvider()
        service = self._service(provider)
        service.create_payment(_command(), "boundary-key")
        provider_fields = set(provider.payment.model_dump())

        self.assertNotIn("scenario_label", provider_fields)
        self.assertNotIn("evaluation_role", provider_fields)
        self.assertNotIn("account_role", provider_fields)
        self.assertNotIn("api_key", provider_fields)
        self.assertNotIn("secret", provider_fields)
        self.assertNotIn("raw_payload", provider_fields)

    def test_service_does_not_invoke_detection_or_streaming_modules(self):
        service = self._service(self.provider)
        with (
            patch(
                "anomaly_detection.detect_account_anomalies",
                side_effect=AssertionError("detector must not be invoked"),
            ),
            patch(
                "rule_based_analysis.analyze_accounts",
                side_effect=AssertionError("rules must not be invoked"),
            ),
            patch(
                "transaction_graph_analysis.analyze_transaction_graph",
                side_effect=AssertionError("graph must not be invoked"),
            ),
            patch(
                "risk_aggregation.aggregate_risk",
                side_effect=AssertionError("aggregation must not be invoked"),
            ),
            patch(
                "synthetic_replay.replay_transactions",
                side_effect=AssertionError("strict replay must not be invoked"),
            ),
            patch(
                "synthetic_streaming.run_synthetic_stream",
                side_effect=AssertionError("stream must not be invoked"),
            ),
        ):
            payment = service.create_payment(_command(), "no-detector-key")

        self.assertEqual(payment.risk_status, RiskStatus.NOT_EVALUATED)

    def test_concurrent_duplicate_calls_create_one_payment(self):
        provider = FakePaymentProvider()
        service = self._service(provider)
        barrier = threading.Barrier(2)
        results = []
        failures = []

        def create():
            try:
                barrier.wait(timeout=5)
                results.append(
                    service.create_payment(_command(), "concurrent-service-key")
                )
            except Exception as error:
                failures.append(error)

        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(failures)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].payment_id, results[1].payment_id)
        self.assertEqual(results[0].payment_status, PaymentStatus.CAPTURED)
        self.assertEqual(self._payment_count(), 1)
        self.assertLessEqual(provider.calls, 2)

    def test_command_rejects_evaluation_and_secret_fields(self):
        for extra in (
            {"scenario_label": "NORMAL"},
            {"evaluation_role": "FOCAL_SUSPICIOUS"},
            {"account_role_metadata": {"role": "NORMAL"}},
            {"api_key": "secret"},
            {"raw_payload": {}},
        ):
            with self.subTest(extra=extra):
                with self.assertRaises(ValidationError):
                    CreatePaymentCommand(**(_command().model_dump() | extra))

    def test_provider_failure_persistence_failure_is_not_masked(self):
        provider = FakePaymentProvider(failure_code="SIMULATED_FAILURE")
        service = self._service(provider)
        with patch.object(
            self.repository,
            "update_payment",
            side_effect=RepositoryDatabaseError("failure status could not persist"),
        ):
            with self.assertRaisesRegex(
                RepositoryDatabaseError,
                "failure status could not persist",
            ):
                service.create_payment(_command(), "failure-persist-failure")

    def _service(self, provider, id_factory=None):
        return SyntheticPaymentService(
            self.repository,
            provider,
            id_factory=id_factory or _id_factory(),
            clock=lambda: _timestamp(),
        )

    def _payment_count(self):
        with self.repository._database.connection() as connection:
            return connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0]

    def _get_only_payment(self):
        with self.repository._database.connection() as connection:
            row = connection.execute(
                "SELECT payment_id FROM payments"
            ).fetchone()
        self.assertIsNotNone(row)
        payment = self.repository.get_payment(row["payment_id"])
        self.assertIsNotNone(payment)
        return payment


def _timestamp():
    return datetime(2025, 1, 1, tzinfo=timezone.utc)


def _id_factory(seed=1):
    counter = iter(range(seed, 1000000))
    lock = threading.Lock()

    def generate():
        with lock:
            value = next(counter)
        return f"{value:032x}"

    return generate


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
        "status": "ACTIVE",
        "currency": "INR",
        "created_at": _timestamp(),
    }
    values.update(overrides)
    return SyntheticMerchant(**values)


def _command(**overrides):
    values = {
        "customer_id": "CUST-0001",
        "merchant_id": "MER-0001",
        "amount_paise": 12500,
        "currency": "INR",
    }
    values.update(overrides)
    return CreatePaymentCommand(**values)


if __name__ == "__main__":
    unittest.main()
