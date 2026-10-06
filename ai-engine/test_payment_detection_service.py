import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import synthetic_streaming
from payment_service.payment_detection_service import PaymentDetectionService
from payment_service.schemas import Payment, PaymentStatus, RiskStatus
from payment_service.streaming_bridge import StreamingDetectionSession


class PaymentDetectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.detection_service = PaymentDetectionService()

    def test_successfully_submits_eligible_payment_and_returns_session_result(self):
        session = StreamingDetectionSession(warmup_events=2)
        self.detection_service.detect_payment(
            _payment("PAY-0001", PaymentStatus.CAPTURED),
            session,
        )
        self.detection_service.detect_payment(
            _payment("PAY-0002", PaymentStatus.AUTHORIZED),
            session,
        )

        result = self.detection_service.detect_payment(
            _payment("PAY-0003", PaymentStatus.CAPTURED),
            session,
        )

        self.assertEqual(
            set(result),
            {
                "protocol",
                "transaction_id",
                "timestamp",
                "phase",
                "detection_enabled",
                "risk_scores",
                "new_alerts",
                "warmup_state",
            },
        )
        self.assertEqual(result["protocol"], "stream_frozen_model")
        self.assertEqual(result["transaction_id"], "PAY-0003")
        self.assertEqual(result["phase"], "scored")
        self.assertTrue(result["detection_enabled"])
        self.assertIsInstance(result["risk_scores"], dict)

    def test_adapter_is_used_to_submit_the_payment(self):
        session = StreamingDetectionSession(warmup_events=2)
        payment = _payment("PAY-0001", PaymentStatus.AUTHORIZED)
        expected_result = {"phase": "warmup", "risk_scores": {}}

        with patch(
            "payment_service.payment_detection_service."
            "submit_payment_for_detection",
            return_value=expected_result,
        ) as adapter:
            result = self.detection_service.detect_payment(payment, session)

        adapter.assert_called_once_with(payment, session)
        self.assertIs(result, expected_result)

    def test_same_caller_supplied_session_is_reused_across_payments(self):
        session = StreamingDetectionSession(warmup_events=3)
        payments = [
            _payment(f"PAY-000{index}", PaymentStatus.CAPTURED)
            for index in range(1, 3)
        ]
        results = [
            self.detection_service.detect_payment(payment, session)
            for payment in payments
        ]

        self.assertEqual([result["phase"] for result in results], ["warmup", "warmup"])
        self.assertEqual(session.warmup_state["observed_events"], 2)
        self.assertEqual(session.warmup_state["remaining_events"], 1)

    def test_model_is_not_recreated_or_refitted_per_payment(self):
        session = StreamingDetectionSession(warmup_events=2)
        with patch(
            "synthetic_streaming._fit_stream_model",
            wraps=synthetic_streaming._fit_stream_model,
        ) as fit_model:
            for index in range(1, 5):
                self.detection_service.detect_payment(
                    _payment(
                        f"PAY-000{index}",
                        PaymentStatus.CAPTURED,
                        sender_account_id=f"ACC-C-000{index}",
                    ),
                    session,
                )

        self.assertEqual(fit_model.call_count, 1)
        self.assertTrue(session.warmup_state["model_frozen"])
        self.assertEqual(session.warmup_state["observed_events"], 2)

    def test_unsupported_payment_status_is_rejected_without_session_submission(self):
        session = StreamingDetectionSession(warmup_events=2)
        with patch.object(session, "process_event") as process_event:
            with self.assertRaisesRegex(ValueError, "CAPTURED or AUTHORIZED"):
                self.detection_service.detect_payment(
                    _payment("PAY-0001", PaymentStatus.FAILED),
                    session,
                )

        process_event.assert_not_called()
        self.assertEqual(session.warmup_state["observed_events"], 0)

    def test_warmup_results_follow_session_semantics(self):
        session = StreamingDetectionSession(warmup_events=3)
        first = self.detection_service.detect_payment(
            _payment("PAY-0001", PaymentStatus.CAPTURED),
            session,
        )
        second = self.detection_service.detect_payment(
            _payment("PAY-0002", PaymentStatus.CAPTURED),
            session,
        )

        self.assertEqual(first["phase"], "warmup")
        self.assertEqual(second["phase"], "warmup")
        self.assertFalse(first["detection_enabled"])
        self.assertEqual(second["warmup_state"]["remaining_events"], 1)

    def test_detection_does_not_mutate_payment_status_or_risk_status(self):
        session = StreamingDetectionSession(warmup_events=2)
        first = _payment("PAY-0001", PaymentStatus.CAPTURED)
        second = _payment(
            "PAY-0002",
            PaymentStatus.AUTHORIZED,
            sender_account_id="ACC-C-0002",
        )
        third = _payment(
            "PAY-0003",
            PaymentStatus.CAPTURED,
            sender_account_id="ACC-C-0003",
        )
        payment_snapshots = [
            (payment.payment_status, payment.risk_status)
            for payment in (first, second, third)
        ]

        self.detection_service.detect_payment(first, session)
        self.detection_service.detect_payment(second, session)
        result = self.detection_service.detect_payment(third, session)

        self.assertTrue(result["detection_enabled"])
        self.assertEqual(
            [
                (payment.payment_status, payment.risk_status)
                for payment in (first, second, third)
            ],
            payment_snapshots,
        )
        self.assertTrue(
            all(risk_status == RiskStatus.NOT_EVALUATED
                for _, risk_status in payment_snapshots)
        )


def _payment(payment_id, status, **overrides):
    timestamp = datetime(
        2025,
        1,
        1,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    values = {
        "payment_id": payment_id,
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
