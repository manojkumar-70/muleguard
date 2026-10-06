"""Read-only contracts for future payment and account investigation views."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
    field_validator,
)

from payment_service.schemas import (
    DetectionResult,
    Payment,
    PaymentStatus,
    RiskSignal,
    RiskStatus,
)


class InvestigationDataSource(StrEnum):
    OFFLINE_DATASET = "OFFLINE_DATASET"
    STREAMING_PAYMENT_SERVICE = "STREAMING_PAYMENT_SERVICE"


class AccountActivityDirection(StrEnum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"


class InvestigationReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaymentInvestigationSummary(InvestigationReadModel):
    payment_id: str = Field(pattern=r"^PAY-[A-Za-z0-9-]{1,72}$")
    customer_id: str = Field(pattern=r"^CUST-[A-Za-z0-9-]{1,72}$")
    merchant_id: str = Field(pattern=r"^MER-[A-Za-z0-9-]{1,72}$")
    sender_account_id: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    receiver_account_id: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    amount_paise: StrictInt = Field(gt=0)
    currency: Literal["INR"]
    status: PaymentStatus
    created_at: datetime
    detection_result_id: str | None = Field(
        default=None, pattern=r"^DET-[A-Za-z0-9-]{1,72}$"
    )
    risk_status: RiskStatus
    data_source: InvestigationDataSource

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "created_at")

    @classmethod
    def from_payment(
        cls,
        payment: Payment,
        data_source: InvestigationDataSource,
    ) -> "PaymentInvestigationSummary":
        payment = Payment.model_validate(payment)
        return cls(
            payment_id=payment.payment_id,
            customer_id=payment.customer_id,
            merchant_id=payment.merchant_id,
            sender_account_id=payment.sender_account_id,
            receiver_account_id=payment.receiver_account_id,
            amount_paise=payment.amount_paise,
            currency=payment.currency,
            status=payment.payment_status,
            created_at=payment.created_at,
            detection_result_id=payment.detection_result_id,
            risk_status=payment.risk_status,
            data_source=data_source,
        )


class DetectionResultInvestigationView(InvestigationReadModel):
    detection_result_id: str = Field(pattern=r"^DET-[A-Za-z0-9-]{1,72}$")
    protocol: Literal["stream_frozen_model"]
    transaction_id: str = Field(pattern=r"^(?:TXN|PAY)-[A-Za-z0-9-]{1,74}$")
    payment_id: str | None = Field(
        default=None, pattern=r"^PAY-[A-Za-z0-9-]{1,72}$"
    )
    risk_score: FiniteFloat = Field(ge=0, le=100)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    signals: dict[str, RiskSignal]
    created_at: datetime
    disclaimer: str = Field(min_length=1, max_length=1000)
    data_source: InvestigationDataSource

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "created_at")

    @classmethod
    def from_detection_result(
        cls,
        result: DetectionResult,
        data_source: InvestigationDataSource,
    ) -> "DetectionResultInvestigationView":
        result = DetectionResult.model_validate(result)
        return cls(
            detection_result_id=result.detection_result_id,
            protocol=result.protocol,
            transaction_id=result.transaction_id,
            payment_id=result.payment_id,
            risk_score=result.risk_score,
            risk_level=result.risk_status,
            signals=result.signals,
            created_at=result.created_at,
            disclaimer=result.disclaimer,
            data_source=data_source,
        )


class AccountActivityItem(InvestigationReadModel):
    account_id: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    direction: AccountActivityDirection
    payment_id: str = Field(pattern=r"^PAY-[A-Za-z0-9-]{1,72}$")
    counterparty_account_id: str = Field(pattern=r"^ACC-[A-Za-z0-9-]{1,74}$")
    amount_paise: StrictInt = Field(gt=0)
    currency: Literal["INR"]
    payment_status: PaymentStatus
    created_at: datetime
    risk_status: RiskStatus
    data_source: InvestigationDataSource

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value):
        return _require_aware_datetime(value, "created_at")


def _require_aware_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value
