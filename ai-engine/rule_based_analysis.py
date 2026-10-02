from dataclasses import dataclass

import pandas as pd


REQUIRED_COLUMNS = {"sender", "receiver", "amount_paise", "timestamp", "status"}
SUCCESS_STATUS = "SUCCESS"
VALID_STATUSES = {"SUCCESS", "DECLINED"}


@dataclass(frozen=True)
class RuleConfig:
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
    flow_imbalance_points: int = 25
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

        if self.incoming_outgoing_ratio_threshold <= 0:
            raise ValueError("incoming_outgoing_ratio_threshold must be positive")
        if self.frequency_threshold_per_day <= 0:
            raise ValueError("frequency_threshold_per_day must be positive")
        if not 0 < self.short_window_concentration_threshold <= 1:
            raise ValueError("short_window_concentration_threshold must be in (0, 1]")

        point_fields = (
            "counterparty_breadth_points",
            "flow_imbalance_points",
            "transaction_frequency_points",
            "short_window_concentration_points",
        )
        for field in point_fields:
            if getattr(self, field) < 0:
                raise ValueError(f"{field} must not be negative")


def analyze_accounts(transactions, config=None):
    """Return account features and explainable heuristic indicators.

    Only successful transfers contribute to monetary and behavioural features.
    Scenario labels are intentionally ignored; they are for evaluation only.
    """
    config = config or RuleConfig()
    _validate_transactions(transactions)

    successful = transactions.loc[transactions["status"].eq(SUCCESS_STATUS)].copy()
    successful["amount_paise"] = pd.to_numeric(
        successful["amount_paise"], errors="raise"
    )
    successful["timestamp"] = pd.to_datetime(
        successful["timestamp"], errors="raise", utc=True
    )

    account_ids = sorted(
        set(transactions["sender"].dropna().astype(str))
        | set(transactions["receiver"].dropna().astype(str))
    )
    account_events = {account_id: [] for account_id in account_ids}

    for row in successful.itertuples(index=False):
        amount = int(row.amount_paise)
        timestamp = row.timestamp
        sender = str(row.sender)
        receiver = str(row.receiver)
        account_events[sender].append(
            (timestamp, "outgoing", receiver, amount)
        )
        account_events[receiver].append(
            (timestamp, "incoming", sender, amount)
        )

    results = []
    for account_id in account_ids:
        events = account_events[account_id]
        incoming = [event for event in events if event[1] == "incoming"]
        outgoing = [event for event in events if event[1] == "outgoing"]
        transaction_count = len(events)
        total_incoming = sum(event[3] for event in incoming)
        total_outgoing = sum(event[3] for event in outgoing)
        incoming_counterparties = {event[2] for event in incoming}
        outgoing_counterparties = {event[2] for event in outgoing}
        ratio = (
            total_incoming / total_outgoing if total_outgoing > 0 else None
        )
        frequency = _transactions_per_day(events)
        concentration = _short_window_concentration(
            events, config.short_window_minutes
        )

        features = {
            "transaction_count": transaction_count,
            "incoming_transaction_count": len(incoming),
            "outgoing_transaction_count": len(outgoing),
            "total_incoming_amount_paise": total_incoming,
            "total_outgoing_amount_paise": total_outgoing,
            "unique_incoming_counterparties": len(incoming_counterparties),
            "unique_outgoing_counterparties": len(outgoing_counterparties),
            "incoming_outgoing_ratio": ratio,
            "transaction_frequency_per_day": frequency,
            "short_window_transaction_concentration": concentration,
        }
        indicators = _evaluate_rules(features, config)
        risk_score = min(100, sum(item["points"] for item in indicators))
        results.append(
            {
                "account_id": account_id,
                "risk_indicators": indicators,
                "initial_risk_score": risk_score,
                "features": features,
            }
        )
    return results


def _validate_transactions(transactions):
    missing_columns = sorted(REQUIRED_COLUMNS - set(transactions.columns))
    if missing_columns:
        raise ValueError(
            "Transactions are missing required columns: "
            + ", ".join(missing_columns)
        )
    if transactions.empty:
        return

    for column in ("sender", "receiver", "status"):
        missing = transactions[column].isna() | transactions[column].astype(
            "string"
        ).str.strip().eq("")
        if missing.any():
            raise ValueError(f"Transactions contain missing values in '{column}'")

    invalid_statuses = ~transactions["status"].isin(VALID_STATUSES)
    if invalid_statuses.any():
        values = sorted(transactions.loc[invalid_statuses, "status"].unique().tolist())
        raise ValueError(f"Invalid transaction status value(s): {values}")

    amounts = pd.to_numeric(transactions["amount_paise"], errors="coerce")
    if (
        amounts.isna().any()
        or amounts.isin([float("inf"), float("-inf")]).any()
        or amounts.le(0).any()
        or amounts.mod(1).ne(0).any()
    ):
        raise ValueError(
            "amount_paise must contain positive finite whole-number paise values"
        )

    timestamps = pd.to_datetime(transactions["timestamp"], errors="coerce", utc=True)
    if timestamps.isna().any():
        raise ValueError("timestamp must contain valid date/time values")


def _transactions_per_day(events):
    if not events:
        return 0.0
    timestamps = [event[0] for event in events]
    span_days = (max(timestamps) - min(timestamps)).total_seconds() / 86_400
    return len(events) / max(span_days, 1.0)


def _short_window_concentration(events, window_minutes):
    if not events:
        return 0.0
    timestamps = sorted(event[0] for event in events)
    window = pd.Timedelta(minutes=window_minutes)
    left = 0
    max_count = 0
    for right, timestamp in enumerate(timestamps):
        while timestamp - timestamps[left] > window:
            left += 1
        max_count = max(max_count, right - left + 1)
    return max_count / len(timestamps)


def _evaluate_rules(features, config):
    indicators = []
    count = features["transaction_count"]
    incoming_count = features["unique_incoming_counterparties"]
    incoming_outgoing_ratio = features["incoming_outgoing_ratio"]

    if (
        count >= config.min_transactions_for_rules
        and incoming_count >= config.min_unique_incoming_counterparties
    ):
        indicators.append(
            {
                "rule": "diverse_incoming_counterparties",
                "explanation": (
                    f"Observed {incoming_count} unique incoming counterparties "
                    f"across {count} successful transactions; configured breadth "
                    f"threshold is {config.min_unique_incoming_counterparties}."
                ),
                "points": config.counterparty_breadth_points,
            }
        )

    if (
        incoming_outgoing_ratio is not None
        and features["unique_incoming_counterparties"]
        >= config.min_unique_incoming_for_imbalance
        and count >= config.min_transactions_for_rules
        and incoming_outgoing_ratio >= config.incoming_outgoing_ratio_threshold
        and _direction_count(features, "incoming")
        >= config.min_incoming_transactions_for_imbalance
        and _direction_count(features, "outgoing")
        >= config.min_outgoing_transactions_for_imbalance
    ):
        indicators.append(
            {
                "rule": "incoming_outgoing_flow_imbalance",
                "explanation": (
                    f"Incoming value is {incoming_outgoing_ratio:.2f} times "
                    "outgoing value, with activity in both directions and "
                    "multiple incoming counterparties."
                ),
                "points": config.flow_imbalance_points,
            }
        )

    frequency = features["transaction_frequency_per_day"]
    if (
        count >= config.min_transactions_for_rules
        and frequency >= config.frequency_threshold_per_day
    ):
        indicators.append(
            {
                "rule": "elevated_transaction_frequency",
                "explanation": (
                    f"Observed {frequency:.2f} successful transactions per day; "
                    f"configured threshold is {config.frequency_threshold_per_day:.2f}."
                ),
                "points": config.transaction_frequency_points,
            }
        )

    concentration = features["short_window_transaction_concentration"]
    if (
        count >= config.min_transactions_for_rules
        and concentration >= config.short_window_concentration_threshold
    ):
        indicators.append(
            {
                "rule": "short_window_transaction_concentration",
                "explanation": (
                    f"{concentration:.0%} of successful transactions occurred "
                    f"within one {config.short_window_minutes}-minute window; "
                    f"configured threshold is "
                    f"{config.short_window_concentration_threshold:.0%}."
                ),
                "points": config.short_window_concentration_points,
            }
        )
    return indicators


def _direction_count(features, direction):
    return features[f"{direction}_transaction_count"]
