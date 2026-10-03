import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from payment_service.schemas import (
    ALLOWED_PAYMENT_TRANSITIONS,
    DetectionResult,
    HumanReview,
    HumanReviewDecision,
    NormalizedTransactionEvent,
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
    is_valid_payment_transition,
    validate_payment_transition,
)


class PaymentSchemaTests(unittest.TestCase):
    def test_customer_merchant_and_payment_accept_synthetic_contracts(self):
        customer = SyntheticCustomer(
            customer_id="CUST-0001",
            display_name="Synthetic Customer",
            account_id="ACC-C-0001",
            created_at=_timestamp(),
        )
        merchant = SyntheticMerchant(
            merchant_id="MER-0001",
            display_name="Synthetic Merchant",
            account_id="ACC-M-0001",
            created_at=_timestamp(),
            provider_merchant_reference="FAKE-MERCHANT-1",
        )
        payment = _payment()

        self.assertEqual(customer.status, "ACTIVE")
        self.assertEqual(merchant.currency, "INR")
        self.assertEqual(payment.amount_paise, 12500)
        self.assertEqual(payment.payment_status, PaymentStatus.CREATED)
        self.assertEqual(payment.risk_status, RiskStatus.NOT_EVALUATED)

    def test_payment_and_risk_status_are_independent_fields(self):
        payment = _payment(
            payment_status=PaymentStatus.CAPTURED,
            risk_status=RiskStatus.HIGH,
        )

        self.assertEqual(payment.payment_status, PaymentStatus.CAPTURED)
        self.assertEqual(payment.risk_status, RiskStatus.HIGH)
        self.assertEqual(
            _payment(payment_status=PaymentStatus.DECLINED).risk_status,
            RiskStatus.NOT_EVALUATED,
        )

    def test_amount_must_be_positive_integer_paise(self):
        for amount in (0, -1, 125.5, "12500", True):
            with self.subTest(amount=amount):
                with self.assertRaises(ValidationError):
                    _payment(amount_paise=amount)

    def test_currency_id_and_timezone_validation(self):
        with self.assertRaises(ValidationError):
            _payment(currency="USD")
        with self.assertRaises(ValidationError):
            _payment(sender_account_id="real-bank-account")
        with self.assertRaises(ValidationError):
            _payment(created_at=datetime(2025, 1, 1))

    def test_provider_event_and_normalized_event_validate(self):
        event = ProviderEvent(
            provider_name="fake",
            provider_event_id="FAKE-EVENT-1",
            provider_payment_reference="FAKE-PAYMENT-1",
            event_type="payment.captured",
            occurred_at=_timestamp(),
            received_at=_timestamp(),
            signature_status=ProviderSignatureStatus.VERIFIED,
            payload_digest="a" * 64,
            normalized_status=PaymentStatus.CAPTURED,
            processing_status=ProviderEventProcessingStatus.RECEIVED,
        )
        transaction = _normalized_event()

        self.assertEqual(event.processing_status, ProviderEventProcessingStatus.RECEIVED)
        self.assertEqual(transaction.amount_paise, 12500)
        self.assertEqual(transaction.status, "SUCCESS")

    def test_detection_review_and_simulation_contracts_validate(self):
        detection = DetectionResult(
            detection_result_id="DET-0001",
            payment_id="PAY-0001",
            transaction_id="TXN-0001",
            protocol="stream_frozen_model",
            risk_score=70.5,
            risk_status="HIGH",
            signals={
                "rule_based": RiskSignal(
                    contribution=50,
                    explanation="Rule signal",
                    indicators=[
                        {"code": "flow_imbalance", "explanation": "Observed imbalance"}
                    ],
                )
            },
            created_at=_timestamp(),
            disclaimer="Synthetic research signal only.",
        )
        review = HumanReview(
            review_id="REV-0001",
            detection_result_id=detection.detection_result_id,
            reviewer_id="USER-0001",
            decision=HumanReviewDecision.NEEDS_MORE_INFORMATION,
            created_at=_timestamp(),
        )
        intervention = SimulatedIntervention(
            intervention_id="INT-0001",
            payment_id="PAY-0001",
            simulation_type=SimulatedInterventionType.SIMULATED_REVIEW,
            assumptions=["No real action is performed."],
            created_by="USER-0001",
            created_at=_timestamp(),
        )

        self.assertEqual(detection.risk_status, "HIGH")
        self.assertEqual(review.decision, HumanReviewDecision.NEEDS_MORE_INFORMATION)
        self.assertEqual(intervention.execution_status, "SIMULATED")

    def test_payment_and_provider_contracts_reject_evaluation_and_secret_fields(self):
        invalid_fields = (
            {"scenario_label": "SYNTHETIC_SUSPICIOUS"},
            {"evaluation_role": "FOCAL_SUSPICIOUS"},
            {"account_role": "FOCAL_SUSPICIOUS"},
            {"account_role_metadata": {"role": "FOCAL_SUSPICIOUS"}},
            {"api_key": "must-not-be-accepted"},
            {"secret": "must-not-be-accepted"},
            {"metadata": {"evaluation_role": "NORMAL"}},
        )
        for extra in invalid_fields:
            with self.subTest(extra=extra):
                with self.assertRaises(ValidationError):
                    _payment(**extra)

        with self.assertRaises(ValidationError):
            _normalized_event(scenario_label="NORMAL")

    def test_provider_event_rejects_raw_payload_and_secret_fields(self):
        fields = {
            "provider_name": "fake",
            "provider_event_id": "FAKE-EVENT-1",
            "provider_payment_reference": "FAKE-PAYMENT-1",
            "event_type": "payment.captured",
            "occurred_at": _timestamp(),
            "received_at": _timestamp(),
            "signature_status": ProviderSignatureStatus.VERIFIED,
            "payload_digest": "a" * 64,
            "raw_payload": {"anything": "not accepted"},
            "webhook_secret": "not accepted",
        }
        with self.assertRaises(ValidationError):
            ProviderEvent(**fields)

    def test_unknown_fields_and_invalid_simulation_target_are_rejected(self):
        with self.assertRaises(ValidationError):
            SyntheticMerchant(
                merchant_id="MER-0001",
                display_name="Merchant",
                account_id="ACC-M-0001",
                created_at=_timestamp(),
                extra_notes="not part of the closed contract",
            )
        with self.assertRaises(ValidationError):
            SimulatedIntervention(
                intervention_id="INT-0001",
                simulation_type=SimulatedInterventionType.SIMULATED_HOLD,
                assumptions=["hypothetical"],
                created_by="USER-0001",
                created_at=_timestamp(),
            )
        with self.assertRaises(ValidationError):
            SimulatedIntervention(
                intervention_id="INT-0001",
                account_id="ACC-0001",
                simulation_type=SimulatedInterventionType.SIMULATED_HOLD,
                assumptions=["   "],
                created_by="USER-0001",
                created_at=_timestamp(),
            )

    def test_status_transition_table_and_validation(self):
        self.assertEqual(
            set(ALLOWED_PAYMENT_TRANSITIONS),
            set(PaymentStatus),
        )
        self.assertTrue(
            is_valid_payment_transition(PaymentStatus.CREATED, PaymentStatus.PENDING)
        )
        self.assertTrue(
            is_valid_payment_transition(
                PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED
            )
        )
        self.assertFalse(
            is_valid_payment_transition(PaymentStatus.CAPTURED, PaymentStatus.PENDING)
        )
        self.assertFalse(
            is_valid_payment_transition(PaymentStatus.DECLINED, PaymentStatus.CAPTURED)
        )
        with self.assertRaisesRegex(ValueError, "CAPTURED -> PENDING"):
            validate_payment_transition(PaymentStatus.CAPTURED, PaymentStatus.PENDING)
        with self.assertRaises(ValueError):
            is_valid_payment_transition("UNKNOWN", PaymentStatus.PENDING)


def _timestamp():
    return datetime(2025, 1, 1, tzinfo=timezone.utc)


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
        "provider_name": "fake",
        "idempotency_key_digest": "b" * 64,
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
    }
    values.update(overrides)
    return Payment(**values)


def _normalized_event(**overrides):
    values = {
        "transaction_id": "TXN-0001",
        "sender": "ACC-C-0001",
        "receiver": "ACC-M-0001",
        "amount_paise": 12500,
        "currency": "INR",
        "timestamp": _timestamp(),
        "status": "SUCCESS",
        "payment_id": "PAY-0001",
        "source": "FAKE_PROVIDER",
    }
    values.update(overrides)
    return NormalizedTransactionEvent(**values)


if __name__ == "__main__":
    unittest.main()
