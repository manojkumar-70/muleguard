"""Adapter from persisted synthetic payments to streaming detector events."""

from payment_service.schemas import (
    NormalizedTransactionEvent,
    Payment,
    PaymentStatus,
)
from payment_service.streaming_bridge import StreamingDetectionSession


_DETECTABLE_PAYMENT_STATUSES = {
    PaymentStatus.CAPTURED,
    PaymentStatus.AUTHORIZED,
}


def payment_to_transaction_event(payment: Payment) -> NormalizedTransactionEvent:
    """Project a captured or authorized payment to detector-safe fields."""
    payment = Payment.model_validate(payment)
    if payment.payment_status not in _DETECTABLE_PAYMENT_STATUSES:
        raise ValueError(
            "Only CAPTURED or AUTHORIZED payments can be submitted for detection."
        )

    return NormalizedTransactionEvent(
        transaction_id=payment.payment_id,
        sender=payment.sender_account_id,
        receiver=payment.receiver_account_id,
        amount_paise=payment.amount_paise,
        currency=payment.currency,
        timestamp=payment.created_at,
        status="SUCCESS",
        payment_id=payment.payment_id,
        source="synthetic_payment_service",
    )


def submit_payment_for_detection(
    payment: Payment,
    session: StreamingDetectionSession,
) -> dict:
    """Convert a payment and submit its normalized event to a live session."""
    event = payment_to_transaction_event(payment)
    return session.process_event(event)
