"""Fail-closed configuration for the local synthetic payment API."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal

from payment_service.sqlite_database import DEFAULT_DATABASE_PATH


@dataclass(frozen=True)
class PaymentAPISettings:
    """Local API settings; provider selection is intentionally not configurable."""

    enabled: bool = False
    database_path: Path = DEFAULT_DATABASE_PATH
    repository_backend: Literal["sqlite", "mongodb"] = "sqlite"

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
        repository_backend = os.environ.get(
            "MULEGUARD_PAYMENT_REPOSITORY", "sqlite"
        ).strip().lower()
        if repository_backend not in {"sqlite", "mongodb"}:
            raise ValueError(
                "MULEGUARD_PAYMENT_REPOSITORY must be sqlite or mongodb"
            )
        if repository_backend == "mongodb":
            missing = [
                name
                for name in ("MONGODB_URI", "MONGODB_DATABASE")
                if not os.environ.get(name, "").strip()
            ]
            if missing:
                raise ValueError(
                    "MongoDB payment repository requires environment variable(s): "
                    + ", ".join(missing)
                )
        return cls(
            enabled=enabled_value == "true",
            database_path=database_path,
            repository_backend=repository_backend,
        )
