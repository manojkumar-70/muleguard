"""Synchronous MongoDB implementation of the payment repository contract."""

from enum import Enum
import os
import re
from datetime import timezone

from pymongo import ASCENDING, MongoClient
from pymongo.errors import (
    CollectionInvalid,
    DuplicateKeyError,
    PyMongoError,
)

from payment_service.errors import (
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


_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_COLLECTIONS = (
    "synthetic_customers",
    "synthetic_merchants",
    "payments",
    "provider_events",
    "detection_results",
    "human_reviews",
    "simulated_interventions",
)


class MongoPaymentRepository(PaymentRepository):
    """Persist validated synthetic payment records in MongoDB."""

    def __init__(
        self,
        uri: str | None = None,
        database_name: str | None = None,
        *,
        client=None,
        database=None,
    ):
        self._client = client
        if database is None:
            uri = uri or os.getenv("MONGODB_URI")
            database_name = database_name or os.getenv("MONGODB_DATABASE")
            if not uri or not database_name:
                raise ValueError(
                    "MONGODB_URI and MONGODB_DATABASE must be configured."
                )
            try:
                self._client = self._client or MongoClient(
                    uri,
                    tz_aware=True,
                    tzinfo=timezone.utc,
                )
            except PyMongoError as error:
                raise _database_error(error, "create MongoDB client") from None
            database = self._client[database_name]
        elif self._client is None:
            self._client = getattr(database, "client", None)

        self._database = database
        self._initialize()

    def save_customer(self, customer: SyntheticCustomer) -> None:
        customer = SyntheticCustomer.model_validate(customer)
        self._insert_document(
            "synthetic_customers",
            _model_document(customer),
            "save customer",
        )

    def get_customer(self, customer_id: str) -> SyntheticCustomer | None:
        try:
            document = self._database["synthetic_customers"].find_one(
                {"customer_id": customer_id}
            )
        except PyMongoError as error:
            raise _database_error(error, f"retrieve customer {customer_id}") from error
        return _model_from_document(SyntheticCustomer, document)

    def save_merchant(self, merchant: SyntheticMerchant) -> None:
        merchant = SyntheticMerchant.model_validate(merchant)
        self._insert_document(
            "synthetic_merchants",
            _model_document(merchant),
            "save merchant",
        )

    def get_merchant(self, merchant_id: str) -> SyntheticMerchant | None:
        try:
            document = self._database["synthetic_merchants"].find_one(
                {"merchant_id": merchant_id}
            )
        except PyMongoError as error:
            raise _database_error(error, f"retrieve merchant {merchant_id}") from error
        return _model_from_document(SyntheticMerchant, document)

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

        idempotency_filter = {
            "idempotency_scope": idempotency_scope,
            "idempotency_key_digest": idempotency_key_digest,
        }
        payments = self._database["payments"]
        try:
            existing = payments.find_one(idempotency_filter)
            if existing is not None:
                return _existing_payment(existing, request_digest), False

            self._validate_payment_accounts(payment)
            self._validate_payment_detection_reference(payment)
            document = _model_document(payment)
            document.update(
                {
                    "idempotency_scope": idempotency_scope,
                    "request_digest": request_digest,
                }
            )
            payments.insert_one(document)
            return payment, True
        except DuplicateKeyError as error:
            try:
                existing = payments.find_one(idempotency_filter)
            except PyMongoError as lookup_error:
                raise _database_error(
                    lookup_error, "resolve payment idempotency conflict"
                ) from lookup_error
            if existing is not None:
                return _existing_payment(existing, request_digest), False
            raise RepositoryConstraintError(
                f"MongoDB constraint rejected operation 'create payment': {error}"
            ) from error
        except PyMongoError as error:
            raise _database_error(error, "create payment") from error

    def get_payment(self, payment_id: str) -> Payment | None:
        try:
            document = self._database["payments"].find_one(
                {"payment_id": payment_id}
            )
        except PyMongoError as error:
            raise _database_error(error, f"retrieve payment {payment_id}") from error
        return _model_from_document(Payment, document)

    def update_payment(self, payment: Payment) -> None:
        payment = Payment.model_validate(payment)
        try:
            self._update_payment_document(payment)
        except PyMongoError as error:
            raise _database_error(error, "update payment") from error

    def record_provider_event_once(self, event: ProviderEvent) -> bool:
        event = ProviderEvent.model_validate(event)
        try:
            return self._record_provider_event(event, mark_processed=False)
        except DuplicateKeyError as error:
            return self._resolve_duplicate_provider_event(event, error)
        except PyMongoError as error:
            raise _database_error(error, "record provider event") from error

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
        if self._client is None:
            raise RepositoryDatabaseError(
                "MongoDB transactions require a client with session support."
            )

        def process_in_transaction(session):
            inserted = self._record_provider_event(
                event,
                mark_processed=True,
                session=session,
            )
            if not inserted:
                return False

            current = self._database["payments"].find_one(
                {"payment_id": payment.payment_id},
                session=session,
            )
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
            self._update_payment_document(payment, session=session)
            return True

        try:
            with self._client.start_session() as session:
                return session.with_transaction(process_in_transaction)
        except DuplicateKeyError as error:
            return self._resolve_duplicate_provider_event(event, error)
        except PyMongoError as error:
            raise _database_error(
                error, "process provider event and update payment"
            ) from error

    def save_detection_result(self, result: DetectionResult) -> None:
        result = DetectionResult.model_validate(result)
        try:
            if self._database["payments"].find_one(
                {"payment_id": result.payment_id}
            ) is None:
                raise RepositoryConstraintError(
                    "Detection result must reference an existing payment."
                )
            self._insert_document(
                "detection_results",
                _model_document(result),
                "save detection result",
            )
        except PyMongoError as error:
            raise _database_error(error, "save detection result") from error

    def save_human_review(self, review: HumanReview) -> None:
        review = HumanReview.model_validate(review)
        try:
            if self._database["detection_results"].find_one(
                {"detection_result_id": review.detection_result_id}
            ) is None:
                raise RepositoryConstraintError(
                    "Human review must reference an existing detection result."
                )
            self._insert_document(
                "human_reviews",
                _model_document(review),
                "save human review",
            )
        except PyMongoError as error:
            raise _database_error(error, "save human review") from error

    def save_simulated_intervention(
        self,
        intervention: SimulatedIntervention,
    ) -> None:
        intervention = SimulatedIntervention.model_validate(intervention)
        try:
            if (
                intervention.payment_id is not None
                and self._database["payments"].find_one(
                    {"payment_id": intervention.payment_id}
                )
                is None
            ):
                raise RepositoryConstraintError(
                    "Intervention must reference an existing payment."
                )
            self._insert_document(
                "simulated_interventions",
                _model_document(intervention),
                "save simulated intervention",
            )
        except PyMongoError as error:
            raise _database_error(error, "save simulated intervention") from error

    def _initialize(self) -> None:
        try:
            existing = set(self._database.list_collection_names())
            for name in _COLLECTIONS:
                if name in existing:
                    continue
                try:
                    self._database.create_collection(name)
                except CollectionInvalid:
                    if name not in self._database.list_collection_names():
                        raise

            indexes = {
                "synthetic_customers": (
                    ((("customer_id", ASCENDING),), {"unique": True}),
                    ((("account_id", ASCENDING),), {"unique": True}),
                ),
                "synthetic_merchants": (
                    ((("merchant_id", ASCENDING),), {"unique": True}),
                    ((("account_id", ASCENDING),), {"unique": True}),
                    (
                        (("provider_merchant_reference", ASCENDING),),
                        {
                            "unique": True,
                            "partialFilterExpression": {
                                "provider_merchant_reference": {"$type": "string"}
                            },
                        },
                    ),
                ),
                "payments": (
                    ((("payment_id", ASCENDING),), {"unique": True}),
                    (
                        (
                            ("idempotency_scope", ASCENDING),
                            ("idempotency_key_digest", ASCENDING),
                        ),
                        {"unique": True},
                    ),
                ),
                "provider_events": (
                    (
                        (
                            ("provider_name", ASCENDING),
                            ("provider_event_id", ASCENDING),
                        ),
                        {"unique": True},
                    ),
                ),
                "detection_results": (
                    ((("detection_result_id", ASCENDING),), {"unique": True}),
                    ((("transaction_id", ASCENDING),), {"unique": True}),
                ),
                "human_reviews": (
                    ((("review_id", ASCENDING),), {"unique": True}),
                ),
                "simulated_interventions": (
                    ((("intervention_id", ASCENDING),), {"unique": True}),
                ),
            }
            for collection_name, collection_indexes in indexes.items():
                collection = self._database[collection_name]
                for keys, options in collection_indexes:
                    collection.create_index(list(keys), **options)
        except PyMongoError as error:
            raise _database_error(error, "initialize payment collections") from error

    def _insert_document(
        self,
        collection_name: str,
        document: dict,
        operation: str,
    ) -> None:
        try:
            self._database[collection_name].insert_one(document)
        except DuplicateKeyError as error:
            raise RepositoryConstraintError(
                f"MongoDB constraint rejected operation '{operation}': {error}"
            ) from error
        except PyMongoError as error:
            raise _database_error(error, operation) from error

    def _validate_payment_accounts(self, payment: Payment) -> None:
        customer = self._database["synthetic_customers"].find_one(
            {"customer_id": payment.customer_id}
        )
        merchant = self._database["synthetic_merchants"].find_one(
            {"merchant_id": payment.merchant_id}
        )
        if customer is None or merchant is None:
            raise RepositoryConstraintError(
                "Payment must reference an existing synthetic customer and merchant."
            )
        if customer["account_id"] != payment.sender_account_id:
            raise RepositoryConstraintError(
                "Payment sender_account_id must match its synthetic customer."
            )
        if merchant["account_id"] != payment.receiver_account_id:
            raise RepositoryConstraintError(
                "Payment receiver_account_id must match its synthetic merchant."
            )

    def _validate_payment_detection_reference(
        self,
        payment: Payment,
        session=None,
    ) -> None:
        if (
            payment.detection_result_id is not None
            and self._database["detection_results"].find_one(
                {"detection_result_id": payment.detection_result_id},
                session=session,
            )
            is None
        ):
            raise RepositoryConstraintError(
                "Payment must reference an existing detection result."
            )

    def _update_payment_document(self, payment: Payment, session=None) -> None:
        payments = self._database["payments"]
        current = payments.find_one(
            {"payment_id": payment.payment_id},
            session=session,
        )
        if current is None:
            raise PaymentNotFoundError(f"Payment not found: {payment.payment_id}")

        immutable_fields = (
            "customer_id",
            "merchant_id",
            "sender_account_id",
            "receiver_account_id",
            "amount_paise",
            "currency",
            "provider_name",
            "idempotency_key_digest",
        )
        for field_name in immutable_fields:
            if current[field_name] != getattr(payment, field_name):
                raise ValueError(f"Payment field '{field_name}' is immutable")
        if not _same_bson_datetime(current["created_at"], payment.created_at):
            raise ValueError("Payment field 'created_at' is immutable")

        old_status = PaymentStatus(current["payment_status"])
        if old_status != payment.payment_status:
            try:
                validate_payment_transition(old_status, payment.payment_status)
            except ValueError as error:
                raise InvalidPaymentTransitionError(str(error)) from error
        self._validate_payment_detection_reference(payment, session=session)

        updates = {
            field_name: getattr(payment, field_name)
            for field_name in (
                "payment_status",
                "risk_status",
                "provider_payment_reference",
                "updated_at",
                "provider_status_updated_at",
                "failure_code",
                "detection_protocol",
                "detection_result_id",
            )
        }
        payments.update_one(
            {"payment_id": payment.payment_id},
            {"$set": _mongo_safe(updates)},
            session=session,
        )

    def _record_provider_event(
        self,
        event: ProviderEvent,
        mark_processed: bool,
        session=None,
    ) -> bool:
        collection = self._database["provider_events"]
        event_filter = {
            "provider_name": event.provider_name,
            "provider_event_id": event.provider_event_id,
        }
        existing = collection.find_one(event_filter, session=session)
        if existing is not None:
            if existing["payload_digest"] != event.payload_digest:
                raise ProviderEventConflictError(
                    "Provider event ID was reused with a different payload digest."
                )
            return False

        document = _model_document(event)
        if mark_processed:
            document["processing_status"] = ProviderEventProcessingStatus.PROCESSED
        collection.insert_one(document, session=session)
        return True

    def _resolve_duplicate_provider_event(
        self,
        event: ProviderEvent,
        error: DuplicateKeyError,
    ) -> bool:
        try:
            existing = self._database["provider_events"].find_one(
                {
                    "provider_name": event.provider_name,
                    "provider_event_id": event.provider_event_id,
                }
            )
        except PyMongoError as lookup_error:
            raise _database_error(
                lookup_error, "resolve provider event duplicate"
            ) from lookup_error
        if existing is None:
            raise RepositoryConstraintError(
                "MongoDB constraint rejected operation 'record provider event': "
                f"{error}"
            ) from error
        if existing["payload_digest"] != event.payload_digest:
            raise ProviderEventConflictError(
                "Provider event ID was reused with a different payload digest."
            ) from error
        return False


def _model_document(model) -> dict:
    return _mongo_safe(model.model_dump(mode="python"))


def _model_from_document(model_type, document):
    if document is None:
        return None
    return model_type.model_validate(
        {
            field_name: document[field_name]
            for field_name in model_type.model_fields
            if field_name in document
        }
    )


def _mongo_safe(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _mongo_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mongo_safe(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_mongo_safe(item) for item in value)
    return value


def _existing_payment(document: dict, request_digest: str) -> Payment:
    if document["request_digest"] != request_digest:
        raise IdempotencyConflictError(
            "Idempotency key was reused with a different request."
        )
    return _model_from_document(Payment, document)


def _same_bson_datetime(stored, requested) -> bool:
    if stored.tzinfo is None or stored.utcoffset() is None:
        raise ValueError("Persisted timestamps must be timezone-aware")
    if requested.tzinfo is None or requested.utcoffset() is None:
        raise ValueError("Persisted timestamps must be timezone-aware")
    return int(stored.timestamp() * 1000) == int(requested.timestamp() * 1000)


def _validate_scope(scope: str) -> None:
    if not isinstance(scope, str) or not scope or len(scope) > 200:
        raise ValueError("idempotency_scope must be a non-empty string up to 200 chars")


def _validate_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _database_error(error: PyMongoError, operation: str) -> RepositoryDatabaseError:
    return RepositoryDatabaseError(
        f"MongoDB failed during operation '{operation}' "
        f"(error_type={type(error).__name__})."
    )
