import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from payment_service.errors import (
    IdempotencyConflictError,
    InvalidPaymentTransitionError,
    PaymentNotFoundError,
    ProviderEventConflictError,
    RepositoryConstraintError,
)
from payment_service.schemas import (
    DetectionResult,
    HumanReview,
    HumanReviewDecision,
    Payment,
    PaymentStatus,
    ProviderEvent,
    ProviderEventProcessingStatus,
    ProviderSignatureStatus,
    RiskSignal,
    RiskStatus,
    SimulatedIntervention,
    SimulatedInterventionType,
    SyntheticCustomer,
    SyntheticMerchant,
    ALLOWED_PAYMENT_TRANSITIONS,
)
from payment_service.sqlite_database import SCHEMA_VERSION, SQLiteDatabase
from payment_service.sqlite_repository import SQLitePaymentRepository


class SQLitePaymentRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "payments.sqlite3"
        self.repository = SQLitePaymentRepository(self.database_path)
        self.customer = _customer()
        self.merchant = _merchant()
        self.repository.save_customer(self.customer)
        self.repository.save_merchant(self.merchant)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initialization_is_repeatable_and_sets_schema_version(self):
        database = SQLiteDatabase(self.database_path, busy_timeout_ms=3250)
        database.initialize()
        database.initialize()
        with database.connection() as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION,
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("PRAGMA busy_timeout").fetchone()[0],
                3250,
            )
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = ?",
                    ("table",),
                )
            }
        self.assertTrue(
            {
                "synthetic_customers",
                "synthetic_merchants",
                "payments",
                "provider_events",
                "detection_results",
                "human_reviews",
                "simulated_interventions",
            }.issubset(table_names)
        )

    def test_customer_and_merchant_persist_after_reopen(self):
        reopened = SQLitePaymentRepository(self.database_path)

        self.assertEqual(reopened.get_customer(self.customer.customer_id), self.customer)
        self.assertEqual(reopened.get_merchant(self.merchant.merchant_id), self.merchant)
        self.assertIsNone(reopened.get_customer("CUST-MISSING"))
        self.assertIsNone(reopened.get_merchant("MER-MISSING"))

    def test_payment_foreign_keys_require_customer_and_merchant(self):
        missing_customer = _payment(customer_id="CUST-MISSING")
        with self.assertRaises(RepositoryConstraintError):
            self.repository.get_or_create_payment(
                "scope-a", missing_customer.idempotency_key_digest, "a" * 64,
                missing_customer,
            )

        missing_merchant = _payment(
            payment_id="PAY-0002",
            idempotency_key_digest="c" * 64,
            merchant_id="MER-MISSING",
        )
        with self.assertRaises(RepositoryConstraintError):
            self.repository.get_or_create_payment(
                "scope-a", missing_merchant.idempotency_key_digest, "c" * 64,
                missing_merchant,
            )

    def test_payment_accounts_must_match_customer_and_merchant(self):
        payment = _payment(sender_account_id="ACC-C-OTHER")

        with self.assertRaisesRegex(RepositoryConstraintError, "sender_account_id"):
            self.repository.get_or_create_payment(
                "account-mismatch",
                payment.idempotency_key_digest,
                "a" * 64,
                payment,
            )
        self.assertEqual(self._count("payments"), 0)

    def test_idempotent_creation_returns_original_persisted_payment(self):
        payment = _payment()
        created, was_created = self.repository.get_or_create_payment(
            "customer:CUST-0001:create",
            payment.idempotency_key_digest,
            "a" * 64,
            payment,
        )
        repeated, was_created_again = self.repository.get_or_create_payment(
            "customer:CUST-0001:create",
            payment.idempotency_key_digest,
            "a" * 64,
            payment,
        )

        self.assertTrue(was_created)
        self.assertFalse(was_created_again)
        self.assertEqual(created, payment)
        self.assertEqual(repeated, payment)
        self.assertEqual(self._count("payments"), 1)

    def test_conflicting_request_for_same_idempotency_key_raises(self):
        payment = _payment()
        self.repository.get_or_create_payment(
            "same-scope", payment.idempotency_key_digest, "a" * 64, payment
        )

        with self.assertRaises(IdempotencyConflictError):
            self.repository.get_or_create_payment(
                "same-scope", payment.idempotency_key_digest, "b" * 64, payment
            )
        self.assertEqual(self._count("payments"), 1)

    def test_idempotency_keys_are_scoped(self):
        first = _payment()
        second = _payment(
            payment_id="PAY-0002",
            idempotency_key_digest=first.idempotency_key_digest,
        )
        created_first, first_new = self.repository.get_or_create_payment(
            "customer-one", first.idempotency_key_digest, "a" * 64, first
        )
        created_second, second_new = self.repository.get_or_create_payment(
            "customer-two", second.idempotency_key_digest, "a" * 64, second
        )

        self.assertTrue(first_new)
        self.assertTrue(second_new)
        self.assertNotEqual(created_first.payment_id, created_second.payment_id)
        self.assertEqual(self._count("payments"), 2)

    def test_payment_retrieval_preserves_integer_amount_and_timezone_offset(self):
        offset_time = datetime(
            2025, 1, 1, 12, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )
        payment = _payment(created_at=offset_time, updated_at=offset_time)
        self.repository.get_or_create_payment(
            "scope", payment.idempotency_key_digest, "a" * 64, payment
        )
        reopened = SQLitePaymentRepository(self.database_path)
        stored = reopened.get_payment(payment.payment_id)

        self.assertEqual(stored.amount_paise, 12500)
        self.assertEqual(stored.created_at, offset_time)
        self.assertEqual(stored.created_at.utcoffset(), timedelta(hours=5, minutes=30))

    def test_payment_update_accepts_all_configured_valid_transitions(self):
        for index, (current_status, targets) in enumerate(
            ALLOWED_PAYMENT_TRANSITIONS.items()
        ):
            for target_status in targets:
                with self.subTest(current=current_status, target=target_status):
                    payment_id = f"PAY-{index:02d}-{target_status.value}"
                    key_digest = _hex_digest(index * 10 + len(target_status.value))
                    payment = _payment(
                        payment_id=payment_id,
                        idempotency_key_digest=key_digest,
                        payment_status=current_status,
                    )
                    self.repository.get_or_create_payment(
                        f"scope-{payment_id}",
                        key_digest,
                        _hex_digest(index),
                        payment,
                    )
                    updated = payment.model_copy(
                        update={
                            "payment_status": target_status,
                            "updated_at": _timestamp(2),
                        }
                    )
                    self.repository.update_payment(updated)
                    self.assertEqual(
                        self.repository.get_payment(payment_id).payment_status,
                        target_status,
                    )

    def test_invalid_payment_transitions_leave_record_unchanged(self):
        payment = _payment(payment_status=PaymentStatus.CAPTURED)
        self.repository.get_or_create_payment(
            "terminal", payment.idempotency_key_digest, "a" * 64, payment
        )
        changed = payment.model_copy(
            update={
                "payment_status": PaymentStatus.PENDING,
                "updated_at": _timestamp(3),
            }
        )

        with self.assertRaises(InvalidPaymentTransitionError):
            self.repository.update_payment(changed)
        self.assertEqual(
            self.repository.get_payment(payment.payment_id).payment_status,
            PaymentStatus.CAPTURED,
        )

    def test_risk_only_update_does_not_change_payment_status(self):
        payment = _payment(payment_status=PaymentStatus.CAPTURED)
        self.repository.get_or_create_payment(
            "risk", payment.idempotency_key_digest, "a" * 64, payment
        )
        updated = payment.model_copy(
            update={
                "risk_status": RiskStatus.HIGH,
                "updated_at": _timestamp(2),
            }
        )
        self.repository.update_payment(updated)
        stored = self.repository.get_payment(payment.payment_id)

        self.assertEqual(stored.payment_status, PaymentStatus.CAPTURED)
        self.assertEqual(stored.risk_status, RiskStatus.HIGH)

    def test_immutable_payment_fields_cannot_be_changed(self):
        payment = _payment()
        self.repository.get_or_create_payment(
            "immutable", payment.idempotency_key_digest, "a" * 64, payment
        )
        changed = payment.model_copy(update={"amount_paise": 99999})

        with self.assertRaisesRegex(ValueError, "amount_paise.*immutable"):
            self.repository.update_payment(changed)
        self.assertEqual(
            self.repository.get_payment(payment.payment_id).amount_paise,
            payment.amount_paise,
        )

    def test_provider_event_duplicate_and_digest_conflict(self):
        event = _event()

        self.assertTrue(self.repository.record_provider_event_once(event))
        self.assertFalse(self.repository.record_provider_event_once(event))
        self.assertEqual(self._count("provider_events"), 1)

        conflicting = event.model_copy(update={"payload_digest": "d" * 64})
        with self.assertRaises(ProviderEventConflictError):
            self.repository.record_provider_event_once(conflicting)
        self.assertEqual(self._count("provider_events"), 1)

    def test_atomic_webhook_processing_updates_event_and_payment_together(self):
        payment = _payment(provider_payment_reference="FAKE-PAYMENT-0001")
        self.repository.get_or_create_payment(
            "webhook", payment.idempotency_key_digest, "a" * 64, payment
        )
        updated = payment.model_copy(
            update={
                "payment_status": PaymentStatus.PENDING,
                "updated_at": _timestamp(2),
                "provider_status_updated_at": _timestamp(2),
            }
        )
        event = _event(normalized_status=PaymentStatus.PENDING)

        self.assertTrue(
            self.repository.record_provider_event_and_update_payment(event, updated)
        )
        self.assertEqual(
            self.repository.get_payment(payment.payment_id).payment_status,
            PaymentStatus.PENDING,
        )
        self.assertFalse(
            self.repository.record_provider_event_and_update_payment(event, updated)
        )
        self.assertEqual(self._count("provider_events"), 1)
        with self._database_connection() as connection:
            stored_event = connection.execute(
                "SELECT processing_status FROM provider_events WHERE provider_name = ?",
                ("fake",),
            ).fetchone()
        self.assertEqual(
            stored_event["processing_status"],
            ProviderEventProcessingStatus.PROCESSED,
        )

    def test_atomic_webhook_rejects_unverified_or_mismatched_status(self):
        payment = _payment(provider_payment_reference="FAKE-PAYMENT-0001")
        self.repository.get_or_create_payment(
            "webhook-invalid", payment.idempotency_key_digest, "a" * 64, payment
        )
        target = payment.model_copy(
            update={"payment_status": PaymentStatus.PENDING}
        )

        with self.assertRaisesRegex(ValueError, "Only verified"):
            self.repository.record_provider_event_and_update_payment(
                _event(signature_status=ProviderSignatureStatus.INVALID), target
            )
        with self.assertRaisesRegex(ValueError, "must match"):
            self.repository.record_provider_event_and_update_payment(
                _event(normalized_status=PaymentStatus.CAPTURED), target
            )
        self.assertEqual(self._count("provider_events"), 0)
        self.assertEqual(
            self.repository.get_payment(payment.payment_id).payment_status,
            PaymentStatus.CREATED,
        )

    def test_atomic_webhook_rolls_back_event_if_payment_update_fails(self):
        payment = _payment(provider_payment_reference="FAKE-PAYMENT-0001")
        self.repository.get_or_create_payment(
            "webhook-rollback", payment.idempotency_key_digest, "a" * 64, payment
        )
        updated = payment.model_copy(
            update={"payment_status": PaymentStatus.PENDING}
        )
        with patch.object(
            self.repository,
            "_update_payment_row",
            side_effect=RuntimeError("injected update failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.repository.record_provider_event_and_update_payment(
                    _event(normalized_status=PaymentStatus.PENDING), updated
                )
        self.assertEqual(self._count("provider_events"), 0)
        self.assertEqual(
            self.repository.get_payment(payment.payment_id).payment_status,
            PaymentStatus.CREATED,
        )

    def test_missing_payment_during_atomic_event_processing_rolls_back(self):
        updated = _payment(
            payment_id="PAY-MISSING",
            provider_payment_reference="FAKE-PAYMENT-0001",
            payment_status=PaymentStatus.PENDING,
        )
        with self.assertRaises(PaymentNotFoundError):
            self.repository.record_provider_event_and_update_payment(
                _event(normalized_status=PaymentStatus.PENDING),
                updated,
            )
        self.assertEqual(self._count("provider_events"), 0)

    def test_detection_review_and_simulated_intervention_persist(self):
        payment = _payment()
        self.repository.get_or_create_payment(
            "records", payment.idempotency_key_digest, "a" * 64, payment
        )
        result = _detection()
        self.repository.save_detection_result(result)
        review = HumanReview(
            review_id="REV-0001",
            detection_result_id=result.detection_result_id,
            reviewer_id="USER-0001",
            decision=HumanReviewDecision.NEEDS_MORE_INFORMATION,
            note="Review context.",
            created_at=_timestamp(),
        )
        self.repository.save_human_review(review)
        intervention = SimulatedIntervention(
            intervention_id="INT-0001",
            payment_id=payment.payment_id,
            simulation_type=SimulatedInterventionType.SIMULATED_HOLD,
            assumptions=["hypothetical only"],
            estimated_impact={
                "metric": "exposure",
                "baseline_value": 10,
                "simulated_value": 0,
                "unit": "INR",
            },
            created_by="USER-0001",
            created_at=_timestamp(),
        )
        self.repository.save_simulated_intervention(intervention)

        with self._database_connection() as connection:
            stored_signals = connection.execute(
                "SELECT signals_json FROM detection_results WHERE detection_result_id = ?",
                (result.detection_result_id,),
            ).fetchone()["signals_json"]
            stored_review = connection.execute(
                "SELECT decision, note FROM human_reviews WHERE review_id = ?",
                (review.review_id,),
            ).fetchone()
            stored_intervention = connection.execute(
                """
                SELECT assumptions_json, estimated_impact_json, execution_status
                FROM simulated_interventions WHERE intervention_id = ?
                """,
                (intervention.intervention_id,),
            ).fetchone()

        self.assertEqual(json.loads(stored_signals)["rule_based"]["contribution"], 25)
        self.assertEqual(stored_review["decision"], review.decision)
        self.assertEqual(stored_review["note"], review.note)
        self.assertEqual(json.loads(stored_intervention["assumptions_json"]), intervention.assumptions)
        self.assertEqual(stored_intervention["execution_status"], "SIMULATED")
        self.assertEqual(self.repository.get_payment(payment.payment_id).risk_status, RiskStatus.NOT_EVALUATED)

    def test_review_and_detection_foreign_keys_are_enforced(self):
        with self.assertRaises(RepositoryConstraintError):
            self.repository.save_detection_result(_detection())

        review = HumanReview(
            review_id="REV-ORPHAN",
            detection_result_id="DET-MISSING",
            reviewer_id="USER-0001",
            decision=HumanReviewDecision.UNRESOLVED,
            created_at=_timestamp(),
        )
        with self.assertRaises(RepositoryConstraintError):
            self.repository.save_human_review(review)

    def test_concurrent_same_idempotency_request_creates_once(self):
        second_repository = SQLitePaymentRepository(self.database_path)
        payment = _payment()
        barrier = threading.Barrier(2)
        outcomes = []
        failures = []

        def create(repo):
            try:
                barrier.wait(timeout=5)
                outcomes.append(
                    repo.get_or_create_payment(
                        "concurrent",
                        payment.idempotency_key_digest,
                        "a" * 64,
                        payment,
                    )
                )
            except Exception as error:
                failures.append(error)

        threads = [
            threading.Thread(target=create, args=(self.repository,)),
            threading.Thread(target=create, args=(second_repository,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(failures)
        self.assertEqual(len(outcomes), 2)
        self.assertCountEqual([created for _, created in outcomes], [True, False])
        self.assertEqual(self._count("payments"), 1)

    def test_raw_idempotency_keys_and_webhook_payloads_are_not_stored(self):
        payment = _payment()
        self.repository.get_or_create_payment(
            "sensitive-test", payment.idempotency_key_digest, "a" * 64, payment
        )
        self.repository.record_provider_event_once(_event())

        with self._database_connection() as connection:
            payment_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(payments)")
            }
            event_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(provider_events)")
            }
        self.assertNotIn("idempotency_key", payment_columns)
        self.assertNotIn("raw_payload", event_columns)
        self.assertNotIn("scenario_label", payment_columns)
        self.assertNotIn("evaluation_role", payment_columns)

    def _count(self, table):
        permitted_tables = {
            "payments",
            "provider_events",
            "detection_results",
            "human_reviews",
            "simulated_interventions",
        }
        if table not in permitted_tables:
            raise ValueError("Unexpected test table")
        with self._database_connection() as connection:
            return connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]

    def _database_connection(self):
        return self.repository._database.connection()


def _timestamp(hours=0):
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hours)


def _hex_digest(number):
    return f"{number:064x}"[-64:]


def _customer():
    return SyntheticCustomer(
        customer_id="CUST-0001",
        display_name="Synthetic Customer",
        account_id="ACC-C-0001",
        created_at=_timestamp(),
    )


def _merchant():
    return SyntheticMerchant(
        merchant_id="MER-0001",
        display_name="Synthetic Merchant",
        account_id="ACC-M-0001",
        created_at=_timestamp(),
        provider_merchant_reference="FAKE-MERCHANT-0001",
    )


def _payment(**overrides):
    values = {
        "payment_id": "PAY-0001",
        "customer_id": "CUST-0001",
        "merchant_id": "MER-0001",
        "sender_account_id": "ACC-C-0001",
        "receiver_account_id": "ACC-M-0001",
        "amount_paise": 12500,
        "currency": "INR",
        "payment_status": PaymentStatus.CREATED,
        "risk_status": RiskStatus.NOT_EVALUATED,
        "provider_name": "fake",
        "provider_payment_reference": "FAKE-PAYMENT-0001",
        "idempotency_key_digest": "b" * 64,
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
    }
    values.update(overrides)
    return Payment(**values)


def _event(**overrides):
    values = {
        "provider_name": "fake",
        "provider_event_id": "FAKE-EVENT-0001",
        "provider_payment_reference": "FAKE-PAYMENT-0001",
        "event_type": "payment.pending",
        "occurred_at": _timestamp(1),
        "received_at": _timestamp(1),
        "signature_status": ProviderSignatureStatus.VERIFIED,
        "payload_digest": "c" * 64,
        "normalized_status": PaymentStatus.PENDING,
        "processing_status": ProviderEventProcessingStatus.RECEIVED,
    }
    values.update(overrides)
    return ProviderEvent(**values)


def _detection():
    return DetectionResult(
        detection_result_id="DET-0001",
        payment_id="PAY-0001",
        transaction_id="TXN-0001",
        protocol="stream_frozen_model",
        risk_score=55.5,
        risk_status="MEDIUM",
        signals={
            "rule_based": RiskSignal(
                contribution=25,
                explanation="Synthetic rule result",
                indicators=[
                    {"code": "flow_imbalance", "explanation": "Synthetic indicator"}
                ],
            )
        },
        created_at=_timestamp(),
        disclaimer="Synthetic test result only.",
    )


if __name__ == "__main__":
    unittest.main()
