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

from pymongo import DESCENDING

from payment_service.errors import (
    DetectionResultNotFoundError,
    PaymentNotFoundError,
)
from payment_service.investigation_schemas import InvestigationDataSource
from payment_service.mongo_repository import MongoPaymentRepository
from payment_service.schemas import (
    DetectionResult,
    Payment,
    PaymentStatus,
    RiskSignal,
    RiskStatus,
    SyntheticCustomer,
    SyntheticMerchant,
)
from payment_service.sqlite_repository import SQLitePaymentRepository
from test_mongo_repository import _FakeMongoClient, _FakeMongoCollection


class InvestigationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sqlite = SQLitePaymentRepository(
            Path(self.temp_dir.name) / "investigations.sqlite3"
        )
        self.mongo_client = _FakeMongoClient()
        self.mongo_database = self.mongo_client["investigations"]
        self.mongo = MongoPaymentRepository(
            client=self.mongo_client,
            database=self.mongo_database,
        )
        self.find_patch = patch.object(
            _FakeMongoCollection,
            "find",
            _fake_find,
            create=True,
        )
        self.find_patch.start()
        self.repositories = (self.sqlite, self.mongo)
        for repository in self.repositories:
            repository.save_customer(_customer("CUST-A", "ACC-A"))
            repository.save_customer(_customer("CUST-C", "ACC-C"))
            repository.save_merchant(_merchant("MER-A", "ACC-A"))
            repository.save_merchant(_merchant("MER-B", "ACC-B"))
            repository.save_merchant(_merchant("MER-C", "ACC-C"))
            for payment in _payments():
                repository.get_or_create_payment(
                    f"scope:{payment.payment_id}",
                    payment.idempotency_key_digest,
                    "d" * 64,
                    payment,
                )
            result = _detection("DET-0001", "PAY-0002", "TXN-0001", 1)
            repository.save_detection_result(result)
            linked_payment = repository.get_payment("PAY-0002").model_copy(
                update={"detection_result_id": "DET-0001"}
            )
            repository.update_payment(linked_payment)

    def tearDown(self):
        self.find_patch.stop()
        self.temp_dir.cleanup()

    def test_payment_lookup_and_missing_payment(self):
        results = [
            repository.get_payment_investigation("PAY-0001")
            for repository in self.repositories
        ]

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].data_source, InvestigationDataSource.STREAMING_PAYMENT_SERVICE)
        self.assertIsNone(results[0].detection_result_id)
        for repository in self.repositories:
            with self.assertRaises(PaymentNotFoundError):
                repository.get_payment_investigation("PAY-MISSING")

    def test_account_payment_queries_include_both_directions_newest_first(self):
        for repository in self.repositories:
            results = repository.list_account_payments("ACC-A")
            self.assertEqual(
                [result.payment_id for result in results],
                ["PAY-0003", "PAY-0001", "PAY-0002"],
            )
            self.assertEqual(
                [result.payment_id for result in repository.list_account_payments(
                    "ACC-A", limit=2
                )],
                ["PAY-0003", "PAY-0001"],
            )
            self.assertEqual(
                repository.list_account_payments("ACC-UNKNOWN"),
                [],
            )
        self.assertEqual(
            [
                result.model_dump(mode="json")
                for result in self.sqlite.list_account_payments("ACC-A")
            ],
            [
                result.model_dump(mode="json")
                for result in self.mongo.list_account_payments("ACC-A")
            ],
        )

    def test_account_activity_contains_sides_and_orders_newest_first(self):
        expected = [
            ("PAY-0003", "OUTGOING", "ACC-B"),
            ("PAY-0001", "OUTGOING", "ACC-B"),
            ("PAY-0002", "INCOMING", "ACC-C"),
        ]
        for repository in self.repositories:
            activity = repository.list_account_activity("ACC-A")
            self.assertEqual(
                [
                    (
                        item.payment_id,
                        item.direction.value,
                        item.counterparty_account_id,
                    )
                    for item in activity
                ],
                expected,
            )
            self.assertTrue(
                all(
                    item.data_source
                    == InvestigationDataSource.STREAMING_PAYMENT_SERVICE
                    for item in activity
                )
            )
        self.assertEqual(
            [
                item.model_dump(mode="json")
                for item in self.sqlite.list_account_activity("ACC-A")
            ],
            [
                item.model_dump(mode="json")
                for item in self.mongo.list_account_activity("ACC-A")
            ],
        )

    def test_detection_result_lookup_and_missing_result(self):
        results = [
            repository.get_detection_result("DET-0001")
            for repository in self.repositories
        ]

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].transaction_id, "TXN-0001")
        for repository in self.repositories:
            with self.assertRaises(DetectionResultNotFoundError):
                repository.get_detection_result("DET-MISSING")

    def test_detection_results_for_payment_and_payment_link_field(self):
        for repository in self.repositories:
            linked = repository.get_payment_investigation("PAY-0002")
            unlinked = repository.get_payment_investigation("PAY-0001")
            self.assertEqual(linked.detection_result_id, "DET-0001")
            self.assertIsNone(unlinked.detection_result_id)
            detections = repository.list_detection_results_for_payment("PAY-0002")
            self.assertEqual([item.detection_result_id for item in detections], ["DET-0001"])
            self.assertEqual(
                repository.list_detection_results_for_payment("PAY-0001"),
                [],
            )
        self.assertEqual(
            [
                item.model_dump(mode="json")
                for item in self.sqlite.list_detection_results_for_payment("PAY-0002")
            ],
            [
                item.model_dump(mode="json")
                for item in self.mongo.list_detection_results_for_payment("PAY-0002")
            ],
        )

    def test_limits_are_positive_bounded_integers(self):
        for repository in self.repositories:
            for invalid_limit in (0, -1, 501, True, 2.5, "5"):
                with self.subTest(repository=type(repository).__name__, limit=invalid_limit):
                    with self.assertRaises(ValueError):
                        repository.list_account_payments("ACC-A", invalid_limit)
                    with self.assertRaises(ValueError):
                        repository.list_account_activity("ACC-A", invalid_limit)
            self.assertEqual(
                len(repository.list_account_payments("ACC-A", 500)),
                3,
            )

    def test_reads_do_not_mutate_payments_or_risk_status(self):
        for repository in self.repositories:
            before = {
                payment_id: repository.get_payment(payment_id).model_dump()
                for payment_id in ("PAY-0001", "PAY-0002", "PAY-0003")
            }
            repository.get_payment_investigation("PAY-0002")
            repository.list_account_payments("ACC-A")
            repository.get_detection_result("DET-0001")
            repository.list_detection_results_for_payment("PAY-0002")
            repository.list_account_activity("ACC-A")
            after = {
                payment_id: repository.get_payment(payment_id).model_dump()
                for payment_id in ("PAY-0001", "PAY-0002", "PAY-0003")
            }
            self.assertEqual(before, after)
            self.assertTrue(
                all(
                    repository.get_payment(payment_id).risk_status
                    == RiskStatus.NOT_EVALUATED
                    for payment_id in before
                )
            )


class _FakeCursor:
    def __init__(self, documents):
        self.documents = copy.deepcopy(documents)

    def sort(self, sort_spec):
        for field, direction in reversed(sort_spec):
            self.documents.sort(
                key=lambda document: document[field],
                reverse=direction == DESCENDING,
            )
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(copy.deepcopy(self.documents))


def _fake_find(collection, query):
    alternatives = query.get("$or")
    if alternatives is None:
        matches = [
            document
            for document in collection.documents
            if all(document.get(key) == value for key, value in query.items())
        ]
    else:
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


def _customer(customer_id, account_id):
    return SyntheticCustomer(
        customer_id=customer_id,
        display_name="Synthetic customer",
        account_id=account_id,
        created_at=_timestamp(0),
    )


def _merchant(merchant_id, account_id):
    return SyntheticMerchant(
        merchant_id=merchant_id,
        display_name="Synthetic merchant",
        account_id=account_id,
        created_at=_timestamp(0),
    )


def _payments():
    values = (
        ("PAY-0001", "CUST-A", "MER-B", 1),
        ("PAY-0002", "CUST-C", "MER-A", 1),
        ("PAY-0003", "CUST-A", "MER-B", 2),
    )
    return [
        Payment(
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
            idempotency_key_digest=f"{int(payment_id[-4:], 16) + 1:064x}",
            created_at=_timestamp(minute),
            updated_at=_timestamp(minute),
        )
        for payment_id, customer_id, merchant_id, minute in values
    ]


def _detection(detection_id, payment_id, transaction_id, minute):
    return DetectionResult(
        detection_result_id=detection_id,
        payment_id=payment_id,
        transaction_id=transaction_id,
        protocol="stream_frozen_model",
        risk_score=25,
        risk_status="LOW",
        signals={
            "rule_based": RiskSignal(
                contribution=10,
                explanation="Synthetic fixture.",
            )
        },
        created_at=_timestamp(minute),
        disclaimer="Synthetic test only.",
    )


if __name__ == "__main__":
    unittest.main()
