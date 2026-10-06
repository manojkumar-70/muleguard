import json
import os
from pathlib import Path
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import (
    DATA_SOURCE_MODES,
    INVESTIGATION_MODE,
    OFFLINE_DATASET_MODE,
    render_investigation_mode,
)
from investigation_client import (
    API_BASE_URL_ENV,
    DEFAULT_API_BASE_URL,
    InvestigationAPIClient,
    InvestigationAPIError,
    configured_api_base_url,
    validate_loopback_base_url,
)


class InvestigationClientTests(unittest.TestCase):
    def test_offline_dataset_is_the_default_mode(self):
        self.assertEqual(DATA_SOURCE_MODES[0], OFFLINE_DATASET_MODE)

    def test_investigation_mode_is_an_explicit_option(self):
        self.assertIn(INVESTIGATION_MODE, DATA_SOURCE_MODES)

    def test_default_and_environment_base_url_are_loopback_only(self):
        self.assertEqual(configured_api_base_url(), DEFAULT_API_BASE_URL)
        with patch.dict(os.environ, {API_BASE_URL_ENV: "http://localhost:8123/"}):
            self.assertEqual(
                configured_api_base_url(),
                "http://localhost:8123",
            )
        for url in (
            "https://example.com",
            "http://127.0.0.1.evil.invalid",
            "http://user:secret@localhost:8000",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_loopback_base_url(url)

    def test_api_client_success_validates_model_and_data_source(self):
        client = _client(lambda request: _json_response(_payment()))
        try:
            payment = client.get_payment("PAY-0001")
        finally:
            client.close()

        self.assertEqual(payment.payment_id, "PAY-0001")
        self.assertEqual(payment.data_source.value, "STREAMING_PAYMENT_SERVICE")

    def test_empty_results_are_valid(self):
        client = _client(lambda request: _json_response([]))
        try:
            payments = client.list_account_payments("ACC-A")
        finally:
            client.close()
        self.assertEqual(payments, [])

    def test_404_is_reported_as_safe_not_found(self):
        client = _client(lambda request: httpx.Response(404))
        try:
            with self.assertRaisesRegex(InvestigationAPIError, "not found"):
                client.get_payment("PAY-0001")
        finally:
            client.close()

    def test_connection_failure_is_reported_safely(self):
        def fail(request):
            raise httpx.ConnectError("private connection details", request=request)

        client = _client(fail)
        try:
            with self.assertRaisesRegex(
                InvestigationAPIError,
                "Could not connect to the local investigation API",
            ) as raised:
                client.get_payment("PAY-0001")
        finally:
            client.close()
        self.assertNotIn("private connection details", str(raised.exception))

    def test_malformed_api_response_and_invalid_ids_are_rejected(self):
        client = _client(lambda request: _json_response({"unexpected": "shape"}))
        try:
            with self.assertRaisesRegex(InvestigationAPIError, "malformed"):
                client.get_payment("PAY-0001")
            with self.assertRaisesRegex(ValueError, "valid account ID"):
                client.list_account_activity("invalid-id")
            with self.assertRaisesRegex(ValueError, "valid payment ID"):
                client.get_payment("../PAY-0001")
        finally:
            client.close()


class InvestigationRenderingTests(unittest.TestCase):
    def test_account_activity_payment_details_and_detection_rendering(self):
        ui = _RecordingUI()
        render_investigation_mode(
            ui=ui,
            client=_SampleClient(),
        )

        self.assertIn(
            "STREAMING_PAYMENT_SERVICE",
            " ".join(ui.messages),
        )
        self.assertTrue(
            any(
                "Account activity timeline" in message
                for message in ui.messages
            )
        )
        self.assertTrue(
            any("Payment details — PAY-0001" in message for message in ui.messages)
        )
        self.assertTrue(
            any(
                "Detection result — DET-0001" in message
                for message in ui.messages
            )
        )
        rendered_rows = [
            row
            for table in ui.tables
            for row in table
        ]
        self.assertTrue(
            any(
                row.get("Data source") == "STREAMING_PAYMENT_SERVICE"
                for row in rendered_rows
            )
        )

    def test_empty_account_activity_renders_empty_state(self):
        ui = _RecordingUI()
        render_investigation_mode(ui=ui, client=_SampleClient(empty=True))

        self.assertTrue(
            any("No payments were found" in message for message in ui.messages)
        )
        self.assertTrue(
            any(
                "No payment activity" in message
                for message in ui.messages
            )
        )

    def test_api_errors_are_shown_without_failing_dashboard_render(self):
        ui = _RecordingUI()
        render_investigation_mode(ui=ui, client=_FailingClient())

        self.assertTrue(
            any(
                "Could not connect to the local investigation API" in message
                for message in ui.errors
            )
        )


class _RecordingUI:
    def __init__(self):
        self.messages = []
        self.errors = []
        self.tables = []

    def header(self, value):
        self.messages.append(value)

    def info(self, value):
        self.messages.append(value)

    def caption(self, value):
        self.messages.append(value)

    def text_input(self, *args, **kwargs):
        return "ACC-A"

    @contextmanager
    def spinner(self, message):
        self.messages.append(message)
        yield

    def subheader(self, value):
        self.messages.append(value)

    def dataframe(self, value, **kwargs):
        self.tables.append(value)

    def selectbox(self, _label, options, **kwargs):
        return options[0]

    def json(self, value):
        self.messages.append(json.dumps(value))

    def error(self, value):
        self.errors.append(value)


class _SampleClient:
    def __init__(self, empty=False):
        self.empty = empty

    def list_account_payments(self, account_id):
        return [] if self.empty else [_payment_model()]

    def list_account_activity(self, account_id):
        return [] if self.empty else [_activity_model()]

    def get_payment(self, payment_id):
        return _payment_model()

    def list_payment_detection_results(self, payment_id):
        return [] if self.empty else [_detection_model()]

    def get_detection_result(self, detection_result_id):
        return _detection_model()


class _FailingClient:
    def list_account_payments(self, account_id):
        raise InvestigationAPIError(
            "Could not connect to the local investigation API."
        )

    def close(self):
        pass


def _client(handler):
    return InvestigationAPIClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )


def _json_response(value):
    return httpx.Response(
        200,
        json=value,
        headers={"content-type": "application/json"},
    )


def _payment():
    return _payment_model().model_dump(mode="json")


def _payment_model():
    from payment_service.investigation_schemas import (
        InvestigationDataSource,
        PaymentInvestigationSummary,
    )

    return PaymentInvestigationSummary(
        payment_id="PAY-0001",
        customer_id="CUST-0001",
        merchant_id="MER-0001",
        sender_account_id="ACC-A",
        receiver_account_id="ACC-B",
        amount_paise=1000,
        currency="INR",
        status="CAPTURED",
        created_at="2025-01-01T00:00:00Z",
        detection_result_id="DET-0001",
        risk_status="NOT_EVALUATED",
        data_source=InvestigationDataSource.STREAMING_PAYMENT_SERVICE,
    )


def _activity_model():
    from payment_service.investigation_schemas import (
        AccountActivityDirection,
        AccountActivityItem,
        InvestigationDataSource,
    )

    return AccountActivityItem(
        account_id="ACC-A",
        direction=AccountActivityDirection.OUTGOING,
        payment_id="PAY-0001",
        counterparty_account_id="ACC-B",
        amount_paise=1000,
        currency="INR",
        payment_status="CAPTURED",
        created_at="2025-01-01T00:00:00Z",
        risk_status="NOT_EVALUATED",
        data_source=InvestigationDataSource.STREAMING_PAYMENT_SERVICE,
    )


def _detection_model():
    from payment_service.investigation_schemas import (
        DetectionResultInvestigationView,
        InvestigationDataSource,
    )
    from payment_service.schemas import RiskSignal

    return DetectionResultInvestigationView(
        detection_result_id="DET-0001",
        protocol="stream_frozen_model",
        transaction_id="PAY-0001",
        payment_id="PAY-0001",
        risk_score=25,
        risk_level="LOW",
        signals={
            "rule_based": RiskSignal(
                contribution=10,
                explanation="Synthetic test explanation.",
            )
        },
        created_at="2025-01-01T00:00:00Z",
        disclaimer="Synthetic test only.",
        data_source=InvestigationDataSource.STREAMING_PAYMENT_SERVICE,
    )


if __name__ == "__main__":
    unittest.main()
