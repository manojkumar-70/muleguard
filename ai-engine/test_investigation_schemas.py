import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

from pydantic import ValidationError

AI_ENGINE_PATH = Path(__file__).resolve().parent
if str(AI_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_PATH))

from payment_service.investigation_schemas import (
    AccountActivityDirection,
    AccountActivityItem,
    DetectionResultInvestigationView,
    InvestigationDataSource,
    PaymentInvestigationSummary,
)
from payment_service.schemas import (
    DetectionResult,
    Payment,
    PaymentStatus,
    RiskSignal,
    RiskStatus,
)


class InvestigationSchemaTests(unittest.TestCase):
    def test_detection_result_accepts_payment_and_transaction_ids(self):
        payment_result = DetectionResult.model_validate(
            _detection_result(transaction_id="PAY-0001")
        )
        transaction_result = DetectionResult.model_validate(
            _detection_result(transaction_id="TXN-0001")
        )

        self.assertEqual(payment_result.transaction_id, "PAY-0001")
        self.assertEqual(transaction_result.transaction_id, "TXN-0001")

    def test_detection_result_rejects_invalid_or_empty_transaction_ids(self):
        for transaction_id in ("", " ", "BAD-0001", "PAY-", "TXN-"):
            with self.subTest(transaction_id=transaction_id):
                with self.assertRaises(ValidationError):
                    DetectionResult.model_validate(
                        _detection_result(transaction_id=transaction_id)
                    )

    def test_payment_investigation_serialization_is_safe_and_immutable(self):
        summary = PaymentInvestigationSummary.from_payment(
            _payment(),
            InvestigationDataSource.STREAMING_PAYMENT_SERVICE,
        )
        serialized = summary.model_dump(mode="json")

        self.assertEqual(serialized["payment_id"], "PAY-0001")
        self.assertEqual(serialized["status"], "CAPTURED")
        self.assertEqual(
            serialized["data_source"],
            "STREAMING_PAYMENT_SERVICE",
        )
        self.assertIsNone(serialized["detection_result_id"])
        self.assertTrue(
            {
                "provider_name",
                "provider_payment_reference",
                "idempotency_key_digest",
            }.isdisjoint(serialized)
        )
        with self.assertRaises(ValidationError):
            summary.payment_id = "PAY-0002"

    def test_detection_investigation_serialization_preserves_signals(self):
        view = DetectionResultInvestigationView.from_detection_result(
            _detection_result(transaction_id="PAY-0001"),
            InvestigationDataSource.STREAMING_PAYMENT_SERVICE,
        )
        serialized = view.model_dump(mode="json")

        self.assertEqual(serialized["transaction_id"], "PAY-0001")
        self.assertEqual(serialized["risk_level"], "MEDIUM")
        self.assertEqual(
            serialized["signals"]["rule_based"]["explanation"],
            "Synthetic rule explanation.",
        )
        self.assertEqual(
            serialized["data_source"],
            "STREAMING_PAYMENT_SERVICE",
        )
        self.assertNotIn("scenario_label", serialized)
        self.assertNotIn("evaluation_role", serialized)

    def test_account_activity_serialization(self):
        activity = AccountActivityItem(
            account_id="ACC-C-0001",
            direction=AccountActivityDirection.OUTGOING,
            payment_id="PAY-0001",
            counterparty_account_id="ACC-M-0001",
            amount_paise=1500,
            currency="INR",
            payment_status=PaymentStatus.CAPTURED,
            created_at=_timestamp(),
            risk_status=RiskStatus.NOT_EVALUATED,
            data_source=InvestigationDataSource.STREAMING_PAYMENT_SERVICE,
        )

        self.assertEqual(
            activity.model_dump(mode="json")["direction"],
            "OUTGOING",
        )
        self.assertEqual(activity.payment_status, PaymentStatus.CAPTURED)

    def test_data_source_distinguishes_offline_and_streaming(self):
        offline = PaymentInvestigationSummary.from_payment(
            _payment(),
            InvestigationDataSource.OFFLINE_DATASET,
        )
        streaming = PaymentInvestigationSummary.from_payment(
            _payment(),
            InvestigationDataSource.STREAMING_PAYMENT_SERVICE,
        )

        self.assertNotEqual(offline.data_source, streaming.data_source)

    def test_optional_detection_fields_and_required_fields(self):
        result_values = _detection_result(transaction_id="TXN-0001")
        result = DetectionResultInvestigationView(
            detection_result_id=result_values["detection_result_id"],
            protocol=result_values["protocol"],
            transaction_id=result_values["transaction_id"],
            payment_id=None,
            risk_score=result_values["risk_score"],
            risk_level="MEDIUM",
            signals=result_values["signals"],
            created_at=result_values["created_at"],
            disclaimer=result_values["disclaimer"],
            data_source=InvestigationDataSource.OFFLINE_DATASET,
        )
        self.assertIsNone(result.payment_id)
        with self.assertRaises(ValidationError):
            DetectionResultInvestigationView(
                detection_result_id="DET-0001",
                protocol="stream_frozen_model",
                transaction_id="TXN-0001",
                risk_level="MEDIUM",
                data_source=InvestigationDataSource.OFFLINE_DATASET,
            )

    def test_existing_payment_and_detection_schemas_remain_compatible(self):
        payment = Payment.model_validate(_payment())
        result = DetectionResult.model_validate(
            _detection_result(transaction_id="TXN-0001")
        )

        self.assertEqual(payment.payment_status, PaymentStatus.CAPTURED)
        self.assertEqual(result.transaction_id, "TXN-0001")


def _timestamp():
    return datetime(2025, 1, 1, tzinfo=timezone.utc)


def _payment():
    return Payment(
        payment_id="PAY-0001",
        customer_id="CUST-0001",
        merchant_id="MER-0001",
        sender_account_id="ACC-C-0001",
        receiver_account_id="ACC-M-0001",
        amount_paise=1500,
        currency="INR",
        payment_status=PaymentStatus.CAPTURED,
        risk_status=RiskStatus.NOT_EVALUATED,
        provider_name="fake",
        idempotency_key_digest="a" * 64,
        created_at=_timestamp(),
        updated_at=_timestamp(),
    )


def _detection_result(transaction_id):
    return {
        "detection_result_id": "DET-0001",
        "payment_id": "PAY-0001",
        "transaction_id": transaction_id,
        "protocol": "stream_frozen_model",
        "risk_score": 55.0,
        "risk_status": "MEDIUM",
        "signals": {
            "rule_based": RiskSignal(
                contribution=25,
                explanation="Synthetic rule explanation.",
            )
        },
        "created_at": _timestamp(),
        "disclaimer": "Synthetic/offline test only.",
    }


if __name__ == "__main__":
    unittest.main()
