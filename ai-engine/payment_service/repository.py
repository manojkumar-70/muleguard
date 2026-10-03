"""Persistence contract for a future payment-service repository."""

from typing import Protocol

from payment_service.schemas import (
    DetectionResult,
    HumanReview,
    Payment,
    ProviderEvent,
    SimulatedIntervention,
)


class PaymentRepository(Protocol):
    """Storage operations and uniqueness guarantees required by the service.

    Implementations must atomically enforce uniqueness for
    ``(idempotency_scope, idempotency_key_digest)`` and
    ``(provider_name, provider_event_id)``. Repeating a key with a different
    request digest must fail rather than overwrite the original payment.
    """

    def get_or_create_payment(
        self,
        idempotency_scope: str,
        idempotency_key_digest: str,
        request_digest: str,
        payment: Payment,
    ) -> tuple[Payment, bool]:
        """Return (existing-or-created payment, was_created)."""
        ...

    def get_payment(self, payment_id: str) -> Payment | None:
        """Fetch a payment by internal ID."""
        ...

    def update_payment(self, payment: Payment) -> None:
        """Persist a validated payment without applying implicit transitions."""
        ...

    def record_provider_event_once(self, event: ProviderEvent) -> bool:
        """Atomically store an event; return false for an exact duplicate."""
        ...

    def save_detection_result(self, result: DetectionResult) -> None:
        """Persist one protocol-tagged detection result."""
        ...

    def save_human_review(self, review: HumanReview) -> None:
        """Persist a human review as an append-only record."""
        ...

    def save_simulated_intervention(
        self,
        intervention: SimulatedIntervention,
    ) -> None:
        """Persist an explicitly simulated intervention record only."""
        ...
