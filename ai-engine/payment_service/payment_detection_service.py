"""Explicit opt-in integration from payments to a streaming detection session."""

from payment_service.payment_event_adapter import submit_payment_for_detection
from payment_service.schemas import Payment
from payment_service.streaming_bridge import StreamingDetectionSession


class PaymentDetectionService:
    """Submit supplied payments to a caller-managed streaming session."""

    def detect_payment(
        self,
        payment: Payment,
        session: StreamingDetectionSession,
    ) -> dict:
        """Return the session result without changing the payment itself."""
        return submit_payment_for_detection(payment, session)
