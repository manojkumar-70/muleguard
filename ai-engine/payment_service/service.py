"""Application service for synthetic payment creation and retrieval."""

from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict, Field, StrictInt

from payment_service.errors import PaymentNotFoundError
from payment_service.provider import (
    ControlledProviderFailure,
    PaymentProvider,
    ProviderResult,
)
from payment_service.repository import PaymentRepository
from payment_service.schemas import (
    ContractModel,
    Payment,
    PaymentStatus,
    RiskStatus,
    validate_payment_transition,
)


_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,256}$")


class CreatePaymentCommand(ContractModel):
    """Closed, label-free request to create a synthetic demo payment."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=r"^CUST-[A-Za-z0-9-]{1,72}$")
    merchant_id: str = Field(pattern=r"^MER-[A-Za-z0-9-]{1,72}$")
    amount_paise: StrictInt = Field(gt=0)
    currency: Literal["INR"]


class PaymentServiceError(Exception):
    """Base class for application-service failures."""


class SyntheticEntityNotFoundError(PaymentServiceError):
    """A referenced customer or merchant does not exist."""


class InactiveSyntheticEntityError(PaymentServiceError):
    """A disabled synthetic customer or merchant cannot create a payment."""


class InvalidProviderOutcomeError(PaymentServiceError):
    """Provider result is invalid for the current payment state."""


@dataclass(frozen=True)
class PaymentCreationResult:
    """Payment and whether this call inserted its idempotency record."""

    payment: Payment
    was_created: bool


class SyntheticPaymentService:
    """Create and retrieve synthetic payments without detector integration."""

    def __init__(
        self,
        repository: PaymentRepository,
        provider: PaymentProvider,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._repository = repository
        self._provider = provider
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_payment(
        self,
        command: CreatePaymentCommand,
        idempotency_key: str,
    ) -> Payment:
        """Create, persist, and resolve a synthetic payment idempotently."""
        return self.create_payment_with_result(command, idempotency_key).payment

    def create_payment_with_result(
        self,
        command: CreatePaymentCommand,
        idempotency_key: str,
    ) -> PaymentCreationResult:
        """Create a payment and report whether this call created its record."""
        command = CreatePaymentCommand.model_validate(command)
        key_digest = _digest_idempotency_key(idempotency_key)
        customer = self._repository.get_customer(command.customer_id)
        merchant = self._repository.get_merchant(command.merchant_id)
        if customer is None:
            raise SyntheticEntityNotFoundError(
                f"Synthetic customer not found: {command.customer_id}"
            )
        if merchant is None:
            raise SyntheticEntityNotFoundError(
                f"Synthetic merchant not found: {command.merchant_id}"
            )
        if customer.status != "ACTIVE":
            raise InactiveSyntheticEntityError(
                f"Synthetic customer is inactive: {customer.customer_id}"
            )
        if merchant.status != "ACTIVE":
            raise InactiveSyntheticEntityError(
                f"Synthetic merchant is inactive: {merchant.merchant_id}"
            )
        if merchant.currency != command.currency:
            raise ValueError("Payment currency must match the merchant currency")

        request_digest = _request_digest(command)
        now = _aware_utc(self._clock())
        idempotency_scope = f"customer:{customer.customer_id}:create-payment"
        initial_payment = Payment(
            payment_id=_payment_id(self._id_factory()),
            customer_id=customer.customer_id,
            merchant_id=merchant.merchant_id,
            sender_account_id=customer.account_id,
            receiver_account_id=merchant.account_id,
            amount_paise=command.amount_paise,
            currency=command.currency,
            payment_status=PaymentStatus.CREATED,
            risk_status=RiskStatus.NOT_EVALUATED,
            provider_name=self._provider.provider_name,
            idempotency_key_digest=key_digest,
            created_at=now,
            updated_at=now,
        )

        payment, was_created = self._repository.get_or_create_payment(
            idempotency_scope=idempotency_scope,
            idempotency_key_digest=key_digest,
            request_digest=request_digest,
            payment=initial_payment,
        )
        if payment.payment_status != PaymentStatus.CREATED:
            return PaymentCreationResult(payment, was_created)

        try:
            provider_result = ProviderResult.model_validate(
                self._provider.create_payment(payment)
            )
            try:
                validate_payment_transition(
                    payment.payment_status,
                    provider_result.payment_status,
                )
            except ValueError as error:
                raise InvalidProviderOutcomeError(
                    "Synthetic provider returned an invalid payment status transition."
                ) from error
            if provider_result.payment_status == PaymentStatus.FAILED:
                failure_code = provider_result.failure_code or "SYNTHETIC_FAILURE"
            else:
                failure_code = provider_result.failure_code
            updated_payment = payment.model_copy(
                update={
                    "payment_status": provider_result.payment_status,
                    "provider_payment_reference": (
                        provider_result.provider_payment_reference
                    ),
                    "provider_status_updated_at": _aware_utc(self._clock()),
                    "updated_at": _aware_utc(self._clock()),
                    "failure_code": failure_code,
                }
            )
        except ControlledProviderFailure as error:
            updated_payment = payment.model_copy(
                update={
                    "payment_status": PaymentStatus.FAILED,
                    "provider_status_updated_at": _aware_utc(self._clock()),
                    "updated_at": _aware_utc(self._clock()),
                    "failure_code": error.failure_code,
                }
            )

        if updated_payment.risk_status != RiskStatus.NOT_EVALUATED:
            raise AssertionError(
                "Payment service must preserve NOT_EVALUATED risk status."
            )
        self._repository.update_payment(updated_payment)
        stored_payment = self._repository.get_payment(updated_payment.payment_id)
        if stored_payment is None:
            raise PaymentNotFoundError(
                "Payment disappeared after a successful persistence update."
            )
        return PaymentCreationResult(stored_payment, was_created)

    def get_payment(self, payment_id: str) -> Payment:
        """Retrieve a payment or raise the repository's typed not-found error."""
        payment = self._repository.get_payment(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment not found: {payment_id}")
        return payment


def _digest_idempotency_key(raw_key):
    if (
        not isinstance(raw_key, str)
        or _IDEMPOTENCY_KEY_PATTERN.fullmatch(raw_key) is None
    ):
        raise ValueError(
            "idempotency_key must contain 1 to 256 visible ASCII characters"
        )
    return hashlib.sha256(raw_key.encode("ascii")).hexdigest()


def _request_digest(command):
    canonical_request = {
        "amount_paise": command.amount_paise,
        "currency": command.currency,
        "customer_id": command.customer_id,
        "merchant_id": command.merchant_id,
    }
    encoded = json.dumps(
        canonical_request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _payment_id(value):
    normalized = value.replace("-", "")
    if re.fullmatch(r"[A-Fa-f0-9]{16,64}", normalized) is None:
        raise ValueError("id_factory must return 16 to 64 hexadecimal characters")
    return f"PAY-{normalized.lower()}"


def _aware_utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Service clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)
