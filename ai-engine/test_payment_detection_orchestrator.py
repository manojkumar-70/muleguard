import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_streaming
from payment_service.payment_detection_orchestrator import (
    PaymentDetectionOrchestrator,
)
from payment_service.payment_detection_service import PaymentDetectionService
from payment_service.schemas import Payment, PaymentStatus, RiskStatus
from payment_service.streaming_bridge import StreamingDetectionSession


class PaymentDetectionOrchestratorTests(unittest.TestCase):
    def test_captured_payment_flows_through_detection_service(self):
        detection_service = Mock(spec=PaymentDetectionService)
        expected_result = {"phase": "scored", "risk_scores": {"ACC-A": 50.0}}
        detection_service.detect_payment.return_value = expected_result
        orchestrator = PaymentDetectionOrchestrator(detection_service)
        payment = _payment(PaymentStatus.CAPTURED)
        session = StreamingDetectionSession(warmup_events=2)

        result = orchestrator.process_payment(payment, session)

        self.assertIs(result, expected_result)
        detection_service.detect_payment.assert_called_once_with(payment, session)

    def test_authorized_payment_flows_through_detection_service(self):
        detection_service = Mock(spec=PaymentDetectionService)
        detection_service.detect_payment.return_value = {"phase": "warmup"}
        orchestrator = PaymentDetectionOrchestrator(detection_service)
        payment = _payment(PaymentStatus.AUTHORIZED)
        session = StreamingDetectionSession(warmup_events=2)

        result = orchestrator.process_payment(payment, session)

        self.assertEqual(result, {"phase": "warmup"})
        detection_service.detect_payment.assert_called_once_with(payment, session)

    def test_unsupported_status_is_rejected_before_detection_service_call(self):
        detection_service = Mock(spec=PaymentDetectionService)
        orchestrator = PaymentDetectionOrchestrator(detection_service)
        session = StreamingDetectionSession(warmup_events=2)

        for status in (
            PaymentStatus.CREATED,
            PaymentStatus.PENDING,
            PaymentStatus.DECLINED,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
            PaymentStatus.EXPIRED,
        ):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, "CAPTURED or AUTHORIZED"):
                    orchestrator.process_payment(_payment(status), session)

        detection_service.detect_payment.assert_not_called()
        self.assertEqual(session.warmup_state["observed_events"], 0)

    def test_same_supplied_session_is_reused_and_model_fits_once(self):
        session = StreamingDetectionSession(warmup_events=2, seed=9)
        detection_service = PaymentDetectionService()
        orchestrator = PaymentDetectionOrchestrator(detection_service)

        with (
            patch(
                "synthetic_streaming._fit_stream_model",
                wraps=synthetic_streaming._fit_stream_model,
            ) as fit_model,
            patch(
                "payment_service.payment_detection_orchestrator."
                "StreamingDetectionSession",
                side_effect=AssertionError("orchestrator must not create sessions"),
            ) as session_factory,
        ):
            results = [
                orchestrator.process_payment(
                    _payment(
                        PaymentStatus.CAPTURED,
                        payment_id=f"PAY-000{index}",
                        sender_account_id=f"ACC-C-000{index}",
                    ),
                    session,
                )
                for index in range(1, 5)
            ]

        self.assertEqual(fit_model.call_count, 1)
        session_factory.assert_not_called()
        self.assertTrue(session.warmup_state["model_frozen"])
        self.assertEqual(
            [result["phase"] for result in results],
            ["warmup", "warmup", "scored", "scored"],
        )

    def test_detection_service_result_is_returned_unchanged(self):
        detection_service = Mock(spec=PaymentDetectionService)
        result = {
            "protocol": "stream_frozen_model",
            "risk_scores": {"ACC-C-0001": 24.0, "ACC-M-0001": 30.0},
            "new_alerts": [],
        }
        detection_service.detect_payment.return_value = result
        orchestrator = PaymentDetectionOrchestrator(detection_service)

        returned = orchestrator.process_payment(
            _payment(PaymentStatus.CAPTURED),
            StreamingDetectionSession(warmup_events=2),
        )

        self.assertIs(returned, result)

    def test_payment_status_and_risk_status_remain_unchanged(self):
        detection_service = Mock(spec=PaymentDetectionService)
        detection_service.detect_payment.return_value = {
            "phase": "scored",
            "risk_scores": {"ACC-C-0001": 99.0},
        }
        orchestrator = PaymentDetectionOrchestrator(detection_service)
        payment = _payment(PaymentStatus.CAPTURED)
        original_payment_status = payment.payment_status
        original_risk_status = payment.risk_status

        orchestrator.process_payment(
            payment,
            StreamingDetectionSession(warmup_events=2),
        )

        self.assertEqual(payment.payment_status, original_payment_status)
        self.assertEqual(payment.risk_status, original_risk_status)
        self.assertEqual(payment.risk_status, RiskStatus.NOT_EVALUATED)

    def test_orchestration_does_not_construct_or_call_provider(self):
        detection_service = Mock(spec=PaymentDetectionService)
        detection_service.detect_payment.return_value = {"phase": "warmup"}
        orchestrator = PaymentDetectionOrchestrator(detection_service)
        payment = _payment(PaymentStatus.CAPTURED)
        session = StreamingDetectionSession(warmup_events=2)

        with (
            patch(
                "payment_service.provider.FakePaymentProvider",
                side_effect=AssertionError("provider must not be used"),
            ) as provider_factory,
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network must not be used"),
            ) as network_call,
        ):
            orchestrator.process_payment(payment, session)

        provider_factory.assert_not_called()
        network_call.assert_not_called()
        detection_service.detect_payment.assert_called_once_with(payment, session)


def _payment(status, **overrides):
    timestamp = datetime(
        2025,
        1,
        1,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    values = {
        "payment_id": "PAY-0001",
        "customer_id": "CUST-0001",
        "merchant_id": "MER-0001",
        "sender_account_id": "ACC-C-0001",
        "receiver_account_id": "ACC-M-0001",
        "amount_paise": 12500,
        "currency": "INR",
        "payment_status": status,
        "risk_status": RiskStatus.NOT_EVALUATED,
        "provider_name": "fake",
        "provider_payment_reference": "FAKE-PAY-0001",
        "idempotency_key_digest": "a" * 64,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    values.update(overrides)
    return Payment(**values)


if __name__ == "__main__":
    unittest.main()
