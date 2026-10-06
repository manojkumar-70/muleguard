"""Read-only investigation endpoints for synthetic payment records."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from payment_service.config import PaymentAPISettings
from payment_service.errors import RepositoryDatabaseError
from payment_service.investigation_schemas import (
    AccountActivityItem,
    DetectionResultInvestigationView,
    PaymentInvestigationSummary,
)
from payment_service.mongo_repository import MongoPaymentRepository
from payment_service.repository import PaymentRepository
from payment_service.schemas import HumanReview, HumanReviewDecision
from payment_service.investigation_review_service import (
    InvestigationReviewService,
)
from payment_service.sqlite_repository import SQLitePaymentRepository


investigation_router = APIRouter(
    prefix="/investigation",
    tags=["payment-investigation"],
)
_MAX_LIMIT = 500


class CreateHumanReviewRequest(BaseModel):
    """Input fields for an explicitly submitted investigator review."""

    model_config = ConfigDict(extra="forbid")

    detection_result_id: str = Field(pattern=r"^DET-[A-Za-z0-9-]{1,72}$")
    reviewer_id: str = Field(pattern=r"^USER-[A-Za-z0-9-]{1,68}$")
    decision: HumanReviewDecision
    note: str | None = Field(default=None, max_length=4000)


def get_investigation_repository(request: Request) -> PaymentRepository:
    """Construct only the configured repository for enabled read operations."""
    settings: PaymentAPISettings = request.app.state.payment_api_settings
    if not settings.enabled:
        raise RepositoryDatabaseError(
            "Payment investigation service is disabled."
        )
    if settings.repository_backend == "mongodb":
        try:
            return MongoPaymentRepository()
        except ValueError as error:
            raise RepositoryDatabaseError(
                "MongoDB payment repository configuration is unavailable. "
                "Set MONGODB_URI and MONGODB_DATABASE."
            ) from error
    return SQLitePaymentRepository(path=settings.database_path)


def get_investigation_review_service(
    repository: Annotated[
        PaymentRepository,
        Depends(get_investigation_repository),
    ],
) -> InvestigationReviewService:
    return InvestigationReviewService(repository)


@investigation_router.get(
    "/payments/{payment_id}",
    response_model=PaymentInvestigationSummary,
)
def get_payment_investigation(
    payment_id: str,
    repository: Annotated[
        PaymentRepository,
        Depends(get_investigation_repository),
    ],
):
    return repository.get_payment_investigation(payment_id)


@investigation_router.get(
    "/accounts/{account_id}/payments",
    response_model=list[PaymentInvestigationSummary],
)
def list_account_payments(
    account_id: str,
    repository: Annotated[
        PaymentRepository,
        Depends(get_investigation_repository),
    ],
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
):
    return repository.list_account_payments(account_id, limit)


@investigation_router.get(
    "/accounts/{account_id}/activity",
    response_model=list[AccountActivityItem],
)
def list_account_activity(
    account_id: str,
    repository: Annotated[
        PaymentRepository,
        Depends(get_investigation_repository),
    ],
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
):
    return repository.list_account_activity(account_id, limit)


@investigation_router.get(
    "/detection-results/{detection_result_id}",
    response_model=DetectionResultInvestigationView,
)
def get_detection_result(
    detection_result_id: str,
    repository: Annotated[
        PaymentRepository,
        Depends(get_investigation_repository),
    ],
):
    return repository.get_detection_result(detection_result_id)


@investigation_router.get(
    "/payments/{payment_id}/detection-results",
    response_model=list[DetectionResultInvestigationView],
)
def list_payment_detection_results(
    payment_id: str,
    repository: Annotated[
        PaymentRepository,
        Depends(get_investigation_repository),
    ],
):
    return repository.list_detection_results_for_payment(payment_id)


@investigation_router.post(
    "/payments/{payment_id}/reviews",
    response_model=HumanReview,
    status_code=201,
)
def create_payment_review(
    payment_id: str,
    request_body: CreateHumanReviewRequest,
    service: Annotated[
        InvestigationReviewService,
        Depends(get_investigation_review_service),
    ],
):
    return service.create_review(
        payment_id=payment_id,
        detection_result_id=request_body.detection_result_id,
        reviewer_id=request_body.reviewer_id,
        decision=request_body.decision,
        note=request_body.note,
    )


@investigation_router.get(
    "/payments/{payment_id}/reviews",
    response_model=list[HumanReview],
)
def list_payment_reviews(
    payment_id: str,
    service: Annotated[
        InvestigationReviewService,
        Depends(get_investigation_review_service),
    ],
):
    return service.list_reviews(payment_id)
