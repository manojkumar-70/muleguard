"""Typed failures raised by payment persistence implementations."""


class RepositoryError(Exception):
    """Base class for payment repository failures."""


class RepositoryDatabaseError(RepositoryError):
    """SQLite could not complete a persistence operation."""


class RepositoryConstraintError(RepositoryError):
    """A database integrity constraint rejected a persistence operation."""


class PaymentNotFoundError(RepositoryError):
    """A referenced payment does not exist."""


class DetectionResultNotFoundError(RepositoryError):
    """A referenced detection result does not exist."""


class IdempotencyConflictError(RepositoryError):
    """An idempotency key was reused with a different request digest."""


class ProviderEventConflictError(RepositoryError):
    """A provider event ID was reused with a different payload digest."""


class InvalidPaymentTransitionError(RepositoryError):
    """A requested payment lifecycle transition is not allowed."""
