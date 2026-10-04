"""Offline provider contracts for synthetic payment execution."""

import hashlib
import re
from typing import Protocol

from pydantic import ConfigDict, Field

from payment_service.schemas import ContractModel, Payment, PaymentStatus


class ProviderResult(ContractModel):
    """Validated outcome returned by a provider adapter."""

    model_config = ConfigDict(extra="forbid")

    provider_payment_reference: str = Field(
        pattern=r"^FAKE-PAY-[A-Fa-f0-9]{16,64}$"
    )
    payment_status: PaymentStatus
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,40}$")


class PaymentProvider(Protocol):
    """Provider boundary; implementations in this phase must remain offline."""

    @property
    def provider_name(self) -> str:
        """Return a lowercase provider identifier."""
        ...

    def create_payment(self, payment: Payment) -> ProviderResult:
        """Return a deterministic synthetic outcome for a persisted payment."""
        ...


class ControlledProviderFailure(Exception):
    """A bounded, non-sensitive simulated provider failure."""

    def __init__(self, failure_code: str):
        if re.fullmatch(r"[A-Z0-9_]{1,40}", failure_code) is None:
            raise ValueError("failure_code must be a bounded uppercase code")
        self.failure_code = failure_code
        super().__init__(f"Synthetic provider failed: {self.failure_code}")


class FakePaymentProvider:
    """Return deterministic outcomes without network or payment-rail access."""

    provider_name = "fake"

    def __init__(
        self,
        outcome=PaymentStatus.CAPTURED,
        failure_code=None,
    ):
        self.outcome = PaymentStatus(outcome)
        if self.outcome == PaymentStatus.CREATED:
            raise ValueError("Fake provider outcome cannot be CREATED")
        if failure_code is not None and re.fullmatch(
            r"[A-Z0-9_]{1,40}", failure_code
        ) is None:
            raise ValueError("failure_code must be a bounded uppercase code")
        self.failure_code = failure_code
        self.calls = 0

    def create_payment(self, payment: Payment) -> ProviderResult:
        payment = Payment.model_validate(payment)
        self.calls += 1
        reference_digest = hashlib.sha256(
            payment.payment_id.encode("utf-8")
        ).hexdigest()[:32]
        provider_reference = f"FAKE-PAY-{reference_digest}"
        if self.failure_code is not None:
            raise ControlledProviderFailure(self.failure_code)
        return ProviderResult(
            provider_payment_reference=provider_reference,
            payment_status=self.outcome,
            failure_code=(
                self.failure_code
                if self.outcome == PaymentStatus.FAILED
                else None
            ),
        )
