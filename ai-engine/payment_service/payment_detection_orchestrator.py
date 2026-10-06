"""Explicit orchestration boundary for payment streaming detection."""

from payment_service.payment_detection_service import PaymentDetectionService
from payment_service.detection_persistence_service import (
    DetectionPersistenceService,
)
from payment_service.repository import PaymentRepository
from payment_service.schemas import DetectionResult, Payment, PaymentStatus
from payment_service.streaming_bridge import StreamingDetectionSession


_ELIGIBLE_STATUSES = {
    PaymentStatus.CAPTURED,
    PaymentStatus.AUTHORIZED,
}


class PaymentDetectionOrchestrator:
    """Coordinate eligible payments with a caller-owned detection session."""

    def __init__(
        self,
        detection_service: PaymentDetectionService | None = None,
        detection_persistence_service: DetectionPersistenceService | None = None,
    ):
        self._detection_service = detection_service or PaymentDetectionService()
        self._detection_persistence_service = (
            detection_persistence_service or DetectionPersistenceService()
        )

    def process_payment(
        self,
        payment: Payment,
        session: StreamingDetectionSession,
    ) -> dict:
        """Submit a successful payment and return the session's account results."""
        payment = Payment.model_validate(payment)
        if payment.payment_status not in _ELIGIBLE_STATUSES:
            raise ValueError(
                "Only CAPTURED or AUTHORIZED payments can be submitted for detection."
            )
        return self._detection_service.detect_payment(payment, session)

    def persist_detection_result(
        self,
        result: DetectionResult,
        repository: PaymentRepository,
    ) -> DetectionResult:
        """Explicitly persist a caller-produced result through its repository."""
        return self._detection_persistence_service.persist(result, repository)
