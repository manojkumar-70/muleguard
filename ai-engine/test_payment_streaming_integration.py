import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_streaming
from payment_service.payment_detection_service import PaymentDetectionService
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


class PaymentStreamingIntegrationTests(unittest.TestCase):
    def test_successful_payment_sequence_uses_one_warmed_session_and_model(self):
        warmup_events = 3
        session = StreamingDetectionSession(
            warmup_events=warmup_events,
            seed=17,
        )
        detection_service = PaymentDetectionService()
        payments = [
            _payment(
                f"PAY-000{index}",
                PaymentStatus.CAPTURED if index % 2 else PaymentStatus.AUTHORIZED,
                minute=index,
                sender_account_id=f"ACC-C-000{index}",
                receiver_account_id=f"ACC-M-000{index % 3}",
            )
            for index in range(1, 7)
        ]
        original_payment_state = [
            (payment.payment_status, payment.risk_status)
            for payment in payments
        ]
        normalized_events = []
        submitted_sessions = []
        training_rows = []
        fitted_models = []
        scored_prefixes = []
        original_fit = synthetic_streaming._fit_stream_model
        original_features = synthetic_streaming._build_incremental_features

        def capture_conversion(payment):
            event = payment_to_transaction_event(payment)
            normalized_events.append(event)
            return event

        def capture_submission(payment, supplied_session):
            submitted_sessions.append(supplied_session)
            return submit_payment_for_detection(payment, supplied_session)

        def capture_fit(transactions, seed):
            training_rows.extend(transactions.to_dict(orient="records"))
            model, features = original_fit(transactions, seed)
            fitted_models.append(model)
            return model, features

        def capture_features(transactions):
            if session.warmup_state["complete"]:
                scored_prefixes.append(transactions["transaction_id"].tolist())
            return original_features(transactions)

        results = []
        with (
            patch(
                "payment_service.payment_event_adapter.payment_to_transaction_event",
                side_effect=capture_conversion,
            ) as conversion,
            patch(
                "payment_service.payment_detection_service."
                "submit_payment_for_detection",
                side_effect=capture_submission,
            ) as submission,
            patch(
                "synthetic_streaming._fit_stream_model",
                side_effect=capture_fit,
            ) as fit_model,
            patch(
                "synthetic_streaming._build_incremental_features",
                side_effect=capture_features,
            ),
        ):
            for payment in payments:
                results.append(
                    detection_service.detect_payment(payment, session)
                )

        self.assertEqual(conversion.call_count, len(payments))
        self.assertEqual(submission.call_count, len(payments))
        self.assertEqual(submitted_sessions, [session] * len(payments))
        self.assertEqual(fit_model.call_count, 1)
        self.assertEqual(len(fitted_models), 1)
        self.assertIs(session._model, fitted_models[0])
        self.assertEqual(
            session.warmup_state["required_events"],
            warmup_events,
        )
        self.assertTrue(session.warmup_state["model_frozen"])

        self.assertEqual(
            [result["phase"] for result in results],
            ["warmup", "warmup", "warmup", "scored", "scored", "scored"],
        )
        for result in results[:warmup_events]:
            self.assertFalse(result["detection_enabled"])
            self.assertEqual(result["risk_scores"], {})
            self.assertEqual(result["new_alerts"], [])
        for payment, result in zip(payments[warmup_events:], results[warmup_events:]):
            self.assertTrue(result["detection_enabled"])
            self.assertEqual(result["protocol"], "stream_frozen_model")
            self.assertEqual(result["transaction_id"], payment.payment_id)
            self.assertIsInstance(result["risk_scores"], dict)
            self.assertTrue(result["risk_scores"])
            self.assertTrue(
                all(
                    account_id.startswith("ACC-")
                    for account_id in result["risk_scores"]
                )
            )
            self.assertNotIn("transaction_risk_score", result)

        self.assertEqual(len(training_rows), warmup_events)
        self.assertEqual(
            [row["transaction_id"] for row in training_rows],
            [payment.payment_id for payment in payments[:warmup_events]],
        )
        self.assertEqual(
            [event.transaction_id for event in normalized_events],
            [payment.payment_id for payment in payments],
        )
        self.assertEqual(
            [event.timestamp for event in normalized_events],
            sorted(event.timestamp for event in normalized_events),
        )
        for event in normalized_events:
            self.assertIsInstance(event, NormalizedTransactionEvent)
            self.assertNotIn("scenario_label", event.model_dump())
            self.assertNotIn("evaluation_role", event.model_dump())
            self.assertNotIn("provider_name", event.model_dump())
            self.assertNotIn("idempotency_key_digest", event.model_dump())

        expected_prefixes = [
            [payment.payment_id for payment in payments[:end]]
            for end in range(warmup_events + 1, len(payments) + 1)
        ]
        self.assertEqual(scored_prefixes, expected_prefixes)
        self.assertEqual(
            [
                (payment.payment_status, payment.risk_status)
                for payment in payments
            ],
            original_payment_state,
        )

    def test_unsupported_payment_status_is_rejected_before_detection(self):
        session = StreamingDetectionSession(warmup_events=3)
        detection_service = PaymentDetectionService()
        unsupported = _payment("PAY-0001", PaymentStatus.FAILED)

        with patch.object(session, "process_event") as process_event:
            with self.assertRaisesRegex(ValueError, "CAPTURED or AUTHORIZED"):
                detection_service.detect_payment(unsupported, session)

        process_event.assert_not_called()
        self.assertEqual(session.warmup_state["observed_events"], 0)


def _payment(
    payment_id: str,
    payment_status: PaymentStatus,
    minute: int = 0,
    sender_account_id: str = "ACC-C-0001",
    receiver_account_id: str = "ACC-M-0001",
) -> Payment:
    created_at = datetime(
        2025,
        1,
        1,
        tzinfo=timezone.utc,
    ) + timedelta(minutes=minute)
    return Payment(
        payment_id=payment_id,
        customer_id="CUST-0001",
        merchant_id="MER-0001",
        sender_account_id=sender_account_id,
        receiver_account_id=receiver_account_id,
        amount_paise=12500 + minute,
        currency="INR",
        payment_status=payment_status,
        risk_status=RiskStatus.NOT_EVALUATED,
        provider_name="fake",
        provider_payment_reference=f"FAKE-{payment_id}",
        idempotency_key_digest=f"{minute + 1:064x}",
        created_at=created_at,
        updated_at=created_at,
    )


if __name__ == "__main__":
    unittest.main()
