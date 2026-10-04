"""Explicitly isolated HTTP routes for local synthetic payments."""

from collections.abc import Callable
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from payment_service.config import PaymentAPISettings
from payment_service.errors import (
    IdempotencyConflictError,
    InvalidPaymentTransitionError,
    PaymentNotFoundError,
    RepositoryConstraintError,
    RepositoryDatabaseError,
)
from payment_service.http_schemas import CreatePaymentRequest, PublicPayment
from payment_service.provider import FakePaymentProvider
from payment_service.service import (
    CreatePaymentCommand,
    InactiveSyntheticEntityError,
    InvalidProviderOutcomeError,
    SyntheticEntityNotFoundError,
    SyntheticPaymentService,
)
from payment_service.sqlite_repository import SQLitePaymentRepository


payment_router = APIRouter(tags=["synthetic-payments"])
_LOGGER = logging.getLogger(__name__)


class PaymentAPIError(Exception):
    """Sanitized error response for the synthetic payment HTTP boundary."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


def get_payment_service(request: Request) -> SyntheticPaymentService:
    """Lazily construct the offline service only for an enabled payment API."""
    settings: PaymentAPISettings = request.app.state.payment_api_settings
    if not settings.enabled:
        raise PaymentAPIError(
            404, "payment_api_disabled", "Synthetic payment API is disabled."
        )
    repository = SQLitePaymentRepository(path=settings.database_path)
    return SyntheticPaymentService(repository, FakePaymentProvider())


@payment_router.post(
    "/payments",
    status_code=201,
    response_model=PublicPayment,
    responses={200: {"model": PublicPayment}},
    response_model_exclude_none=True,
)
def create_payment(
    request_body: CreatePaymentRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=256,
            pattern=r"^[\x21-\x7e]+$",
        ),
    ],
    service: Annotated[SyntheticPaymentService, Depends(get_payment_service)],
):
    try:
        result = service.create_payment_with_result(
            CreatePaymentCommand(**request_body.model_dump()),
            idempotency_key,
        )
    except (
        PaymentNotFoundError,
        SyntheticEntityNotFoundError,
        InactiveSyntheticEntityError,
        IdempotencyConflictError,
        RepositoryConstraintError,
        RepositoryDatabaseError,
        InvalidPaymentTransitionError,
        InvalidProviderOutcomeError,
        PaymentAPIError,
    ):
        raise
    except ValidationError as error:
        raise PaymentAPIError(
            502,
            "invalid_provider_outcome",
            "The synthetic provider returned an invalid outcome.",
        ) from error
    except Exception as error:
        _log_unexpected_failure("create", error)
        raise PaymentAPIError(
            502,
            "synthetic_provider_error",
            "Synthetic payment execution failed.",
        ) from error

    return JSONResponse(
        status_code=201 if result.was_created else 200,
        content=PublicPayment.model_validate(result.payment).model_dump(
            mode="json", exclude_none=True
        ),
    )


@payment_router.get(
    "/payments/{payment_id}",
    response_model=PublicPayment,
    response_model_exclude_none=True,
)
def get_payment(
    payment_id: str,
    service: Annotated[SyntheticPaymentService, Depends(get_payment_service)],
):
    try:
        payment = service.get_payment(payment_id)
        return PublicPayment.model_validate(payment)
    except (
        PaymentNotFoundError,
        RepositoryDatabaseError,
        PaymentAPIError,
    ):
        raise
    except Exception as error:
        _log_unexpected_failure("retrieve", error)
        raise PaymentAPIError(
            503, "payment_service_unavailable", "Payment service is unavailable."
        ) from error


def payment_error_handler(
    status_code: int, code: str, message: str
) -> Callable:
    """Create an exception handler that never serializes exception text."""

    async def handle_error(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": message}},
        )

    return handle_error


def _log_unexpected_failure(operation: str, error: Exception) -> None:
    _LOGGER.error(
        "Synthetic payment %s failed (exception_type=%s)",
        operation,
        type(error).__name__,
    )
