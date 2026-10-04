"""Fail-closed configuration for the local synthetic payment API."""

from dataclasses import dataclass
import os
from pathlib import Path

from payment_service.sqlite_database import DEFAULT_DATABASE_PATH


@dataclass(frozen=True)
class PaymentAPISettings:
    """Local API settings; provider selection is intentionally not configurable."""

    enabled: bool = False
    database_path: Path = DEFAULT_DATABASE_PATH

    @classmethod
    def from_environment(cls) -> "PaymentAPISettings":
        enabled_value = os.environ.get(
            "MULEGUARD_PAYMENT_API_ENABLED", "false"
        ).strip().lower()
        if enabled_value not in {"true", "false"}:
            raise ValueError(
                "MULEGUARD_PAYMENT_API_ENABLED must be true or false"
            )
        database_value = os.environ.get(
            "MULEGUARD_PAYMENT_API_DATABASE_PATH"
        )
        database_path = (
            Path(database_value).expanduser()
            if database_value
            else DEFAULT_DATABASE_PATH
        )
        return cls(
            enabled=enabled_value == "true",
            database_path=database_path,
        )
