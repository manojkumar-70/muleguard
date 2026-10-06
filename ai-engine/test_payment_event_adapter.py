import unittest
from datetime import datetime, timedelta, timezone

from payment_service.payment_event_adapter import (
    payment_to_transaction_event,
    submit_payment_for_detection,
)
from payment_service.schemas import (
    NormalizedTransactionEvent,
    Payment,
    PaymentStatus,
    RiskStatus,
)
from payment_service.streaming_bridge import StreamingDetectionSession


class PaymentEventAdapterTests(unittest.TestCase):
    def test_captured_payment_converts_to_successful_normalized_event(self):
        payment = _payment(PaymentStatus.CAPTURED)

        event = payment_to_transaction_event(payment)

        self.assertIsInstance(event, NormalizedTransactionEvent)
        self.assertEqual(event.status, "SUCCESS")

    def test_authorized_payment_converts_to_successful_normalized_event(self):
        event = payment_to_transaction_event(_payment(PaymentStatus.AUTHORIZED))

        self.assertEqual(event.status, "SUCCESS")

    def test_unsupported_payment_status_is_rejected(self):
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
                    payment_to_transaction_event(_payment(status))

    def test_conversion_maps_exact_event_fields_only(self):
        payment = _payment(PaymentStatus.CAPTURED)

        event = payment_to_transaction_event(payment)

        self.assertEqual(
            event.model_dump(),
            {
                "transaction_id": payment.payment_id,
                "sender": payment.sender_account_id,
                "receiver": payment.receiver_account_id,
                "amount_paise": payment.amount_paise,
                "currency": payment.currency,
                "timestamp": payment.created_at,
                "status": "SUCCESS",
                "device_id": None,
                "ip_address": None,
                "payment_id": payment.payment_id,
                "source": "synthetic_payment_service",
            },
        )
        self.assertEqual(
            set(event.model_fields_set),
            {
                "transaction_id",
                "sender",
                "receiver",
                "amount_paise",
                "currency",
                "timestamp",
                "status",
                "payment_id",
                "source",
            },
        )

    def test_scenario_and_evaluation_fields_are_not_in_normalized_event(self):
        event = payment_to_transaction_event(_payment(PaymentStatus.CAPTURED))
        event_fields = set(event.model_dump())

        self.assertNotIn("scenario_label", event_fields)
        self.assertNotIn("evaluation_role", event_fields)
        self.assertNotIn("provider_name", event_fields)
        self.assertNotIn("provider_payment_reference", event_fields)
        self.assertNotIn("idempotency_key_digest", event_fields)
        self.assertNotIn("failure_code", event_fields)
        self.assertNotIn("raw_payment", event_fields)

    def test_detection_output_does_not_mutate_payment_risk_status(self):
        payment = _payment(PaymentStatus.CAPTURED)
        original_risk_status = payment.risk_status
        session = StreamingDetectionSession(warmup_events=2)
        submit_payment_for_detection(payment, session)
        submit_payment_for_detection(
            _payment(
                PaymentStatus.AUTHORIZED,
                payment_id="PAY-0002",
                sender_account_id="ACC-C-0002",
            ),
            session,
        )
        result = submit_payment_for_detection(
            _payment(
                PaymentStatus.CAPTURED,
                payment_id="PAY-0003",
                sender_account_id="ACC-C-0003",
            ),
            session,
        )

        self.assertEqual(result["protocol"], "stream_frozen_model")
        self.assertEqual(result["transaction_id"], "PAY-0003")
        self.assertEqual(result["phase"], "scored")
        self.assertTrue(result["detection_enabled"])
        self.assertIsInstance(result["risk_scores"], dict)
        self.assertEqual(payment.risk_status, original_risk_status)

    def test_adapter_preserves_configured_session_warmup(self):
        session = StreamingDetectionSession(warmup_events=3)
        results = [
            submit_payment_for_detection(
                _payment(
                    PaymentStatus.CAPTURED,
                    payment_id=f"PAY-000{index}",
                    sender_account_id=f"ACC-C-000{index}",
                ),
                session,
            )
            for index in range(1, 3)
        ]

        self.assertTrue(all(result["phase"] == "warmup" for result in results))
        self.assertEqual(session.warmup_state["required_events"], 3)
        self.assertEqual(session.warmup_state["remaining_events"], 1)
        self.assertFalse(session.warmup_state["complete"])


def _payment(status, **overrides):
    created_at = datetime(
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
        "created_at": created_at,
        "updated_at": created_at,
    }
    values.update(overrides)
    return Payment(**values)


if __name__ == "__main__":
    unittest.main()
