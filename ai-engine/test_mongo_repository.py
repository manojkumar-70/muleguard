import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pymongo.errors import CollectionInvalid, DuplicateKeyError

from payment_service.errors import (
    IdempotencyConflictError,
    InvalidPaymentTransitionError,
    PaymentNotFoundError,
    ProviderEventConflictError,
    RepositoryConstraintError,
)
from payment_service.mongo_repository import MongoPaymentRepository
from payment_service.schemas import (
    ALLOWED_PAYMENT_TRANSITIONS,
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
)


class MongoPaymentRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.client = _FakeMongoClient()
        self.database = self.client["test_payments"]
        self.repository = MongoPaymentRepository(
            client=self.client,
            database=self.database,
        )
        self.customer = _customer()
        self.merchant = _merchant()
        self.repository.save_customer(self.customer)
        self.repository.save_merchant(self.merchant)

    def test_initialization_creates_collections_and_unique_indexes(self):
        self.assertEqual(
            set(self.database.list_collection_names()),
            {
                "synthetic_customers",
                "synthetic_merchants",
                "payments",
                "provider_events",
                "detection_results",
                "human_reviews",
                "simulated_interventions",
            },
        )
        payments_indexes = self.database["payments"].indexes
        self.assertIn(
            (((("payment_id", 1),), {"unique": True})),
            payments_indexes,
        )
        self.assertIn(
            (
                (("idempotency_scope", 1), ("idempotency_key_digest", 1)),
                {"unique": True},
            ),
            payments_indexes,
        )
        self.assertIn(
            (
                (("provider_name", 1), ("provider_event_id", 1)),
                {"unique": True},
            ),
            self.database["provider_events"].indexes,
        )

    def test_constructor_requires_environment_configuration_without_injection(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "MONGODB_URI"):
                MongoPaymentRepository()

    def test_customer_and_merchant_round_trip_with_aware_timestamps(self):
        self.assertEqual(
            self.repository.get_customer(self.customer.customer_id),
            self.customer,
        )
        self.assertEqual(
            self.repository.get_merchant(self.merchant.merchant_id),
            self.merchant,
        )
        self.assertIsNone(self.repository.get_customer("CUST-MISSING"))
        self.assertIsNone(self.repository.get_merchant("MER-MISSING"))

        stored_time = self.repository.get_customer(
            self.customer.customer_id
        ).created_at
        self.assertIsNotNone(stored_time.utcoffset())

    def test_payment_account_references_and_account_ids_are_validated(self):
        payment = _payment(sender_account_id="ACC-C-OTHER")
        with self.assertRaisesRegex(RepositoryConstraintError, "sender_account_id"):
            self.repository.get_or_create_payment(
                "account-mismatch",
                payment.idempotency_key_digest,
                "a" * 64,
                payment,
            )

        missing = _payment(customer_id="CUST-MISSING")
        with self.assertRaises(RepositoryConstraintError):
            self.repository.get_or_create_payment(
                "missing-customer",
                missing.idempotency_key_digest,
                "b" * 64,
                missing,
            )

    def test_payment_idempotency_returns_original_or_conflicts(self):
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
        self.assertEqual(len(self.database["payments"].documents), 1)

        with self.assertRaises(IdempotencyConflictError):
            self.repository.get_or_create_payment(
                "customer:CUST-0001:create",
                payment.idempotency_key_digest,
                "b" * 64,
                payment,
            )

    def test_payment_idempotency_scope_is_unique_pair_and_payment_id_is_unique(self):
        first = _payment()
        second = _payment(
            payment_id="PAY-0002",
            idempotency_key_digest=first.idempotency_key_digest,
        )
        self.repository.get_or_create_payment(
            "scope-one", first.idempotency_key_digest, "a" * 64, first
        )
        self.repository.get_or_create_payment(
            "scope-two", second.idempotency_key_digest, "a" * 64, second
        )

        duplicate_payment_id = second.model_copy(
            update={"idempotency_key_digest": "d" * 64}
        )
        with self.assertRaises(RepositoryConstraintError):
            self.repository.get_or_create_payment(
                "scope-three",
                duplicate_payment_id.idempotency_key_digest,
                "a" * 64,
                duplicate_payment_id,
            )

    def test_payment_retrieval_validates_model_and_keeps_timestamp_aware(self):
        offset_time = datetime(
            2025, 1, 1, 12, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )
        payment = _payment(created_at=offset_time, updated_at=offset_time)
        self.repository.get_or_create_payment(
            "timestamp", payment.idempotency_key_digest, "a" * 64, payment
        )
        stored = self.repository.get_payment(payment.payment_id)

        self.assertEqual(stored, payment)
        self.assertEqual(stored.amount_paise, 12500)
        self.assertIsNotNone(stored.created_at.utcoffset())

    def test_payment_status_transitions_match_sqlite_contract(self):
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
                        f"scope-{payment_id}", key_digest, _hex_digest(index), payment
                    )
                    self.repository.update_payment(
                        payment.model_copy(
                            update={
                                "payment_status": target_status,
                                "updated_at": _timestamp(2),
                            }
                        )
                    )
                    self.assertEqual(
                        self.repository.get_payment(payment_id).payment_status,
                        target_status,
                    )

    def test_invalid_transitions_and_immutable_fields_do_not_update_payment(self):
        payment = _payment(payment_status=PaymentStatus.CAPTURED)
        self.repository.get_or_create_payment(
            "immutable", payment.idempotency_key_digest, "a" * 64, payment
        )
        with self.assertRaises(InvalidPaymentTransitionError):
            self.repository.update_payment(
                payment.model_copy(
                    update={
                        "payment_status": PaymentStatus.PENDING,
                        "updated_at": _timestamp(2),
                    }
                )
            )
        with self.assertRaisesRegex(ValueError, "amount_paise.*immutable"):
            self.repository.update_payment(
                payment.model_copy(update={"amount_paise": 99999})
            )
        self.assertEqual(
            self.repository.get_payment(payment.payment_id).payment_status,
            PaymentStatus.CAPTURED,
        )

    def test_provider_event_deduplicates_and_detects_digest_conflicts(self):
        event = _event()
        self.assertTrue(self.repository.record_provider_event_once(event))
        self.assertFalse(self.repository.record_provider_event_once(event))

        conflicting = event.model_copy(update={"payload_digest": "d" * 64})
        with self.assertRaises(ProviderEventConflictError):
            self.repository.record_provider_event_once(conflicting)
        self.assertEqual(len(self.database["provider_events"].documents), 1)

    def test_atomic_verified_event_and_payment_update_use_transaction(self):
        payment = _payment()
        self.repository.get_or_create_payment(
            "atomic", payment.idempotency_key_digest, "a" * 64, payment
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
        self.assertEqual(self.client.transaction_count, 1)
        self.assertEqual(
            self.repository.get_payment(payment.payment_id).payment_status,
            PaymentStatus.PENDING,
        )
        stored_event = self.database["provider_events"].documents[0]
        self.assertEqual(
            stored_event["processing_status"],
            ProviderEventProcessingStatus.PROCESSED,
        )
        self.assertFalse(
            self.repository.record_provider_event_and_update_payment(event, updated)
        )

    def test_atomic_event_rolls_back_for_unverified_and_mismatched_provider(self):
        payment = _payment()
        self.repository.get_or_create_payment(
            "atomic-validation", payment.idempotency_key_digest, "a" * 64, payment
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
        with self.assertRaisesRegex(ValueError, "provider_name"):
            self.repository.record_provider_event_and_update_payment(
                _event(provider_name="other"), target
            )
        with self.assertRaisesRegex(ValueError, "reference"):
            self.repository.record_provider_event_and_update_payment(
                _event(provider_payment_reference="OTHER-REF"), target
            )
        self.assertEqual(self.database["provider_events"].documents, [])
        self.assertEqual(
            self.repository.get_payment(payment.payment_id).payment_status,
            PaymentStatus.CREATED,
        )

    def test_atomic_event_rolls_back_if_payment_update_fails_or_payment_is_missing(self):
        payment = _payment()
        self.repository.get_or_create_payment(
            "atomic-rollback", payment.idempotency_key_digest, "a" * 64, payment
        )
        updated = payment.model_copy(
            update={"payment_status": PaymentStatus.PENDING}
        )
        with patch.object(
            self.repository,
            "_update_payment_document",
            side_effect=RuntimeError("injected update failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.repository.record_provider_event_and_update_payment(
                    _event(normalized_status=PaymentStatus.PENDING), updated
                )
        self.assertEqual(self.database["provider_events"].documents, [])

        missing = updated.model_copy(update={"payment_id": "PAY-MISSING"})
        with self.assertRaises(PaymentNotFoundError):
            self.repository.record_provider_event_and_update_payment(
                _event(provider_event_id="EVENT-MISSING"),
                missing,
            )
        self.assertEqual(self.database["provider_events"].documents, [])

    def test_detection_reviews_and_simulated_interventions_persist(self):
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

        self.assertEqual(
            self.database["detection_results"].documents[0]["signals"]["rule_based"][
                "contribution"
            ],
            25,
        )
        self.assertEqual(
            self.database["human_reviews"].documents[0]["decision"],
            HumanReviewDecision.NEEDS_MORE_INFORMATION,
        )
        self.assertEqual(
            self.database["simulated_interventions"].documents[0]["execution_status"],
            "SIMULATED",
        )

    def test_reference_constraints_and_sensitive_fields_are_not_persisted(self):
        with self.assertRaises(RepositoryConstraintError):
            self.repository.save_detection_result(_detection())

        payment = _payment()
        self.repository.get_or_create_payment(
            "sensitive", payment.idempotency_key_digest, "a" * 64, payment
        )
        self.repository.record_provider_event_once(_event())
        self.assertEqual(
            self.database["detection_results"].documents,
            [],
        )
        all_documents = [
            document
            for name in self.database.list_collection_names()
            for document in self.database[name].documents
        ]
        forbidden = {
            "password",
            "secret",
            "card_number",
            "card_data",
            "raw_payload",
            "scenario_label",
            "evaluation_role",
        }
        for document in all_documents:
            self.assertTrue(forbidden.isdisjoint(document))
        self.assertNotIn(
            "raw_payload",
            self.database["provider_events"].documents[0],
        )


class _FakeMongoClient:
    def __init__(self):
        self.databases = {}
        self.transaction_count = 0

    def __getitem__(self, name):
        if name not in self.databases:
            self.databases[name] = _FakeMongoDatabase(self)
        return self.databases[name]

    def start_session(self):
        return _FakeMongoSession(self)


class _FakeMongoDatabase:
    def __init__(self, client):
        self.client = client
        self.collections = {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _FakeMongoCollection(name)
        return self.collections[name]

    def list_collection_names(self):
        return list(self.collections)

    def create_collection(self, name):
        if name in self.collections:
            raise CollectionInvalid(name)
        self.collections[name] = _FakeMongoCollection(name)
        return self.collections[name]


class _FakeMongoCollection:
    def __init__(self, name):
        self.name = name
        self.documents = []
        self.indexes = []

    def create_index(self, keys, **options):
        self.indexes.append((tuple(keys), options))

    def find_one(self, query, session=None):
        del session
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return copy.deepcopy(document)
        return None

    def insert_one(self, document, session=None):
        del session
        stored = copy.deepcopy(document)
        for keys, options in self.indexes:
            if not options.get("unique"):
                continue
            partial = options.get("partialFilterExpression")
            if partial and not all(
                isinstance(stored.get(key), str)
                for key, condition in partial.items()
                if condition == {"$type": "string"}
            ):
                continue
            for existing in self.documents:
                if all(existing.get(key) == stored.get(key) for key, _ in keys):
                    raise DuplicateKeyError("duplicate unique index")
        stored.setdefault("_id", len(self.documents) + 1)
        self.documents.append(stored)
        return None

    def update_one(self, query, update, session=None):
        del session
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                document.update(copy.deepcopy(update["$set"]))
                return None
        return None


class _FakeMongoSession:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        del _args
        return False

    def with_transaction(self, callback):
        self.client.transaction_count += 1
        snapshots = {
            (database_name, collection_name): copy.deepcopy(collection.documents)
            for database_name, database in self.client.databases.items()
            for collection_name, collection in database.collections.items()
        }
        try:
            return callback(self)
        except BaseException:
            for (database_name, collection_name), documents in snapshots.items():
                self.client.databases[database_name].collections[
                    collection_name
                ].documents = documents
            raise


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
