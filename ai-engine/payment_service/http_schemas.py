"""Public request and response contracts for the synthetic payment API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from payment_service.schemas import PaymentStatus, RiskStatus


class CreatePaymentRequest(BaseModel):
    """Closed HTTP request contract for a synthetic INR payment."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=r"^CUST-[A-Za-z0-9-]{1,72}$")
    merchant_id: str = Field(pattern=r"^MER-[A-Za-z0-9-]{1,72}$")
    amount_paise: StrictInt = Field(gt=0)
    currency: Literal["INR"]


class PublicPayment(BaseModel):
    """Payment response with no persistence or idempotency internals."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    payment_id: str = Field(pattern=r"^PAY-[A-Za-z0-9-]{1,72}$")
    customer_id: str = Field(pattern=r"^CUST-[A-Za-z0-9-]{1,72}$")
    merchant_id: str = Field(pattern=r"^MER-[A-Za-z0-9-]{1,72}$")
    amount_paise: StrictInt = Field(gt=0)
    currency: Literal["INR"]
    payment_status: PaymentStatus
    risk_status: RiskStatus
    created_at: datetime
    updated_at: datetime
    provider_status_updated_at: datetime | None = None
    failure_code: str | None = Field(
        default=None, pattern=r"^[A-Z0-9_]{1,40}$"
    )
