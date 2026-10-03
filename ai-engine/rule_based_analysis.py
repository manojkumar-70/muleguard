"""Compatibility interface for the canonical rule-based account detector."""

from dataclasses import dataclass
import math

from rule_engine import RuleEngineConfig, detect_accounts


REQUIRED_COLUMNS = {"sender", "receiver", "amount_paise", "timestamp", "status"}
SUCCESS_STATUS = "SUCCESS"
VALID_STATUSES = {"SUCCESS", "DECLINED"}


@dataclass(frozen=True)
class RuleConfig:
    """Legacy configuration names mapped onto the canonical rule engine."""

    min_transactions_for_rules: int = 5
    min_unique_incoming_counterparties: int = 6
    min_incoming_transactions_for_imbalance: int = 5
    min_outgoing_transactions_for_imbalance: int = 2
    min_unique_incoming_for_imbalance: int = 3
    incoming_outgoing_ratio_threshold: float = 2.5
    frequency_threshold_per_day: float = 10.0
    short_window_minutes: int = 15
    short_window_concentration_threshold: float = 0.7
    counterparty_breadth_points: int = 25
    flow_imbalance_points: int = 30
    transaction_frequency_points: int = 20
    short_window_concentration_points: int = 30

    def __post_init__(self):
        positive_fields = (
            "min_transactions_for_rules",
            "min_unique_incoming_counterparties",
            "min_incoming_transactions_for_imbalance",
            "min_outgoing_transactions_for_imbalance",
            "min_unique_incoming_for_imbalance",
            "short_window_minutes",
        )
        for field in positive_fields:
            if getattr(self, field) < 1:
                raise ValueError(f"{field} must be at least 1")

        for field in (
            "incoming_outgoing_ratio_threshold",
            "frequency_threshold_per_day",
        ):
            value = getattr(self, field)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be positive and finite")
        if not 0 < self.short_window_concentration_threshold <= 1:
            raise ValueError(
                "short_window_concentration_threshold must be in (0, 1]"
            )
        for field in (
            "counterparty_breadth_points",
            "flow_imbalance_points",
            "transaction_frequency_points",
            "short_window_concentration_points",
        ):
            if not 0 <= getattr(self, field) <= 100:
                raise ValueError(f"{field} must be between 0 and 100")


_LEGACY_RULE_NAMES = {
    "incoming_to_outgoing_movement_imbalance": "incoming_outgoing_flow_imbalance",
    "elevated_activity": "elevated_transaction_frequency",
    "short_window_concentration": "short_window_transaction_concentration",
}


def analyze_accounts(transactions, config=None):
    """Return canonical detections in the established API/dashboard schema.

    Transaction scenario labels and evaluation-only roles are removed before
    calling the detector. The returned indicator explanations and point values
    are those produced by the canonical rule engine.
    """
    engine_config = (
        RuleEngineConfig() if config is None else _canonical_config(config)
    )
    detection_input = transactions.drop(
        columns=["scenario_label", "evaluation_role"], errors="ignore"
    )
    detections = detect_accounts(detection_input, engine_config)

    results = []
    for detection in detections:
        features = detection["features"].copy()
        features.update(
            {
                "transaction_count": features["successful_transaction_count"],
                "incoming_outgoing_ratio": features[
                    "incoming_outgoing_amount_ratio"
                ],
                "transaction_frequency_per_day": features[
                    "transaction_activity_frequency_per_day"
                ],
                "short_window_transaction_concentration": features[
                    "short_window_transaction_share"
                ],
            }
        )
        results.append(
            {
                "account_id": detection["account_id"],
                "risk_indicators": [
                    {
                        **reason,
                        "rule": _LEGACY_RULE_NAMES.get(
                            reason["rule"], reason["rule"]
                        ),
                    }
                    for reason in detection["reasons"]
                ],
                "initial_risk_score": detection["risk_score"],
                "features": features,
            }
        )
    return results


def _canonical_config(config):
    if isinstance(config, RuleEngineConfig):
        return config
    if not isinstance(config, RuleConfig):
        raise TypeError("config must be a RuleConfig or RuleEngineConfig")
    return RuleEngineConfig(
        concentration_window_minutes=config.short_window_minutes,
        concentration_min_transactions=config.min_transactions_for_rules,
        concentration_min_share=config.short_window_concentration_threshold,
        min_transactions_for_rules=config.min_transactions_for_rules,
        activity_threshold_per_day=config.frequency_threshold_per_day,
        movement_min_incoming_transactions=(
            config.min_incoming_transactions_for_imbalance
        ),
        movement_min_outgoing_transactions=(
            config.min_outgoing_transactions_for_imbalance
        ),
        movement_min_incoming_counterparties=(
            config.min_unique_incoming_for_imbalance
        ),
        counterparty_breadth_min_incoming_counterparties=(
            config.min_unique_incoming_counterparties
        ),
        incoming_outgoing_ratio_threshold=(
            config.incoming_outgoing_ratio_threshold
        ),
        concentration_points=config.short_window_concentration_points,
        activity_points=config.transaction_frequency_points,
        movement_points=config.flow_imbalance_points,
        counterparty_breadth_points=config.counterparty_breadth_points,
    )
