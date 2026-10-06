import json
import statistics
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_streaming
from payment_service.detection_persistence_service import (
    DetectionPersistenceService,
)
from payment_service.errors import RepositoryConstraintError
from payment_service.mongo_repository import MongoPaymentRepository
from payment_service.payment_detection_orchestrator import (
    PaymentDetectionOrchestrator,
)
from payment_service.payment_detection_service import PaymentDetectionService
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
from payment_service.streaming_bridge import StreamingDetectionSession
from test_mongo_repository import _FakeMongoClient


class DetectionPersistenceServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = DetectionPersistenceService()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "detections.sqlite3"
        self.repository = SQLitePaymentRepository(self.database_path)
        self.payment = _payment("PAY-0001")
        _seed_sqlite(self.repository, self.payment)
        self.result = _detection("DET-0001", self.payment.payment_id, "TXN-0001")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detection_result_persists_through_repository_protocol(self):
        class RecordingRepository:
            def __init__(self):
                self.saved = None

            def save_detection_result(self, result):
                self.saved = result

        repository = RecordingRepository()
        persisted = self.service.persist(self.result, repository)

        self.assertIs(persisted, self.result)
        self.assertIs(repository.saved, self.result)

    def test_sqlite_persists_result_and_duplicate_uses_repository_constraint(self):
        persisted = self.service.persist(self.result, self.repository)

        self.assertEqual(persisted, self.result)
        with self.repository._database.connection() as connection:
            row = connection.execute(
                """
                SELECT detection_result_id, payment_id, transaction_id,
                       protocol, risk_score, risk_status, signals_json
                FROM detection_results WHERE detection_result_id = ?
                """,
                (self.result.detection_result_id,),
            ).fetchone()

        self.assertEqual(row["payment_id"], self.payment.payment_id)
        self.assertEqual(row["transaction_id"], self.result.transaction_id)
        self.assertEqual(row["protocol"], "stream_frozen_model")
        self.assertEqual(json.loads(row["signals_json"])["rule_based"]["contribution"], 25)
        with self.assertRaises(RepositoryConstraintError):
            self.service.persist(self.result, self.repository)

    def test_persistence_does_not_mutate_payment_or_risk_status(self):
        before = self.payment.model_dump()

        self.service.persist(self.result, self.repository)

        self.assertEqual(self.payment.model_dump(), before)
        self.assertEqual(self.payment.payment_status, PaymentStatus.CAPTURED)
        self.assertEqual(self.payment.risk_status, RiskStatus.NOT_EVALUATED)
        stored_payment = self.repository.get_payment(self.payment.payment_id)
        self.assertEqual(stored_payment.payment_status, PaymentStatus.CAPTURED)
        self.assertEqual(stored_payment.risk_status, RiskStatus.NOT_EVALUATED)

    def test_sensitive_or_evaluation_fields_are_not_persisted(self):
        self.service.persist(self.result, self.repository)
        with self.repository._database.connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(detection_results)"
                )
            }
            stored = connection.execute(
                "SELECT * FROM detection_results WHERE detection_result_id = ?",
                (self.result.detection_result_id,),
            ).fetchone()

        self.assertTrue(
            {
                "scenario_label",
                "evaluation_role",
                "provider_credentials",
                "raw_provider_payload",
                "idempotency_secret",
            }.isdisjoint(columns)
        )
        self.assertTrue(
            {
                "scenario_label",
                "evaluation_role",
                "provider_credentials",
                "raw_provider_payload",
                "idempotency_secret",
            }.isdisjoint(set(stored.keys()))
        )

    def test_mongo_repository_compatibility_and_duplicate_semantics(self):
        client = _FakeMongoClient()
        database = client["payment_detection_tests"]
        repository = MongoPaymentRepository(client=client, database=database)
        payment = _payment("PAY-0002")
        repository.save_customer(_customer())
        repository.save_merchant(_merchant())
        repository.get_or_create_payment(
            "scope",
            payment.idempotency_key_digest,
            "b" * 64,
            payment,
        )
        result = _detection("DET-0002", payment.payment_id, "TXN-0002")

        self.assertEqual(self.service.persist(result, repository), result)
        stored = database["detection_results"].documents[0]
        self.assertEqual(stored["payment_id"], payment.payment_id)
        self.assertEqual(stored["risk_status"], "MEDIUM")
        self.assertNotIn("scenario_label", stored)
        self.assertNotIn("evaluation_role", stored)
        with self.assertRaises(RepositoryConstraintError):
            self.service.persist(result, repository)
        self.assertEqual(len(database["detection_results"].documents), 1)

    def test_orchestrator_exposes_persistence_only_as_explicit_call(self):
        orchestrator = PaymentDetectionOrchestrator()

        persisted = orchestrator.persist_detection_result(
            self.result,
            self.repository,
        )

        self.assertEqual(persisted, self.result)
        with self.repository._database.connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM detection_results"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_end_to_end_latency_benchmark_separates_warmup_and_scored_path(self):
        warmup_count = 4
        payment_count = 16
        session = StreamingDetectionSession(warmup_events=warmup_count, seed=43)
        orchestrator = PaymentDetectionOrchestrator(PaymentDetectionService())
        payments = [
            _payment(
                f"PAY-{index:04d}",
                minute=index,
            )
            for index in range(payment_count)
        ]
        for payment in payments:
            _seed_sqlite(self.repository, payment)

        scored_latencies_ms = []
        persisted_results = []
        original_fit = synthetic_streaming._fit_stream_model
        fit_started = time.perf_counter()
        with patch(
            "synthetic_streaming._fit_stream_model",
            wraps=original_fit,
        ) as fit_model:
            warmup_results = [
                orchestrator.process_payment(payment, session)
                for payment in payments[:warmup_count]
            ]
            warmup_and_training_seconds = time.perf_counter() - fit_started
            self.assertTrue(
                all(result["phase"] == "warmup" for result in warmup_results)
            )
            self.assertTrue(session.warmup_state["model_frozen"])
            for index, payment in enumerate(payments[warmup_count:], start=warmup_count):
                started = time.perf_counter()
                detection = orchestrator.process_payment(payment, session)
                self.assertTrue(detection["detection_enabled"])
                result = _detection(
                    f"DET-{index:04d}",
                    payment.payment_id,
                    f"TXN-{index:04d}",
                )
                persisted_results.append(
                    orchestrator.persist_detection_result(result, self.repository)
                )
                scored_latencies_ms.append(
                    (time.perf_counter() - started) * 1000
                )
        self.assertEqual(fit_model.call_count, 1)
        self.assertEqual(len(persisted_results), payment_count - warmup_count)

        p50_ms = statistics.median(scored_latencies_ms)
        p95_ms = _percentile(scored_latencies_ms, 0.95)
        self.assertGreaterEqual(warmup_and_training_seconds, 0)
        self.assertGreaterEqual(p50_ms, 0)
        self.assertGreaterEqual(p95_ms, p50_ms)
        print(
            "Synthetic offline end-to-end latency (not production performance): "
            f"warmup+training={warmup_and_training_seconds * 1000:.3f} ms; "
            f"scored orchestration+caller-supplied DetectionResult persistence "
            f"p50={p50_ms:.3f} ms, p95={p95_ms:.3f} ms "
            f"(n={len(scored_latencies_ms)})."
        )


def _percentile(values, percentile):
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _seed_sqlite(repository, payment):
    if repository.get_customer(payment.customer_id) is None:
        repository.save_customer(_customer(payment.customer_id))
    if repository.get_merchant(payment.merchant_id) is None:
        repository.save_merchant(_merchant(payment.merchant_id))
    if repository.get_payment(payment.payment_id) is None:
        repository.get_or_create_payment(
            f"test:{payment.payment_id}",
            payment.idempotency_key_digest,
            "c" * 64,
            payment,
        )


def _timestamp(minute=0):
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minute)


def _customer(customer_id="CUST-0001"):
    return SyntheticCustomer(
        customer_id=customer_id,
        display_name="Synthetic Customer",
        account_id="ACC-C-0001",
        created_at=_timestamp(),
    )


def _merchant(merchant_id="MER-0001"):
    return SyntheticMerchant(
        merchant_id=merchant_id,
        display_name="Synthetic Merchant",
        account_id="ACC-M-0001",
        created_at=_timestamp(),
    )


def _payment(
    payment_id,
    minute=0,
    sender_account_id="ACC-C-0001",
    receiver_account_id="ACC-M-0001",
):
    timestamp = _timestamp(minute)
    suffix = payment_id.removeprefix("PAY-")
    return Payment(
        payment_id=payment_id,
        customer_id="CUST-0001",
        merchant_id="MER-0001",
        sender_account_id=sender_account_id,
        receiver_account_id=receiver_account_id,
        amount_paise=1000 + minute,
        currency="INR",
        payment_status=PaymentStatus.CAPTURED,
        risk_status=RiskStatus.NOT_EVALUATED,
        provider_name="fake",
        provider_payment_reference=f"FAKE-{payment_id}",
        idempotency_key_digest=f"{int(suffix, 16) + 1:064x}",
        created_at=timestamp,
        updated_at=timestamp,
    )


def _detection(detection_id, payment_id, transaction_id):
    return DetectionResult(
        detection_result_id=detection_id,
        payment_id=payment_id,
        transaction_id=transaction_id,
        protocol="stream_frozen_model",
        risk_score=55.5,
        risk_status="MEDIUM",
        signals={
            "rule_based": RiskSignal(
                contribution=25,
                explanation="Synthetic test signal.",
            )
        },
        created_at=_timestamp(),
        disclaimer="Synthetic/offline persistence test only.",
    )


if __name__ == "__main__":
    unittest.main()
