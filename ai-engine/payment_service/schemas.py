"""Strict synthetic payment contracts and lifecycle validation."""

from datetime import datetime
from enum import StrEnum
import ipaddress
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
    field_validator,
    model_validator,
)


class ContractModel(BaseModel):
    """Base for closed contracts: undeclared fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class RiskStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    ERROR = "ERROR"


class ProviderSignatureStatus(StrEnum):
    VERIFIED = "VERIFIED"
    INVALID = "INVALID"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ProviderEventProcessingStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class HumanReviewDecision(StrEnum):
    CONFIRMED_FOR_REVIEW = "CONFIRMED_FOR_REVIEW"
    NOT_SUSPICIOUS = "NOT_SUSPICIOUS"
    NEEDS_MORE_INFORMATION = "NEEDS_MORE_INFORMATION"
    UNRESOLVED = "UNRESOLVED"


class SimulatedInterventionType(StrEnum):
    SIMULATED_HOLD = "SIMULATED_HOLD"
    SIMULATED_REVIEW = "SIMULATED_REVIEW"
    SIMULATED_RELEASE = "SIMULATED_RELEASE"


class RiskIndicator(ContractModel):
    code: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=2000)


class RiskSignal(ContractModel):
    contribution: FiniteFloat = Field(ge=0, le=100)
    explanation: str = Field(min_length=1, max_length=2000)
    indicators: list[RiskIndicator] = Field(default_factory=list)


class SyntheticCustomer(ContractModel):
    customer_id: str = Field(pattern=r"^CUST-[A-Za-z0-9-]{1,72}$")
    display_name: str = Field(min_length=1, max_length=120)
    account_id: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "created_at")


class SyntheticMerchant(ContractModel):
    merchant_id: str = Field(pattern=r"^MER-[A-Za-z0-9-]{1,72}$")
    display_name: str = Field(min_length=1, max_length=120)
    account_id: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    currency: Literal["INR"] = "INR"
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"
    created_at: datetime
    provider_merchant_reference: str | None = Field(
        default=None, min_length=1, max_length=120
    )

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "created_at")


class Payment(ContractModel):
    payment_id: str = Field(pattern=r"^PAY-[A-Za-z0-9-]{1,72}$")
    customer_id: str = Field(pattern=r"^CUST-[A-Za-z0-9-]{1,72}$")
    merchant_id: str = Field(pattern=r"^MER-[A-Za-z0-9-]{1,72}$")
    sender_account_id: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    receiver_account_id: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    amount_paise: StrictInt = Field(gt=0)
    currency: Literal["INR"]
    payment_status: PaymentStatus
    risk_status: RiskStatus = RiskStatus.NOT_EVALUATED
    provider_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    provider_payment_reference: str | None = Field(
        default=None, min_length=1, max_length=120
    )
    idempotency_key_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    updated_at: datetime
    provider_status_updated_at: datetime | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=100)
    detection_protocol: str | None = Field(default=None, min_length=1, max_length=80)
    detection_result_id: str | None = Field(
        default=None, pattern=r"^DET-[A-Za-z0-9-]{1,72}$"
    )

    @field_validator(
        "created_at",
        "updated_at",
        "provider_status_updated_at",
    )
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value, info):
        if value is None:
            return value
        return _require_aware_datetime(value, info.field_name)


class ProviderEvent(ContractModel):
    provider_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    provider_event_id: str = Field(min_length=1, max_length=160)
    provider_payment_reference: str = Field(min_length=1, max_length=120)
    event_type: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    received_at: datetime
    signature_status: ProviderSignatureStatus
    payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_status: PaymentStatus | None = None
    processing_status: ProviderEventProcessingStatus = (
        ProviderEventProcessingStatus.RECEIVED
    )
    failure_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("occurred_at", "received_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value, info):
        return _require_aware_datetime(value, info.field_name)


class NormalizedTransactionEvent(ContractModel):
    transaction_id: str = Field(pattern=r"^(?:TXN|PAY)-[A-Za-z0-9-]{1,74}$")
    sender: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    receiver: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    amount_paise: StrictInt = Field(gt=0)
    currency: Literal["INR"]
    timestamp: datetime
    status: Literal["SUCCESS", "DECLINED"]
    device_id: str | None = Field(
        default=None, pattern=r"^DEV-[A-Za-z0-9-]{1,74}$"
    )
    ip_address: str | None = None
    payment_id: str = Field(pattern=r"^PAY-[A-Za-z0-9-]{1,72}$")
    source: str = Field(
        pattern=r"^(?:[A-Z][A-Z0-9_-]{0,39}|synthetic_payment_service)$"
    )

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "timestamp")

    @field_validator("ip_address")
    @classmethod
    def ip_address_must_be_documentation_address(cls, value):
        if value is None:
            return value
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError(
                "ip_address must be an IPv4 documentation address"
            ) from error
        documentation_networks = (
            ipaddress.ip_network("192.0.2.0/24"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"),
        )
        if address.version != 4 or not any(
            address in network for network in documentation_networks
        ):
            raise ValueError(
                "Only reserved synthetic IPv4 documentation addresses are accepted"
            )
        return value


class DetectionResult(ContractModel):
    detection_result_id: str = Field(pattern=r"^DET-[A-Za-z0-9-]{1,72}$")
    payment_id: str = Field(pattern=r"^PAY-[A-Za-z0-9-]{1,72}$")
    transaction_id: str = Field(pattern=r"^TXN-[A-Za-z0-9-]{1,74}$")
    protocol: Literal["stream_frozen_model"]
    risk_score: FiniteFloat = Field(ge=0, le=100)
    risk_status: Literal["LOW", "MEDIUM", "HIGH"]
    signals: dict[str, RiskSignal]
    created_at: datetime
    disclaimer: str = Field(min_length=1, max_length=1000)

    @field_validator("created_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "created_at")


class HumanReview(ContractModel):
    review_id: str = Field(pattern=r"^REV-[A-Za-z0-9-]{1,72}$")
    detection_result_id: str = Field(pattern=r"^DET-[A-Za-z0-9-]{1,72}$")
    reviewer_id: str = Field(pattern=r"^USER-[A-Za-z0-9-]{1,68}$")
    decision: HumanReviewDecision
    note: str | None = Field(default=None, max_length=4000)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "created_at")


class ImpactEstimate(ContractModel):
    metric: str = Field(min_length=1, max_length=120)
    baseline_value: FiniteFloat
    simulated_value: FiniteFloat
    unit: str = Field(min_length=1, max_length=40)


class SimulatedIntervention(ContractModel):
    intervention_id: str = Field(pattern=r"^INT-[A-Za-z0-9-]{1,72}$")
    payment_id: str | None = Field(
        default=None, pattern=r"^PAY-[A-Za-z0-9-]{1,72}$"
    )
    account_id: str | None = Field(
        default=None, pattern=r"^ACC-[A-Za-z0-9-]{1,74}$"
    )
    simulation_type: SimulatedInterventionType
    assumptions: list[str] = Field(min_length=1, max_length=50)
    estimated_impact: ImpactEstimate | None = None
    created_by: str = Field(pattern=r"^USER-[A-Za-z0-9-]{1,68}$")
    created_at: datetime
    execution_status: Literal["SIMULATED"] = "SIMULATED"

    @field_validator("created_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "created_at")

    @field_validator("assumptions")
    @classmethod
    def assumptions_must_be_nonempty(cls, values):
        if any(not assumption.strip() for assumption in values):
            raise ValueError("assumptions must not contain empty values")
        return values

    @model_validator(mode="after")
    def validate_target(self):
        if not self.payment_id and not self.account_id:
            raise ValueError("payment_id or account_id is required")
        return self


ALLOWED_PAYMENT_TRANSITIONS = {
    PaymentStatus.CREATED: frozenset(
        {
            PaymentStatus.PENDING,
            PaymentStatus.AUTHORIZED,
            PaymentStatus.CAPTURED,
            PaymentStatus.DECLINED,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
        }
    ),
    PaymentStatus.PENDING: frozenset(
        {
            PaymentStatus.AUTHORIZED,
            PaymentStatus.CAPTURED,
            PaymentStatus.DECLINED,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
            PaymentStatus.EXPIRED,
        }
    ),
    PaymentStatus.AUTHORIZED: frozenset(
        {
            PaymentStatus.CAPTURED,
            PaymentStatus.CANCELLED,
            PaymentStatus.FAILED,
            PaymentStatus.EXPIRED,
        }
    ),
    PaymentStatus.CAPTURED: frozenset(),
    PaymentStatus.DECLINED: frozenset(),
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.CANCELLED: frozenset(),
    PaymentStatus.EXPIRED: frozenset(),
}


def is_valid_payment_transition(current, target):
    """Return whether a canonical payment-state transition is allowed."""
    current_status = PaymentStatus(current)
    target_status = PaymentStatus(target)
    return target_status in ALLOWED_PAYMENT_TRANSITIONS[current_status]


def validate_payment_transition(current, target):
    """Raise ValueError when a requested payment-state transition is invalid."""
    current_status = PaymentStatus(current)
    target_status = PaymentStatus(target)
    if target_status not in ALLOWED_PAYMENT_TRANSITIONS[current_status]:
        raise ValueError(
            f"Invalid payment status transition: "
            f"{current_status.value} -> {target_status.value}"
        )


def _require_aware_datetime(value, field_name):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value
