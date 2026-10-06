"""Explicit persistence service for caller-produced detection results."""

from payment_service.repository import PaymentRepository
from payment_service.schemas import DetectionResult


class DetectionPersistenceService:
    """Persist a validated result through the payment repository contract."""

    def persist(
        self,
        result: DetectionResult,
        repository: PaymentRepository,
    ) -> DetectionResult:
        """Save and return the supplied detection result without side effects."""
        validated_result = DetectionResult.model_validate(result)
        repository.save_detection_result(validated_result)
        return validated_result
