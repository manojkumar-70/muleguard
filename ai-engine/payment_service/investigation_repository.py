"""Shared validation and projections for read-only investigation queries."""

from datetime import timezone
from typing import TypeVar

from payment_service.investigation_schemas import (
    AccountActivityDirection,
    AccountActivityItem,
    DetectionResultInvestigationView,
    InvestigationDataSource,
    PaymentInvestigationSummary,
)
from payment_service.schemas import DetectionResult, Payment


MAX_INVESTIGATION_LIMIT = 500
_DATA_SOURCE = InvestigationDataSource.STREAMING_PAYMENT_SERVICE
_Record = TypeVar("_Record")


def validate_investigation_limit(limit: int) -> int:
    """Validate bounded pagination without accepting booleans as integers."""
    if type(limit) is not int or not 1 <= limit <= MAX_INVESTIGATION_LIMIT:
        raise ValueError(
            f"limit must be an integer between 1 and {MAX_INVESTIGATION_LIMIT}"
        )
    return limit


def sort_newest_first(records: list[_Record], tie_breaker) -> list[_Record]:
    """Sort by exact timestamp descending and a deterministic ascending key."""
    records.sort(key=tie_breaker)
    records.sort(
        key=lambda record: record.created_at.astimezone(timezone.utc),
        reverse=True,
    )
    return records


def payment_investigation(payment: Payment) -> PaymentInvestigationSummary:
    return PaymentInvestigationSummary.from_payment(payment, _DATA_SOURCE)


def detection_investigation(
    result: DetectionResult,
) -> DetectionResultInvestigationView:
    return DetectionResultInvestigationView.from_detection_result(
        result,
        _DATA_SOURCE,
    )


def account_activity(
    payment: Payment,
    account_id: str,
) -> list[AccountActivityItem]:
    """Project linked payment sides into safe account-relative activity."""
    payment = Payment.model_validate(payment)
    items = []
    if payment.sender_account_id == account_id:
        items.append(
            AccountActivityItem(
                account_id=account_id,
                direction=AccountActivityDirection.OUTGOING,
                payment_id=payment.payment_id,
                counterparty_account_id=payment.receiver_account_id,
                amount_paise=payment.amount_paise,
                currency=payment.currency,
                payment_status=payment.payment_status,
                created_at=payment.created_at,
                risk_status=payment.risk_status,
                data_source=_DATA_SOURCE,
            )
        )
    if payment.receiver_account_id == account_id:
        items.append(
            AccountActivityItem(
                account_id=account_id,
                direction=AccountActivityDirection.INCOMING,
                payment_id=payment.payment_id,
                counterparty_account_id=payment.sender_account_id,
                amount_paise=payment.amount_paise,
                currency=payment.currency,
                payment_status=payment.payment_status,
                created_at=payment.created_at,
                risk_status=payment.risk_status,
                data_source=_DATA_SOURCE,
            )
        )
    return items
