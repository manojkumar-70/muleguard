"""Safe loopback-only client for the payment investigation API."""

import ipaddress
import os
import re
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from payment_service.investigation_schemas import (
    AccountActivityItem,
    DetectionResultInvestigationView,
    InvestigationDataSource,
    PaymentInvestigationSummary,
)


DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
API_BASE_URL_ENV = "MULEGUARD_INVESTIGATION_API_BASE_URL"
_ACCOUNT_ID_PATTERN = re.compile(r"^ACC-[A-Za-z0-9-]{1,74}$")
_PAYMENT_ID_PATTERN = re.compile(r"^PAY-[A-Za-z0-9-]{1,72}$")
_DETECTION_ID_PATTERN = re.compile(r"^DET-[A-Za-z0-9-]{1,72}$")


class InvestigationAPIError(Exception):
    """Sanitized API client failure suitable for a user-facing message."""


def configured_api_base_url() -> str:
    return validate_loopback_base_url(
        os.environ.get(API_BASE_URL_ENV, DEFAULT_API_BASE_URL)
    )


def validate_loopback_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Investigation API URL must be a loopback HTTP(S) URL.")
    hostname = parsed.hostname.lower()
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname == "localhost"
    if not is_loopback:
        raise ValueError("Investigation API URL must target localhost only.")
    return value.rstrip("/")


class InvestigationAPIClient:
    """Fetch validated, read-only investigation views from loopback API only."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 5.0,
    ):
        self.base_url = validate_loopback_base_url(
            base_url or configured_api_base_url()
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def get_payment(self, payment_id: str) -> PaymentInvestigationSummary:
        payment_id = _validate_id(payment_id, _PAYMENT_ID_PATTERN, "payment")
        return self._get_model(
            f"/v1/investigation/payments/{payment_id}",
            PaymentInvestigationSummary,
        )

    def list_account_payments(
        self,
        account_id: str,
        limit: int = 50,
    ) -> list[PaymentInvestigationSummary]:
        account_id = _validate_id(account_id, _ACCOUNT_ID_PATTERN, "account")
        _validate_limit(limit)
        return self._get_model_list(
            f"/v1/investigation/accounts/{account_id}/payments",
            PaymentInvestigationSummary,
            params={"limit": limit},
        )

    def list_account_activity(
        self,
        account_id: str,
        limit: int = 50,
    ) -> list[AccountActivityItem]:
        account_id = _validate_id(account_id, _ACCOUNT_ID_PATTERN, "account")
        _validate_limit(limit)
        return self._get_model_list(
            f"/v1/investigation/accounts/{account_id}/activity",
            AccountActivityItem,
            params={"limit": limit},
        )

    def get_detection_result(
        self,
        detection_result_id: str,
    ) -> DetectionResultInvestigationView:
        detection_result_id = _validate_id(
            detection_result_id,
            _DETECTION_ID_PATTERN,
            "detection result",
        )
        return self._get_model(
            f"/v1/investigation/detection-results/{detection_result_id}",
            DetectionResultInvestigationView,
        )

    def list_payment_detection_results(
        self,
        payment_id: str,
    ) -> list[DetectionResultInvestigationView]:
        payment_id = _validate_id(payment_id, _PAYMENT_ID_PATTERN, "payment")
        return self._get_model_list(
            f"/v1/investigation/payments/{payment_id}/detection-results",
            DetectionResultInvestigationView,
        )

    def _get_model(self, path, model_type):
        payload = self._get_json(path)
        try:
            model = model_type.model_validate(payload)
        except (ValidationError, TypeError) as error:
            raise InvestigationAPIError(
                "The investigation API returned malformed data."
            ) from error
        _require_streaming_source(model)
        return model

    def _get_model_list(self, path, model_type, params=None):
        payload = self._get_json(path, params=params)
        if not isinstance(payload, list):
            raise InvestigationAPIError(
                "The investigation API returned malformed data."
            )
        try:
            models = [model_type.model_validate(item) for item in payload]
        except (ValidationError, TypeError) as error:
            raise InvestigationAPIError(
                "The investigation API returned malformed data."
            ) from error
        for model in models:
            _require_streaming_source(model)
        return models

    def _get_json(self, path, params=None):
        try:
            response = self._client.get(path, params=params)
        except httpx.ConnectError as error:
            raise InvestigationAPIError(
                "Could not connect to the local investigation API."
            ) from error
        except httpx.TimeoutException as error:
            raise InvestigationAPIError(
                "The local investigation API request timed out."
            ) from error
        except httpx.RequestError as error:
            raise InvestigationAPIError(
                "The local investigation API request failed."
            ) from error

        if response.status_code == 404:
            raise InvestigationAPIError(
                "The requested payment or detection result was not found."
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise InvestigationAPIError(
                f"The local investigation API returned HTTP {response.status_code}."
            )
        try:
            return response.json()
        except ValueError as error:
            raise InvestigationAPIError(
                "The investigation API returned malformed data."
            ) from error


def _validate_id(value, pattern, name):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"Enter a valid {name} ID.")
    return value


def _validate_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500.")


def _require_streaming_source(model):
    if model.data_source != InvestigationDataSource.STREAMING_PAYMENT_SERVICE:
        raise InvestigationAPIError(
            "The investigation API returned data from an unexpected source."
        )
