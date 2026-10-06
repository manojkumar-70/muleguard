"""Stateful bridge from normalized payment events to streaming detection."""

from datetime import datetime, timezone

import pandas as pd

import synthetic_streaming
from anomaly_detection import FEATURE_COLUMNS
from payment_service.schemas import NormalizedTransactionEvent


_DETECTOR_COLUMNS = (
    "transaction_id",
    "sender",
    "receiver",
    "amount_paise",
    "timestamp",
    "status",
)


class StreamingDetectionSession:
    """Buffer normalized events, warm one model, and score causal prefixes."""

    def __init__(self, warmup_events: int = 50, seed: int = 42):
        if not isinstance(warmup_events, int) or isinstance(warmup_events, bool):
            raise ValueError("warmup_events must be an integer")
        if warmup_events < 1:
            raise ValueError("warmup_events must be at least 1")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("seed must be an integer")

        self._warmup_events = warmup_events
        self._seed = seed
        self._buffer: list[tuple[datetime, int, dict]] = []
        self._transaction_ids: set[str] = set()
        self._arrival_sequence = 0
        self._model = None
        self._training_account_count = 0
        self._accounts: dict[str, dict] = {}

    @property
    def warmup_state(self) -> dict:
        """Return the current warm-up progress without exposing buffered rows."""
        observed = min(len(self._buffer), self._warmup_events)
        return {
            "phase": "ready" if self._model is not None else "warming_up",
            "required_events": self._warmup_events,
            "observed_events": observed,
            "remaining_events": max(self._warmup_events - observed, 0),
            "complete": self._model is not None,
            "model_frozen": self._model is not None,
            "training_account_count": self._training_account_count,
        }

    @property
    def accounts(self) -> dict[str, dict]:
        """Return a snapshot of accumulated account-level scores and alerts."""
        return {
            account_id: {
                **state,
                "risk_score_progression": list(state["risk_score_progression"]),
            }
            for account_id, state in self._accounts.items()
        }

    def process_event(
        self,
        event: NormalizedTransactionEvent,
    ) -> dict:
        """Validate and process one event, returning warm-up or risk metadata."""
        normalized = NormalizedTransactionEvent.model_validate(event)
        if normalized.transaction_id in self._transaction_ids:
            raise ValueError(
                f"Duplicate transaction_id: {normalized.transaction_id}"
            )

        if self._model is not None and self._buffer:
            latest_timestamp = self._buffer[-1][0]
            if normalized.timestamp < latest_timestamp:
                raise ValueError(
                    "Event timestamp cannot precede the latest timestamp after "
                    "the streaming model is frozen."
                )

        row = {
            "transaction_id": normalized.transaction_id,
            "sender": normalized.sender,
            "receiver": normalized.receiver,
            "amount_paise": normalized.amount_paise,
            "timestamp": normalized.timestamp,
            "status": normalized.status,
        }
        entry = (normalized.timestamp, self._arrival_sequence, row)
        candidate = sorted(
            [*self._buffer, entry],
            key=lambda item: (item[0], item[1]),
        )

        if self._model is None:
            if len(candidate) < self._warmup_events:
                self._commit_buffer(candidate, normalized.transaction_id)
                return self._warmup_result(normalized)

            account_ids = {
                account_id
                for _, _, transaction in candidate
                for account_id in (transaction["sender"], transaction["receiver"])
            }
            if len(account_ids) < 2:
                raise ValueError(
                    "Warm-up must observe at least two distinct accounts to train "
                    "IsolationForest."
                )

            training_frame = _detector_frame(candidate)
            model, training_features = synthetic_streaming._fit_stream_model(
                training_frame,
                self._seed,
            )
            self._model = model
            self._training_account_count = len(training_features)
            self._commit_buffer(candidate, normalized.transaction_id)
            return self._warmup_result(normalized)

        result, updated_accounts = self._score_event(normalized, candidate)
        self._commit_buffer(candidate, normalized.transaction_id)
        self._accounts = updated_accounts
        return result

    def _commit_buffer(
        self,
        candidate: list[tuple[datetime, int, dict]],
        transaction_id: str,
    ):
        self._buffer = candidate
        self._transaction_ids.add(transaction_id)
        self._arrival_sequence += 1

    def _warmup_result(
        self,
        event: NormalizedTransactionEvent,
    ) -> dict:
        return {
            "protocol": synthetic_streaming.PROTOCOL,
            "transaction_id": event.transaction_id,
            "timestamp": _utc_isoformat(event.timestamp),
            "phase": "warmup",
            "detection_enabled": False,
            "risk_scores": {},
            "new_alerts": [],
            "warmup_state": self.warmup_state,
        }

    def _score_event(
        self,
        event: NormalizedTransactionEvent,
        candidate: list[tuple[datetime, int, dict]],
    ) -> tuple[dict, dict[str, dict]]:
        transactions = _detector_frame(candidate)
        prefix_features = synthetic_streaming._build_incremental_features(
            transactions
        )
        current_features = prefix_features.loc[:, FEATURE_COLUMNS]
        predictions = self._model.predict(current_features)
        anomaly_scores = -self._model.decision_function(current_features)
        ml_results = prefix_features.copy()
        ml_results["prediction"] = [
            "ANOMALY" if prediction == -1 else "TYPICAL"
            for prediction in predictions
        ]
        ml_results["is_anomaly"] = predictions == -1
        ml_results["anomaly_score"] = anomaly_scores
        ml_records = ml_results.to_dict(orient="records")

        rule_results = synthetic_streaming.analyze_accounts(transactions)
        graph_results = synthetic_streaming.analyze_transaction_graph(transactions)
        risk_results = synthetic_streaming.aggregate_risk(
            rule_results,
            ml_records,
            graph_results,
        )
        risk_by_account = {
            account["account_id"]: account
            for account in risk_results["accounts"]
        }
        rule_by_account = {
            account["account_id"]: account for account in rule_results
        }
        graph_by_account = {
            account["account_id"]: account
            for account in graph_results["accounts"]
        }
        ml_by_account = {
            account["account_id"]: account for account in ml_records
        }

        updated_accounts = {
            account_id: {
                **state,
                "risk_score_progression": list(state["risk_score_progression"]),
            }
            for account_id, state in self._accounts.items()
        }
        timestamp = _utc_isoformat(event.timestamp)
        transaction_count = len(candidate)
        new_alerts = []
        for account_id, risk in risk_by_account.items():
            state = updated_accounts.setdefault(
                account_id,
                {
                    "protocol": synthetic_streaming.PROTOCOL,
                    "first_alert_timestamp": None,
                    "transaction_count_at_first_alert": None,
                    "risk_score_progression": [],
                    "first_alert_explanation": None,
                },
            )
            state["risk_score_progression"].append(
                {
                    "protocol": synthetic_streaming.PROTOCOL,
                    "timestamp": timestamp,
                    "transaction_count": transaction_count,
                    "risk_score": float(risk["risk_score"]),
                    "risk_level": risk["risk_level"],
                }
            )
            if (
                state["first_alert_timestamp"] is None
                and risk["risk_level"] in {"MEDIUM", "HIGH"}
            ):
                explanation = synthetic_streaming._explanation(
                    account_id,
                    risk,
                    rule_by_account[account_id],
                    graph_by_account[account_id],
                    ml_by_account[account_id],
                )
                state["first_alert_timestamp"] = timestamp
                state["transaction_count_at_first_alert"] = transaction_count
                state["first_alert_explanation"] = explanation
                new_alerts.append(
                    {
                        "protocol": synthetic_streaming.PROTOCOL,
                        "account_id": account_id,
                        "risk_score": float(risk["risk_score"]),
                        "risk_level": risk["risk_level"],
                        "explanation": explanation,
                    }
                )

        result = {
            "protocol": synthetic_streaming.PROTOCOL,
            "transaction_id": event.transaction_id,
            "timestamp": timestamp,
            "phase": "scored",
            "detection_enabled": True,
            "risk_scores": {
                account_id: float(risk["risk_score"])
                for account_id, risk in risk_by_account.items()
            },
            "new_alerts": new_alerts,
            "warmup_state": self.warmup_state,
        }
        return result, updated_accounts


def _detector_frame(
    entries: list[tuple[datetime, int, dict]],
) -> pd.DataFrame:
    """Build a strict allowlisted transaction table in causal time order."""
    return pd.DataFrame(
        [transaction for _, _, transaction in entries],
        columns=_DETECTOR_COLUMNS,
    )


def _utc_isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()
