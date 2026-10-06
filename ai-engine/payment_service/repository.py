"""Persistence contract for a future payment-service repository."""

from typing import Protocol

from payment_service.investigation_schemas import (
    AccountActivityItem,
    DetectionResultInvestigationView,
    PaymentInvestigationSummary,
)
from payment_service.schemas import (
    DetectionResult,
    HumanReview,
    Payment,
    ProviderEvent,
    SimulatedIntervention,
    SyntheticCustomer,
    SyntheticMerchant,
)


class PaymentRepository(Protocol):
    """Storage operations and uniqueness guarantees required by the service.

    Implementations must atomically enforce uniqueness for
    ``(idempotency_scope, idempotency_key_digest)`` and
    ``(provider_name, provider_event_id)``. Repeating a key with a different
    request digest must fail rather than overwrite the original payment.
    """

    def save_customer(self, customer: SyntheticCustomer) -> None:
        """Persist a validated synthetic customer."""
        ...

    def get_customer(self, customer_id: str) -> SyntheticCustomer | None:
        """Fetch a customer by internal ID."""
        ...

    def save_merchant(self, merchant: SyntheticMerchant) -> None:
        """Persist a validated synthetic merchant."""
        ...

    def get_merchant(self, merchant_id: str) -> SyntheticMerchant | None:
        """Fetch a merchant by internal ID."""
        ...

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

    def get_payment_investigation(
        self,
        payment_id: str,
    ) -> PaymentInvestigationSummary:
        """Return a safe read-only investigation projection of a payment."""
        ...

    def list_account_payments(
        self,
        account_id: str,
        limit: int = 50,
    ) -> list[PaymentInvestigationSummary]:
        """List payments linked to an account, newest first."""
        ...

    def get_detection_result(
        self,
        detection_result_id: str,
    ) -> DetectionResultInvestigationView:
        """Fetch a persisted detection result by ID."""
        ...

    def list_detection_results_for_payment(
        self,
        payment_id: str,
    ) -> list[DetectionResultInvestigationView]:
        """List persisted detection results associated with a payment."""
        ...

    def list_account_activity(
        self,
        account_id: str,
        limit: int = 50,
    ) -> list[AccountActivityItem]:
        """List account-relative payment activity, newest first."""
        ...

    def update_payment(self, payment: Payment) -> None:
        """Persist a validated payment without applying implicit transitions."""
        ...

    def record_provider_event_once(self, event: ProviderEvent) -> bool:
        """Atomically store an event; return false for an exact duplicate."""
        ...

    def record_provider_event_and_update_payment(
        self,
        event: ProviderEvent,
        payment: Payment,
    ) -> bool:
        """Atomically persist a verified event and its validated payment update."""
        ...

    def save_detection_result(self, result: DetectionResult) -> None:
        """Persist one protocol-tagged detection result."""
        ...

    def save_human_review(self, review: HumanReview) -> None:
        """Persist a human review as an append-only record."""
        ...

    def list_human_reviews_for_payment(
        self,
        payment_id: str,
    ) -> list[HumanReview]:
        """List append-only reviews linked to a payment, newest first."""
        ...

    def save_simulated_intervention(
        self,
        intervention: SimulatedIntervention,
    ) -> None:
        """Persist an explicitly simulated intervention record only."""
        ...
