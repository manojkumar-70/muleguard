"""Explicit append-only human review operations for investigations."""

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from payment_service.errors import (
    DetectionResultNotFoundError,
    PaymentNotFoundError,
)
from payment_service.repository import PaymentRepository
from payment_service.schemas import (
    HumanReview,
    HumanReviewDecision,
    Payment,
)


class InvestigationReviewService:
    """Create and retrieve reviews without modifying payment state."""

    def __init__(
        self,
        repository: PaymentRepository,
        *,
        review_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._repository = repository
        self._review_id_factory = review_id_factory or (
            lambda: f"REV-{uuid4().hex}"
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_review(
        self,
        payment_id: str,
        detection_result_id: str,
        reviewer_id: str,
        decision: HumanReviewDecision,
        note: str | None = None,
    ) -> HumanReview:
        """Append a validated review linked to a detection for this payment."""
        payment = self._repository.get_payment(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment not found: {payment_id}")
        Payment.model_validate(payment)

        detection_results = self._repository.list_detection_results_for_payment(
            payment_id
        )
        if not any(
            result.detection_result_id == detection_result_id
            for result in detection_results
        ):
            raise DetectionResultNotFoundError(
                "Detection result not found for the requested payment."
            )

        review = HumanReview.model_validate(
            {
                "review_id": self._review_id_factory(),
                "detection_result_id": detection_result_id,
                "reviewer_id": reviewer_id,
                "decision": decision,
                "note": note,
                "created_at": self._clock(),
            }
        )
        self._repository.save_human_review(review)
        return review

    def list_reviews(self, payment_id: str) -> list[HumanReview]:
        """Return persisted reviews for a payment without changing state."""
        payment = self._repository.get_payment(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment not found: {payment_id}")
        Payment.model_validate(payment)
        return self._repository.list_human_reviews_for_payment(payment_id)
