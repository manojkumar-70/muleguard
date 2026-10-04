"""SQLite implementation of the synthetic payment repository contract."""

from contextlib import contextmanager
import json
import re
import sqlite3

from payment_service.errors import (
    DetectionResultNotFoundError,
    IdempotencyConflictError,
    InvalidPaymentTransitionError,
    PaymentNotFoundError,
    ProviderEventConflictError,
    RepositoryConstraintError,
    RepositoryDatabaseError,
)
from payment_service.repository import PaymentRepository
from payment_service.schemas import (
    DetectionResult,
    HumanReview,
    Payment,
    PaymentStatus,
    ProviderEvent,
    ProviderEventProcessingStatus,
    ProviderSignatureStatus,
    SimulatedIntervention,
    SyntheticCustomer,
    SyntheticMerchant,
    validate_payment_transition,
)
from payment_service.sqlite_database import DEFAULT_DATABASE_PATH, SQLiteDatabase


_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class SQLitePaymentRepository(PaymentRepository):
    """Persist validated payment foundation records in a local SQLite file."""

    def __init__(
        self,
        path=DEFAULT_DATABASE_PATH,
        busy_timeout_ms=5000,
        database=None,
    ):
        self._database = database or SQLiteDatabase(
            path=path,
            busy_timeout_ms=busy_timeout_ms,
        )
        self._database.initialize()

    def save_customer(self, customer: SyntheticCustomer) -> None:
        customer = SyntheticCustomer.model_validate(customer)
        self._write(
            """
            INSERT INTO synthetic_customers
                (customer_id, display_name, account_id, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                customer.customer_id,
                customer.display_name,
                customer.account_id,
                customer.status,
                _timestamp(customer.created_at),
            ),
        )

    def get_customer(self, customer_id: str) -> SyntheticCustomer | None:
        try:
            with self._database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT customer_id, display_name, account_id, status, created_at
                    FROM synthetic_customers WHERE customer_id = ?
                    """,
                    (customer_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise RepositoryDatabaseError(
                f"Could not retrieve customer {customer_id}: {error}"
            ) from error
        if row is None:
            return None
        return SyntheticCustomer.model_validate(dict(row))

    def save_merchant(self, merchant: SyntheticMerchant) -> None:
        merchant = SyntheticMerchant.model_validate(merchant)
        self._write(
            """
            INSERT INTO synthetic_merchants
                (merchant_id, display_name, account_id, currency, status,
                 created_at, provider_merchant_reference)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merchant.merchant_id,
                merchant.display_name,
                merchant.account_id,
                merchant.currency,
                merchant.status,
                _timestamp(merchant.created_at),
                merchant.provider_merchant_reference,
            ),
        )

    def get_merchant(self, merchant_id: str) -> SyntheticMerchant | None:
        try:
            with self._database.connection() as connection:
                row = connection.execute(
                    """
                    SELECT merchant_id, display_name, account_id, currency, status,
                           created_at, provider_merchant_reference
                    FROM synthetic_merchants WHERE merchant_id = ?
                    """,
                    (merchant_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise RepositoryDatabaseError(
                f"Could not retrieve merchant {merchant_id}: {error}"
            ) from error
        if row is None:
            return None
        return SyntheticMerchant.model_validate(dict(row))

    def get_or_create_payment(
        self,
        idempotency_scope: str,
        idempotency_key_digest: str,
        request_digest: str,
        payment: Payment,
    ) -> tuple[Payment, bool]:
        payment = Payment.model_validate(payment)
        _validate_scope(idempotency_scope)
        _validate_digest(idempotency_key_digest, "idempotency_key_digest")
        _validate_digest(request_digest, "request_digest")
        if payment.idempotency_key_digest != idempotency_key_digest:
            raise ValueError(
                "idempotency_key_digest must match the payment contract value"
            )

        try:
            with self._transaction("IMMEDIATE") as connection:
                row = connection.execute(
                    """
                    SELECT * FROM payments
                    WHERE idempotency_scope = ? AND idempotency_key_digest = ?
                    """,
                    (idempotency_scope, idempotency_key_digest),
                ).fetchone()
                if row is not None:
                    if row["request_digest"] != request_digest:
                        raise IdempotencyConflictError(
                            "Idempotency key was reused with a different request."
                        )
                    return _payment_from_row(row), False

                self._validate_payment_accounts(connection, payment)
                values = _payment_values(
                    payment,
                    idempotency_scope=idempotency_scope,
                    request_digest=request_digest,
                )
                connection.execute(
                    """
                    INSERT INTO payments (
                        payment_id, customer_id, merchant_id, sender_account_id,
                        receiver_account_id, amount_paise, currency, payment_status,
                        risk_status, provider_name, provider_payment_reference,
                        idempotency_key_digest, idempotency_scope, request_digest,
                        created_at, updated_at, provider_status_updated_at,
                        failure_code, detection_protocol, detection_result_id
                    ) VALUES (
                        :payment_id, :customer_id, :merchant_id, :sender_account_id,
                        :receiver_account_id, :amount_paise, :currency,
                        :payment_status, :risk_status, :provider_name,
                        :provider_payment_reference, :idempotency_key_digest,
                        :idempotency_scope, :request_digest, :created_at,
                        :updated_at, :provider_status_updated_at, :failure_code,
                        :detection_protocol, :detection_result_id
                    )
                    """,
                    values,
                )
                return payment, True
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error, "create payment") from error

    def get_payment(self, payment_id: str) -> Payment | None:
        try:
            with self._database.connection() as connection:
                row = connection.execute(
                    "SELECT * FROM payments WHERE payment_id = ?",
                    (payment_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise RepositoryDatabaseError(
                f"Could not retrieve payment {payment_id}: {error}"
            ) from error
        if row is None:
            return None
        return _payment_from_row(row)

    def update_payment(self, payment: Payment) -> None:
        payment = Payment.model_validate(payment)
        try:
            with self._transaction("IMMEDIATE") as connection:
                self._update_payment_row(connection, payment)
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error, "update payment") from error

    def record_provider_event_once(self, event: ProviderEvent) -> bool:
        event = ProviderEvent.model_validate(event)
        try:
            with self._transaction("IMMEDIATE") as connection:
                return self._record_provider_event(
                    connection,
                    event,
                    mark_processed=False,
                )
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error, "record provider event") from error

    def record_provider_event_and_update_payment(
        self,
        event: ProviderEvent,
        payment: Payment,
    ) -> bool:
        event = ProviderEvent.model_validate(event)
        payment = Payment.model_validate(payment)
        if event.signature_status != ProviderSignatureStatus.VERIFIED:
            raise ValueError(
                "Only verified provider events may update payment status"
            )
        if event.normalized_status is None:
            raise ValueError(
                "A normalized provider status is required for atomic payment update"
            )
        if event.normalized_status != payment.payment_status:
            raise ValueError(
                "Payment status must match the provider event normalized_status"
            )

        try:
            with self._transaction("IMMEDIATE") as connection:
                inserted = self._record_provider_event(
                    connection,
                    event,
                    mark_processed=True,
                )
                if not inserted:
                    return False
                current = connection.execute(
                    "SELECT * FROM payments WHERE payment_id = ?",
                    (payment.payment_id,),
                ).fetchone()
                if current is None:
                    raise PaymentNotFoundError(
                        f"Payment not found: {payment.payment_id}"
                    )
                if current["provider_name"] != event.provider_name:
                    raise ValueError(
                        "Provider event provider_name does not match the payment"
                    )
                if (
                    current["provider_payment_reference"]
                    != event.provider_payment_reference
                ):
                    raise ValueError(
                        "Provider event reference does not match the payment"
                    )
                self._update_payment_row(connection, payment)
                return True
        except sqlite3.Error as error:
            raise _translate_sqlite_error(
                error, "process provider event and update payment"
            ) from error

    def save_detection_result(self, result: DetectionResult) -> None:
        result = DetectionResult.model_validate(result)
        signals_json = json.dumps(
            {
                key: signal.model_dump(mode="json")
                for key, signal in result.signals.items()
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        self._write(
            """
            INSERT INTO detection_results (
                detection_result_id, payment_id, transaction_id, protocol,
                risk_score, risk_status, signals_json, created_at, disclaimer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.detection_result_id,
                result.payment_id,
                result.transaction_id,
                result.protocol,
                result.risk_score,
                result.risk_status,
                signals_json,
                _timestamp(result.created_at),
                result.disclaimer,
            ),
        )

    def save_human_review(self, review: HumanReview) -> None:
        review = HumanReview.model_validate(review)
        self._write(
            """
            INSERT INTO human_reviews (
                review_id, detection_result_id, reviewer_id, decision, note,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                review.review_id,
                review.detection_result_id,
                review.reviewer_id,
                review.decision,
                review.note,
                _timestamp(review.created_at),
            ),
        )

    def save_simulated_intervention(
        self,
        intervention: SimulatedIntervention,
    ) -> None:
        intervention = SimulatedIntervention.model_validate(intervention)
        assumptions_json = json.dumps(
            intervention.assumptions,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        impact_json = (
            json.dumps(
                intervention.estimated_impact.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if intervention.estimated_impact is not None
            else None
        )
        self._write(
            """
            INSERT INTO simulated_interventions (
                intervention_id, payment_id, account_id, simulation_type,
                assumptions_json, estimated_impact_json, created_by, created_at,
                execution_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intervention.intervention_id,
                intervention.payment_id,
                intervention.account_id,
                intervention.simulation_type,
                assumptions_json,
                impact_json,
                intervention.created_by,
                _timestamp(intervention.created_at),
                intervention.execution_status,
            ),
        )

    def _record_provider_event(self, connection, event, mark_processed):
        existing = connection.execute(
            """
            SELECT payload_digest FROM provider_events
            WHERE provider_name = ? AND provider_event_id = ?
            """,
            (event.provider_name, event.provider_event_id),
        ).fetchone()
        if existing is not None:
            if existing["payload_digest"] != event.payload_digest:
                raise ProviderEventConflictError(
                    "Provider event ID was reused with a different payload digest."
                )
            return False

        _insert_provider_event(connection, event, mark_processed)
        return True

    @staticmethod
    def _validate_payment_accounts(connection, payment):
        customer = connection.execute(
            "SELECT account_id FROM synthetic_customers WHERE customer_id = ?",
            (payment.customer_id,),
        ).fetchone()
        merchant = connection.execute(
            "SELECT account_id FROM synthetic_merchants WHERE merchant_id = ?",
            (payment.merchant_id,),
        ).fetchone()
        if customer is None or merchant is None:
            return
        if customer["account_id"] != payment.sender_account_id:
            raise RepositoryConstraintError(
                "Payment sender_account_id must match its synthetic customer."
            )
        if merchant["account_id"] != payment.receiver_account_id:
            raise RepositoryConstraintError(
                "Payment receiver_account_id must match its synthetic merchant."
            )

    def _update_payment_row(self, connection, payment):
        current = connection.execute(
            "SELECT * FROM payments WHERE payment_id = ?",
            (payment.payment_id,),
        ).fetchone()
        if current is None:
            raise PaymentNotFoundError(f"Payment not found: {payment.payment_id}")

        immutable_pairs = (
            ("customer_id", payment.customer_id),
            ("merchant_id", payment.merchant_id),
            ("sender_account_id", payment.sender_account_id),
            ("receiver_account_id", payment.receiver_account_id),
            ("amount_paise", payment.amount_paise),
            ("currency", payment.currency),
            ("provider_name", payment.provider_name),
            ("idempotency_key_digest", payment.idempotency_key_digest),
            ("created_at", _timestamp(payment.created_at)),
        )
        for column, new_value in immutable_pairs:
            if current[column] != new_value:
                raise ValueError(f"Payment field '{column}' is immutable")

        old_status = PaymentStatus(current["payment_status"])
        if old_status != payment.payment_status:
            try:
                validate_payment_transition(old_status, payment.payment_status)
            except ValueError as error:
                raise InvalidPaymentTransitionError(str(error)) from error

        connection.execute(
            """
            UPDATE payments SET
                payment_status = ?,
                risk_status = ?,
                provider_payment_reference = ?,
                updated_at = ?,
                provider_status_updated_at = ?,
                failure_code = ?,
                detection_protocol = ?,
                detection_result_id = ?
            WHERE payment_id = ?
            """,
            (
                payment.payment_status,
                payment.risk_status,
                payment.provider_payment_reference,
                _timestamp(payment.updated_at),
                (
                    _timestamp(payment.provider_status_updated_at)
                    if payment.provider_status_updated_at is not None
                    else None
                ),
                payment.failure_code,
                payment.detection_protocol,
                payment.detection_result_id,
                payment.payment_id,
            ),
        )

    @contextmanager
    def _transaction(self, mode):
        begin_statements = {
            "IMMEDIATE": "BEGIN IMMEDIATE",
            "EXCLUSIVE": "BEGIN EXCLUSIVE",
        }
        begin_statement = begin_statements.get(mode)
        if begin_statement is None:
            raise ValueError(f"Unsupported SQLite transaction mode: {mode}")
        with self._database.connection() as connection:
            connection.execute(begin_statement)
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _write(self, statement, parameters):
        try:
            with self._transaction("IMMEDIATE") as connection:
                connection.execute(statement, parameters)
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error, "write payment data") from error


def _insert_provider_event(connection, event, mark_processed):
    connection.execute(
        """
        INSERT INTO provider_events (
            provider_name, provider_event_id, provider_payment_reference,
            event_type, occurred_at, received_at, signature_status,
            payload_digest, normalized_status, processing_status, failure_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.provider_name,
            event.provider_event_id,
            event.provider_payment_reference,
            event.event_type,
            _timestamp(event.occurred_at),
            _timestamp(event.received_at),
            event.signature_status,
            event.payload_digest,
            event.normalized_status,
            (
                ProviderEventProcessingStatus.PROCESSED
                if mark_processed
                else event.processing_status
            ),
            event.failure_reason,
        ),
    )


def _payment_values(payment, idempotency_scope, request_digest):
    return {
        "payment_id": payment.payment_id,
        "customer_id": payment.customer_id,
        "merchant_id": payment.merchant_id,
        "sender_account_id": payment.sender_account_id,
        "receiver_account_id": payment.receiver_account_id,
        "amount_paise": payment.amount_paise,
        "currency": payment.currency,
        "payment_status": payment.payment_status,
        "risk_status": payment.risk_status,
        "provider_name": payment.provider_name,
        "provider_payment_reference": payment.provider_payment_reference,
        "idempotency_key_digest": payment.idempotency_key_digest,
        "idempotency_scope": idempotency_scope,
        "request_digest": request_digest,
        "created_at": _timestamp(payment.created_at),
        "updated_at": _timestamp(payment.updated_at),
        "provider_status_updated_at": (
            _timestamp(payment.provider_status_updated_at)
            if payment.provider_status_updated_at is not None
            else None
        ),
        "failure_code": payment.failure_code,
        "detection_protocol": payment.detection_protocol,
        "detection_result_id": payment.detection_result_id,
    }


def _payment_from_row(row):
    return Payment.model_validate(
        {
            "payment_id": row["payment_id"],
            "customer_id": row["customer_id"],
            "merchant_id": row["merchant_id"],
            "sender_account_id": row["sender_account_id"],
            "receiver_account_id": row["receiver_account_id"],
            "amount_paise": row["amount_paise"],
            "currency": row["currency"],
            "payment_status": row["payment_status"],
            "risk_status": row["risk_status"],
            "provider_name": row["provider_name"],
            "provider_payment_reference": row["provider_payment_reference"],
            "idempotency_key_digest": row["idempotency_key_digest"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "provider_status_updated_at": row["provider_status_updated_at"],
            "failure_code": row["failure_code"],
            "detection_protocol": row["detection_protocol"],
            "detection_result_id": row["detection_result_id"],
        }
    )


def _timestamp(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Persisted timestamps must be timezone-aware")
    return value.isoformat()


def _validate_scope(scope):
    if not isinstance(scope, str) or not scope or len(scope) > 200:
        raise ValueError("idempotency_scope must be a non-empty string up to 200 chars")


def _validate_digest(value, field_name):
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _translate_sqlite_error(error, operation):
    if isinstance(error, sqlite3.IntegrityError):
        return RepositoryConstraintError(
            f"SQLite constraint rejected operation '{operation}': {error}"
        )
    return RepositoryDatabaseError(
        f"SQLite failed during operation '{operation}': {error}"
    )
