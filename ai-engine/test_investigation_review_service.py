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
    RepositoryConstraintError,
)
from payment_service.investigation_review_service import (
    InvestigationReviewService,
)
from payment_service.mongo_repository import MongoPaymentRepository
from payment_service.schemas import (
    DetectionResult,
    HumanReviewDecision,
    Payment,
    PaymentStatus,
    RiskSignal,
    RiskStatus,
    SyntheticCustomer,
    SyntheticMerchant,
)
from payment_service.sqlite_repository import SQLitePaymentRepository
from test_mongo_repository import _FakeMongoClient, _FakeMongoCollection


class InvestigationReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sqlite = SQLitePaymentRepository(
            Path(self.temp_dir.name) / "reviews.sqlite3"
        )
        self.client = _FakeMongoClient()
        self.database = self.client["review_tests"]
        self.mongo = MongoPaymentRepository(client=self.client, database=self.database)
        self.find_patch = patch.object(
            _FakeMongoCollection,
            "find",
            _fake_find,
            create=True,
        )
        self.find_patch.start()
        self.repositories = (self.sqlite, self.mongo)
        for repository in self.repositories:
            _seed(repository)
        self.ids = iter(("REV-0001", "REV-0002"))
        self.service = InvestigationReviewService(
            self.sqlite,
            review_id_factory=lambda: next(self.ids),
            clock=lambda: _timestamp(5),
        )

    def tearDown(self):
        self.find_patch.stop()
        self.temp_dir.cleanup()

    def test_service_creates_and_persists_review(self):
        review = self.service.create_review(
            "PAY-0001",
            "DET-0001",
            "USER-0001",
            HumanReviewDecision.CONFIRMED_FOR_REVIEW,
            "Synthetic investigative note.",
        )

        self.assertEqual(review.review_id, "REV-0001")
        self.assertEqual(review.decision, HumanReviewDecision.CONFIRMED_FOR_REVIEW)
        self.assertEqual(
            self.sqlite.list_human_reviews_for_payment("PAY-0001"),
            [review],
        )

    def test_reviews_are_append_only_and_multiple_reviews_are_preserved(self):
        for service in (
            InvestigationReviewService(
                self.sqlite,
                review_id_factory=iter(("REV-SQL-01", "REV-SQL-02")).__next__,
                clock=lambda: _timestamp(5),
            ),
            InvestigationReviewService(
                self.mongo,
                review_id_factory=iter(("REV-MONGO-01", "REV-MONGO-02")).__next__,
                clock=lambda: _timestamp(5),
            ),
        ):
            with self.subTest(repository=type(service._repository).__name__):
                first = service.create_review(
                    "PAY-0001",
                    "DET-0001",
                    "USER-0001",
                    HumanReviewDecision.NEEDS_MORE_INFORMATION,
                    "First note.",
                )
                second = service.create_review(
                    "PAY-0001",
                    "DET-0001",
                    "USER-0002",
                    HumanReviewDecision.UNRESOLVED,
                    "Second note.",
                )
                reviews = service.list_reviews("PAY-0001")
                self.assertEqual(
                    {review.review_id for review in reviews},
                    {first.review_id, second.review_id},
                )
                self.assertEqual(
                    {review.note for review in reviews},
                    {"First note.", "Second note."},
                )

    def test_payment_fields_and_risk_status_are_unchanged(self):
        for repository in self.repositories:
            service = InvestigationReviewService(
                repository,
                review_id_factory=lambda: "REV-IMMUTABLE",
                clock=lambda: _timestamp(6),
            )
            before = repository.get_payment("PAY-0001").model_dump()

            service.create_review(
                "PAY-0001",
                "DET-0001",
                "USER-0003",
                HumanReviewDecision.NOT_SUSPICIOUS,
            )

            after = repository.get_payment("PAY-0001").model_dump()
            self.assertEqual(after, before)
            self.assertEqual(repository.get_payment("PAY-0001").risk_status, RiskStatus.NOT_EVALUATED)

    def test_missing_payment_or_unrelated_detection_is_rejected(self):
        with self.assertRaises(PaymentNotFoundError):
            self.service.create_review(
                "PAY-MISSING",
                "DET-0001",
                "USER-0001",
                HumanReviewDecision.UNRESOLVED,
            )
        with self.assertRaises(DetectionResultNotFoundError):
            self.service.create_review(
                "PAY-0002",
                "DET-0001",
                "USER-0001",
                HumanReviewDecision.UNRESOLVED,
            )

    def test_append_only_duplicate_review_ids_are_rejected_by_repository(self):
        service = InvestigationReviewService(
            self.sqlite,
            review_id_factory=lambda: "REV-SAME",
            clock=lambda: _timestamp(7),
        )
        service.create_review(
            "PAY-0001",
            "DET-0001",
            "USER-0001",
            HumanReviewDecision.UNRESOLVED,
        )
        with self.assertRaises(RepositoryConstraintError):
            service.create_review(
                "PAY-0001",
                "DET-0001",
                "USER-0002",
                HumanReviewDecision.NOT_SUSPICIOUS,
            )
        saved = self.sqlite.list_human_reviews_for_payment("PAY-0001")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].reviewer_id, "USER-0001")


def _fake_find(collection, query):
    matches = []
    for document in collection.documents:
        matched = True
        for key, value in query.items():
            if isinstance(value, dict) and "$in" in value:
                matched = document.get(key) in value["$in"]
            else:
                matched = document.get(key) == value
            if not matched:
                break
        if matched:
            matches.append(document)
    return _FakeCursor(matches)


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

    def __iter__(self):
        return iter(copy.deepcopy(self.documents))


def _timestamp(minute):
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minute)


def _seed(repository):
    repository.save_customer(
        SyntheticCustomer(
            customer_id="CUST-0001",
            display_name="Synthetic customer",
            account_id="ACC-C-0001",
            created_at=_timestamp(0),
        )
    )
    repository.save_merchant(
        SyntheticMerchant(
            merchant_id="MER-0001",
            display_name="Synthetic merchant",
            account_id="ACC-M-0001",
            created_at=_timestamp(0),
        )
    )
    for payment_id, customer_id in (
        ("PAY-0001", "CUST-0001"),
        ("PAY-0002", "CUST-0001"),
    ):
        payment = Payment(
            payment_id=payment_id,
            customer_id=customer_id,
            merchant_id="MER-0001",
            sender_account_id="ACC-C-0001",
            receiver_account_id="ACC-M-0001",
            amount_paise=1000,
            currency="INR",
            payment_status=PaymentStatus.CAPTURED,
            risk_status=RiskStatus.NOT_EVALUATED,
            provider_name="fake",
            idempotency_key_digest=(
                f"{int(payment_id[-4:], 16) + 1:064x}"
            ),
            created_at=_timestamp(1),
            updated_at=_timestamp(1),
        )
        repository.get_or_create_payment(
            f"test:{payment_id}",
            payment.idempotency_key_digest,
            "a" * 64,
            payment,
        )
    repository.save_detection_result(
        DetectionResult(
            detection_result_id="DET-0001",
            payment_id="PAY-0001",
            transaction_id="PAY-0001",
            protocol="stream_frozen_model",
            risk_score=30,
            risk_status="MEDIUM",
            signals={
                "rule_based": RiskSignal(
                    contribution=20,
                    explanation="Synthetic review test.",
                )
            },
            created_at=_timestamp(2),
            disclaimer="Synthetic test only.",
        )
    )


if __name__ == "__main__":
    unittest.main()
