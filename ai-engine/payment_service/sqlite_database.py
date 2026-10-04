"""SQLite initialization and connection management for payment persistence."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from payment_service.errors import RepositoryDatabaseError


SCHEMA_VERSION = 1
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / (
    "payment_service.sqlite3"
)
DEFAULT_BUSY_TIMEOUT_MS = 5000

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE synthetic_customers (
        customer_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 120),
        account_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DISABLED')),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE synthetic_merchants (
        merchant_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 120),
        account_id TEXT NOT NULL UNIQUE,
        currency TEXT NOT NULL CHECK(currency = 'INR'),
        status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DISABLED')),
        created_at TEXT NOT NULL,
        provider_merchant_reference TEXT UNIQUE
    )
    """,
    """
    CREATE TABLE payments (
        payment_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL
            REFERENCES synthetic_customers(customer_id),
        merchant_id TEXT NOT NULL
            REFERENCES synthetic_merchants(merchant_id),
        sender_account_id TEXT NOT NULL,
        receiver_account_id TEXT NOT NULL,
        amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
        currency TEXT NOT NULL CHECK(currency = 'INR'),
        payment_status TEXT NOT NULL CHECK(payment_status IN (
            'CREATED', 'PENDING', 'AUTHORIZED', 'CAPTURED',
            'DECLINED', 'FAILED', 'CANCELLED', 'EXPIRED'
        )),
        risk_status TEXT NOT NULL CHECK(risk_status IN (
            'NOT_EVALUATED', 'LOW', 'MEDIUM', 'HIGH', 'ERROR'
        )),
        provider_name TEXT NOT NULL,
        provider_payment_reference TEXT,
        idempotency_key_digest TEXT NOT NULL,
        idempotency_scope TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        provider_status_updated_at TEXT,
        failure_code TEXT,
        detection_protocol TEXT,
        detection_result_id TEXT
            REFERENCES detection_results(detection_result_id)
    )
    """,
    """
    CREATE UNIQUE INDEX payments_idempotency_unique
    ON payments(idempotency_scope, idempotency_key_digest)
    """,
    """
    CREATE INDEX payments_customer_created
    ON payments(customer_id, created_at)
    """,
    """
    CREATE INDEX payments_merchant_created
    ON payments(merchant_id, created_at)
    """,
    """
    CREATE INDEX payments_status_created
    ON payments(payment_status, created_at)
    """,
    """
    CREATE INDEX payments_provider_reference
    ON payments(provider_name, provider_payment_reference)
    """,
    """
    CREATE TABLE provider_events (
        provider_name TEXT NOT NULL,
        provider_event_id TEXT NOT NULL,
        provider_payment_reference TEXT NOT NULL,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        received_at TEXT NOT NULL,
        signature_status TEXT NOT NULL CHECK(signature_status IN (
            'VERIFIED', 'INVALID', 'NOT_APPLICABLE'
        )),
        payload_digest TEXT NOT NULL,
        normalized_status TEXT CHECK(normalized_status IS NULL OR
            normalized_status IN (
                'CREATED', 'PENDING', 'AUTHORIZED', 'CAPTURED',
                'DECLINED', 'FAILED', 'CANCELLED', 'EXPIRED'
            )),
        processing_status TEXT NOT NULL CHECK(processing_status IN (
            'RECEIVED', 'PROCESSED', 'DUPLICATE', 'REJECTED'
        )),
        failure_reason TEXT,
        PRIMARY KEY(provider_name, provider_event_id)
    )
    """,
    """
    CREATE INDEX provider_events_payment_reference
    ON provider_events(provider_name, provider_payment_reference)
    """,
    """
    CREATE TABLE detection_results (
        detection_result_id TEXT PRIMARY KEY,
        payment_id TEXT NOT NULL REFERENCES payments(payment_id),
        transaction_id TEXT NOT NULL UNIQUE,
        protocol TEXT NOT NULL CHECK(protocol = 'stream_frozen_model'),
        risk_score REAL NOT NULL CHECK(risk_score >= 0 AND risk_score <= 100),
        risk_status TEXT NOT NULL CHECK(risk_status IN ('LOW', 'MEDIUM', 'HIGH')),
        signals_json TEXT NOT NULL CHECK(json_valid(signals_json)),
        created_at TEXT NOT NULL,
        disclaimer TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX detection_results_payment_created
    ON detection_results(payment_id, created_at)
    """,
    """
    CREATE TABLE human_reviews (
        review_id TEXT PRIMARY KEY,
        detection_result_id TEXT NOT NULL
            REFERENCES detection_results(detection_result_id),
        reviewer_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN (
            'CONFIRMED_FOR_REVIEW', 'NOT_SUSPICIOUS',
            'NEEDS_MORE_INFORMATION', 'UNRESOLVED'
        )),
        note TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX human_reviews_detection_created
    ON human_reviews(detection_result_id, created_at)
    """,
    """
    CREATE TABLE simulated_interventions (
        intervention_id TEXT PRIMARY KEY,
        payment_id TEXT REFERENCES payments(payment_id),
        account_id TEXT,
        simulation_type TEXT NOT NULL CHECK(simulation_type IN (
            'SIMULATED_HOLD', 'SIMULATED_REVIEW', 'SIMULATED_RELEASE'
        )),
        assumptions_json TEXT NOT NULL CHECK(json_valid(assumptions_json)),
        estimated_impact_json TEXT CHECK(
            estimated_impact_json IS NULL OR json_valid(estimated_impact_json)
        ),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        execution_status TEXT NOT NULL CHECK(execution_status = 'SIMULATED'),
        CHECK(payment_id IS NOT NULL OR account_id IS NOT NULL)
    )
    """,
    """
    CREATE INDEX simulated_interventions_payment_created
    ON simulated_interventions(payment_id, created_at)
    """,
)


class SQLiteDatabase:
    """File-backed SQLite connection manager with repeatable schema setup."""

    def __init__(
        self,
        path=DEFAULT_DATABASE_PATH,
        busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS,
    ):
        if not isinstance(busy_timeout_ms, int) or isinstance(busy_timeout_ms, bool):
            raise ValueError("busy_timeout_ms must be an integer")
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        if str(path) == ":memory:":
            raise ValueError("SQLiteDatabase requires a file-backed database path")
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def connect(self):
        """Return a configured SQLite connection in explicit transaction mode."""
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except sqlite3.Error as error:
            if "connection" in locals():
                connection.close()
            raise RepositoryDatabaseError(
                f"Could not open SQLite database at {self.path}: {error}"
            ) from error

    def initialize(self):
        """Create schema version 1 or reject an unsupported database version."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connection() as connection:
                connection.execute("BEGIN EXCLUSIVE")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise RepositoryDatabaseError(
                        f"Database schema version {version} is newer than "
                        f"supported version {SCHEMA_VERSION}"
                    )
                if version == 0:
                    for statement in _SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version = 1")
                elif version != SCHEMA_VERSION:
                    raise RepositoryDatabaseError(
                        f"No migration path from database schema version {version}"
                    )
                connection.commit()
        except sqlite3.Error as error:
            raise RepositoryDatabaseError(
                f"Could not initialize SQLite database at {self.path}: {error}"
            ) from error

    @contextmanager
    def connection(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
