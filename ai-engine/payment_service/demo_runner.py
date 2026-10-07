"""Run the deterministic, local-only Phase 7.2 MuleGuard demonstration."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

AI_ENGINE_PATH = Path(__file__).resolve().parents[1]
if str(AI_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_PATH))

from payment_service.detection_persistence_service import (
    DetectionPersistenceService,
)
from payment_service.demo_fixture import (
    FOCAL_ACCOUNT_ID,
    WARMUP_PAYMENT_COUNT,
    DemoFixture,
    DemoPaymentSpec,
    build_demo_fixture,
    seed_demo_entities,
)
from payment_service.payment_detection_orchestrator import (
    PaymentDetectionOrchestrator,
)
from payment_service.provider import FakePaymentProvider
from payment_service.schemas import (
    DetectionResult,
    Payment,
    RiskIndicator,
    RiskSignal,
)
from payment_service.service import CreatePaymentCommand, SyntheticPaymentService
from payment_service.sqlite_database import DEFAULT_DATABASE_PATH
from payment_service.sqlite_repository import SQLitePaymentRepository
from payment_service.streaming_bridge import StreamingDetectionSession
from risk_aggregation import DISCLAIMER


DEMO_DETECTION_RESULT_ID = "DET-PHASE72-0001"
_DEMO_DISCLAIMER = (
    f"{DISCLAIMER} Demo-only projection: the persisted scalar is an account-level "
    "streaming score mapped for the existing DetectionResult contract; it is not "
    "a transaction-level model score."
)


@dataclass(frozen=True)
class DemoRun:
    """Structured outputs from a completed demo run."""

    database_path: Path
    fixture: DemoFixture
    payments: tuple[Payment, ...]
    warmup_progress: tuple[dict, ...]
    streaming_results: tuple[dict, ...]
    first_scored_result: dict
    focal_payment: Payment
    detection_result: DetectionResult
    focal_account_risk_score: float
    focal_account_risk_level: str
    focal_alert_generated: bool
    provider_call_count: int
    session: StreamingDetectionSession


def run_demo(database_path: str | Path | None = None) -> DemoRun:
    """Seed, create, detect, and persist the fixture in a fresh SQLite database."""
    selected_path = _new_demo_database_path() if database_path is None else Path(
        database_path
    ).expanduser().resolve()
    _validate_new_demo_database_path(selected_path)

    fixture = build_demo_fixture()
    repository = SQLitePaymentRepository(selected_path)
    seed_demo_entities(repository, fixture)

    provider = FakePaymentProvider()
    current_payment_time = fixture.payments[0].created_at
    id_sequence = 0

    def next_payment_id() -> str:
        nonlocal id_sequence
        id_sequence += 1
        return f"{id_sequence:016x}"

    def payment_clock() -> datetime:
        return current_payment_time

    payment_service = SyntheticPaymentService(
        repository,
        provider,
        id_factory=next_payment_id,
        clock=payment_clock,
    )
    orchestrator = PaymentDetectionOrchestrator()
    session = StreamingDetectionSession(
        warmup_events=WARMUP_PAYMENT_COUNT,
        seed=fixture.seed,
    )

    payments = []
    warmup_progress = []
    streaming_results = []
    first_scored_result = None
    focal_spec = next(
        spec for spec in reversed(fixture.payments) if spec.is_focal_payment
    )
    focal_payment = None

    for spec in fixture.payments:
        current_payment_time = spec.created_at
        payment = _create_payment(spec, payment_service)
        if payment.payment_id != spec.payment_id:
            raise AssertionError(
                "Payment service did not produce the fixture's deterministic ID."
            )
        if payment.payment_status.value not in {"CAPTURED", "AUTHORIZED"}:
            raise AssertionError("The fake provider returned an ineligible payment.")
        payments.append(payment)

        result = orchestrator.process_payment(payment, session)
        streaming_results.append(result)
        if result["phase"] == "warmup":
            warmup_progress.append(result["warmup_state"])
        elif first_scored_result is None:
            first_scored_result = result

        if spec.sequence == focal_spec.sequence:
            focal_payment = payment

    if first_scored_result is None or focal_payment is None:
        raise AssertionError("Demo fixture did not produce scored focal payments.")

    account_state = session.accounts.get(FOCAL_ACCOUNT_ID)
    if account_state is None or not account_state["risk_score_progression"]:
        raise AssertionError("Focal account has no account-level streaming result.")
    latest_risk = account_state["risk_score_progression"][-1]
    focal_explanation = account_state["first_alert_explanation"]
    alert_generated = account_state["first_alert_timestamp"] is not None

    detection_result = project_account_result_for_demo(
        payment=focal_payment,
        detection_result_id=DEMO_DETECTION_RESULT_ID,
        account_id=FOCAL_ACCOUNT_ID,
        account_risk_score=latest_risk["risk_score"],
        account_risk_level=latest_risk["risk_level"],
        streaming_explanation=focal_explanation,
        created_at=focal_payment.created_at,
    )
    DetectionPersistenceService().persist(detection_result, repository)

    linked_payment = focal_payment.model_copy(
        update={
            "detection_protocol": detection_result.protocol,
            "detection_result_id": detection_result.detection_result_id,
        }
    )
    repository.update_payment(linked_payment)
    stored_payment = repository.get_payment(focal_payment.payment_id)
    stored_result = repository.get_detection_result(
        detection_result.detection_result_id
    )
    if stored_payment is None or stored_result.detection_result_id != (
        detection_result.detection_result_id
    ):
        raise AssertionError("Persisted demo detection could not be retrieved.")
    if (
        stored_payment.payment_status != focal_payment.payment_status
        or stored_payment.risk_status != focal_payment.risk_status
    ):
        raise AssertionError("Detection persistence changed payment state.")

    return DemoRun(
        database_path=selected_path,
        fixture=fixture,
        payments=tuple(payments),
        warmup_progress=tuple(warmup_progress),
        streaming_results=tuple(streaming_results),
        first_scored_result=first_scored_result,
        focal_payment=stored_payment,
        detection_result=detection_result,
        focal_account_risk_score=float(latest_risk["risk_score"]),
        focal_account_risk_level=latest_risk["risk_level"],
        focal_alert_generated=alert_generated,
        provider_call_count=provider.calls,
        session=session,
    )


def project_account_result_for_demo(
    *,
    payment: Payment,
    detection_result_id: str,
    account_id: str,
    account_risk_score: float,
    account_risk_level: str,
    streaming_explanation: dict | None,
    created_at: datetime,
) -> DetectionResult:
    """Project account evidence into the scalar contract for demo display only.

    The score mapping is identity: the actual account-level aggregate score is
    copied unchanged into ``DetectionResult.risk_score``. This is explicitly
    not a transaction-level model score. The evidence records the mapping and
    the streaming explanation (when the detector produced one).
    """
    evidence = {
        "account_id": account_id,
        "account_risk_level": account_risk_level,
        "account_risk_score": float(account_risk_score),
        "projection_method": (
            "identity_copy_of_account_level_streaming_score; "
            "not_a_transaction_level_model_score"
        ),
        "streaming_explanation": streaming_explanation,
    }
    return DetectionResult(
        detection_result_id=detection_result_id,
        payment_id=payment.payment_id,
        transaction_id=payment.payment_id,
        protocol="stream_frozen_model",
        risk_score=float(account_risk_score),
        risk_status=account_risk_level,
        signals={
            "demo_account_level_projection": RiskSignal(
                contribution=float(account_risk_score),
                explanation=json.dumps(
                    evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
                indicators=[
                    RiskIndicator(
                        code="account_level_identity_projection",
                        explanation=(
                            "Demo-only identity mapping of the actual account-level "
                            "streaming score. It is not transaction-level risk."
                        ),
                    )
                ],
            )
        },
        created_at=created_at,
        disclaimer=_DEMO_DISCLAIMER,
    )


def _create_payment(
    spec: DemoPaymentSpec,
    payment_service: SyntheticPaymentService,
) -> Payment:
    return payment_service.create_payment(
        CreatePaymentCommand(
            customer_id=spec.customer_id,
            merchant_id=spec.merchant_id,
            amount_paise=spec.amount_paise,
            currency="INR",
        ),
        idempotency_key=f"phase72-demo-payment-{spec.sequence:03d}",
    )


def _new_demo_database_path() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="muleguardai-phase72-"))
    return directory / "demo.sqlite3"


def _validate_new_demo_database_path(path: Path) -> None:
    resolved_path = path.resolve()
    if resolved_path == DEFAULT_DATABASE_PATH.resolve():
        raise ValueError("The default payment database cannot be used for the demo.")
    if resolved_path.suffix.lower() not in {".sqlite3", ".sqlite", ".db"}:
        raise ValueError("Demo database path must use a SQLite file extension.")
    if resolved_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite an existing demo database: {resolved_path}"
        )
    project_data_path = Path(__file__).resolve().parents[1] / "data"
    if resolved_path.is_relative_to(project_data_path.resolve()):
        raise ValueError("Demo database must not be placed in the project data folder.")


def print_presenter_report(run: DemoRun) -> None:
    """Print only concise, synthetic presenter-facing run details."""
    print("MuleGuardAI Phase 7.2 synthetic demo")
    print(f"Database path: {run.database_path}")
    print(f"Focal account ID: {FOCAL_ACCOUNT_ID}")
    print(f"Focal payment ID: {run.focal_payment.payment_id}")
    print(f"Warm-up count: {len(run.warmup_progress)}")
    print(f"Scored-event count: {len(run.streaming_results) - len(run.warmup_progress)}")
    print(f"Alert generated: {'YES' if run.focal_alert_generated else 'NO'}")
    print(
        "Focal account risk: "
        f"{run.focal_account_risk_level} "
        f"({run.focal_account_risk_score:.2f}/100)"
    )
    print(f"Persisted detection result ID: {run.detection_result.detection_result_id}")
    print(f"Payment status: {run.focal_payment.payment_status.value}")
    print(f"Payment risk status: {run.focal_payment.risk_status.value}")
    print(f"Dashboard account ID to enter: {FOCAL_ACCOUNT_ID}")
    print("SIMULATED / NO REAL FINANCIAL ACTION")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the local synthetic MuleGuardAI Phase 7.2 demo."
    )
    parser.add_argument(
        "--database-path",
        type=Path,
        help=(
            "new demo SQLite path; defaults to a fresh file in the OS temporary "
            "directory. Existing files are never overwritten."
        ),
    )
    arguments = parser.parse_args()
    run = run_demo(arguments.database_path)
    print_presenter_report(run)


if __name__ == "__main__":
    main()
