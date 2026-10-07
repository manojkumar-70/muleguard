"""Golden validation for the deterministic Phase 7.2 local demo."""

from datetime import timezone
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

AI_ENGINE_PATH = Path(__file__).resolve().parent
if str(AI_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_PATH))

from payment_service.demo_fixture import (
    FOCAL_ACCOUNT_ID,
    WARMUP_PAYMENT_COUNT,
    build_demo_fixture,
)
from payment_service.demo_runner import run_demo
from payment_service.schemas import PaymentStatus, RiskStatus
from payment_service.payment_detection_orchestrator import (
    PaymentDetectionOrchestrator,
)
from payment_service.sqlite_repository import SQLitePaymentRepository


class DemoRunnerGoldenTests(unittest.TestCase):
    def test_fixture_runs_end_to_end_deterministically_offline(self):
        fixture = build_demo_fixture()
        self.assertEqual(len(fixture.payments), 60)
        self.assertEqual(
            sum(not spec.is_focal_payment for spec in fixture.payments),
            WARMUP_PAYMENT_COUNT,
        )
        self.assertEqual(
            sum(spec.is_focal_payment for spec in fixture.payments),
            10,
        )
        self.assertEqual(
            [spec.created_at for spec in fixture.payments],
            sorted(spec.created_at for spec in fixture.payments),
        )
        self.assertEqual(
            len({spec.payment_id for spec in fixture.payments}),
            len(fixture.payments),
        )
        self.assertEqual(
            [spec.sequence for spec in fixture.payments[50:]],
            list(range(51, 61)),
        )

        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            first_path = Path(first_directory) / "first.sqlite3"
            second_path = Path(second_directory) / "second.sqlite3"
            original_process_payment = PaymentDetectionOrchestrator.process_payment
            observed_sessions = []

            def record_session(orchestrator, payment, session):
                observed_sessions.append(session)
                return original_process_payment(orchestrator, payment, session)

            with (
                patch.object(
                    PaymentDetectionOrchestrator,
                    "process_payment",
                    autospec=True,
                    side_effect=record_session,
                ),
                patch.object(
                    socket.socket,
                    "connect",
                    side_effect=AssertionError("network call"),
                ),
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=AssertionError("network call"),
                ),
            ):
                first_run = run_demo(first_path)
                first_session = first_run.session
                self.assertEqual(len(observed_sessions), 60)
                self.assertTrue(all(item is first_session for item in observed_sessions))

                observed_sessions.clear()
                second_run = run_demo(second_path)
                self.assertEqual(len(observed_sessions), 60)
                self.assertTrue(
                    all(item is second_run.session for item in observed_sessions)
                )
            repository = SQLitePaymentRepository(first_path)
            persisted_payment = repository.get_payment(
                first_run.focal_payment.payment_id
            )
            persisted_detection = repository.get_detection_result(
                first_run.detection_result.detection_result_id
            )

        self.assertEqual(len(first_run.payments), 60)
        self.assertEqual(len(first_run.warmup_progress), 50)
        self.assertTrue(
            all(
                result["phase"] == "warmup"
                for result in first_run.streaming_results[:50]
            )
        )
        self.assertTrue(
            all(
                not result["detection_enabled"]
                for result in first_run.streaming_results[:50]
            )
        )
        self.assertTrue(
            all(
                payment.payment_status == PaymentStatus.CAPTURED
                for payment in first_run.payments[:WARMUP_PAYMENT_COUNT]
            )
        )
        self.assertEqual(first_run.first_scored_result["phase"], "scored")
        self.assertEqual(
            first_run.first_scored_result["transaction_id"],
            "PAY-0000000000000033",
        )
        self.assertIn(
            FOCAL_ACCOUNT_ID,
            first_run.first_scored_result["risk_scores"],
        )
        self.assertEqual(first_run.session.warmup_state["required_events"], 50)
        self.assertTrue(first_run.session.warmup_state["model_frozen"])
        self.assertEqual(
            first_run.focal_payment.payment_status,
            PaymentStatus.CAPTURED,
        )
        self.assertEqual(
            first_run.focal_payment.risk_status,
            RiskStatus.NOT_EVALUATED,
        )
        self.assertEqual(first_run.provider_call_count, 60)
        self.assertIn(FOCAL_ACCOUNT_ID, first_run.session.accounts)
        self.assertEqual(
            len(first_run.session.accounts[FOCAL_ACCOUNT_ID]["risk_score_progression"]),
            10,
        )
        self.assertTrue(first_run.focal_alert_generated)
        self.assertEqual(first_run.focal_account_risk_level, "HIGH")
        self.assertEqual(first_run.focal_account_risk_score, 98.33)

        self.assertEqual(
            persisted_payment.detection_result_id,
            first_run.detection_result.detection_result_id,
        )
        self.assertEqual(
            persisted_payment.detection_protocol,
            "stream_frozen_model",
        )
        self.assertEqual(
            persisted_payment.payment_status,
            PaymentStatus.CAPTURED,
        )
        self.assertEqual(
            persisted_payment.risk_status,
            RiskStatus.NOT_EVALUATED,
        )
        self.assertEqual(
            persisted_detection.detection_result_id,
            first_run.detection_result.detection_result_id,
        )
        self.assertIn(
            "not_a_transaction_level_model_score",
            persisted_detection.signals["demo_account_level_projection"].explanation,
        )
        projection_evidence = persisted_detection.signals[
            "demo_account_level_projection"
        ]
        self.assertIn(FOCAL_ACCOUNT_ID, projection_evidence.explanation)
        # First-alert detector signals are nested under first_alert_snapshot.
        self.assertIn('"first_alert_snapshot"', projection_evidence.explanation)
        self.assertIn('"rule_based"', projection_evidence.explanation)
        self.assertIn('"graph_based"', projection_evidence.explanation)
        self.assertIn('"ml_anomaly"', projection_evidence.explanation)

        # --- Explanation/score consistency regression (Phase 7.2 fix) ---
        import json
        evidence = json.loads(projection_evidence.explanation)

        # Persisted scalar matches the focal account final streaming score.
        self.assertEqual(
            first_run.focal_account_risk_score,
            persisted_detection.risk_score,
        )
        self.assertEqual(
            first_run.focal_account_risk_level,
            persisted_detection.risk_level,
        )

        # Nested streaming_explanation scores match the persisted scalar.
        nested = evidence["streaming_explanation"]
        self.assertEqual(
            nested["account_risk_score"],
            evidence["account_risk_score"],
        )
        self.assertEqual(
            nested["account_risk_level"],
            evidence["account_risk_level"],
        )
        self.assertEqual(
            nested["account_risk_score"],
            persisted_detection.risk_score,
        )
        self.assertEqual(
            nested["account_risk_level"],
            persisted_detection.risk_level,
        )

        # No stale 48.69/MEDIUM first-alert data at the top-level explanation.
        self.assertNotIn("48.69", nested["summary"])
        self.assertNotIn("MEDIUM", nested["summary"])
        self.assertIn(str(first_run.focal_account_risk_score), nested["summary"])
        self.assertIn(first_run.focal_account_risk_level, nested["summary"])

        # First-alert snapshot is preserved as historical context only.
        first_alert = nested["first_alert_snapshot"]
        self.assertIsNotNone(first_alert)
        self.assertIn("signals", first_alert)

        # Payment state is unchanged.
        self.assertEqual(
            persisted_payment.payment_status,
            PaymentStatus.CAPTURED,
        )
        self.assertEqual(
            persisted_payment.risk_status,
            RiskStatus.NOT_EVALUATED,
        )

        self.assertEqual(
            _deterministic_summary(first_run),
            _deterministic_summary(second_run),
        )
        self.assertEqual(first_run.focal_payment.payment_id, "PAY-000000000000003c")
        self.assertEqual(
            first_run.detection_result.detection_result_id,
            "DET-PHASE72-0001",
        )

    def test_runner_refuses_existing_and_default_payment_database_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            existing_path = Path(directory) / "existing.sqlite3"
            existing_path.touch()
            with self.assertRaises(FileExistsError):
                run_demo(existing_path)

        from payment_service.sqlite_database import DEFAULT_DATABASE_PATH

        with self.assertRaisesRegex(ValueError, "default payment database"):
            run_demo(DEFAULT_DATABASE_PATH)


def _deterministic_summary(run):
    return {
        "payment_ids": [payment.payment_id for payment in run.payments],
        "timestamps": [
            payment.created_at.astimezone(timezone.utc).isoformat()
            for payment in run.payments
        ],
        "first_scored_result": run.first_scored_result,
        "account_risk_score": run.focal_account_risk_score,
        "account_risk_level": run.focal_account_risk_level,
        "alert_generated": run.focal_alert_generated,
        "detection": run.detection_result.model_dump(mode="json"),
    }


if __name__ == "__main__":
    unittest.main()
