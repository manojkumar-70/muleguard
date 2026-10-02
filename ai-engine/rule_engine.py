"""Explainable rule-based analysis for synthetic transaction activity."""

from dataclasses import dataclass
import math
from pathlib import Path

import pandas as pd

from data_loader import DEFAULT_DATASET_PATH, load_transactions


REQUIRED_COLUMNS = {"sender", "receiver", "amount_paise", "timestamp", "status"}
SUCCESS_STATUS = "SUCCESS"
EVALUATION_ROLES = {
    "FOCAL_SUSPICIOUS",
    "SOURCE_PARTICIPANT",
    "DESTINATION_PARTICIPANT",
    "NORMAL",
}
DEFAULT_ROLE_MANIFEST_PATH = DEFAULT_DATASET_PATH.with_name(
    "evaluation_account_roles.csv"
)


@dataclass(frozen=True)
class RuleEngineConfig:
    concentration_window_minutes: int = 15
    concentration_min_transactions: int = 5
    concentration_min_share: float = 0.7
    min_transactions_for_rules: int = 5
    activity_threshold_per_day: float = 10.0
    movement_min_incoming_transactions: int = 5
    movement_min_outgoing_transactions: int = 2
    movement_min_incoming_counterparties: int = 3
    incoming_outgoing_ratio_threshold: float = 2.5
    concentration_points: int = 30
    activity_points: int = 20
    movement_points: int = 30
    medium_risk_threshold: int = 30
    high_risk_threshold: int = 65

    def __post_init__(self):
        positive_integer_fields = (
            "concentration_window_minutes",
            "concentration_min_transactions",
            "min_transactions_for_rules",
            "movement_min_incoming_transactions",
            "movement_min_outgoing_transactions",
            "movement_min_incoming_counterparties",
        )
        for field in positive_integer_fields:
            if getattr(self, field) < 1:
                raise ValueError(f"{field} must be at least 1")

        if not 0 < self.concentration_min_share <= 1:
            raise ValueError("concentration_min_share must be in (0, 1]")
        if not math.isfinite(self.activity_threshold_per_day) or (
            self.activity_threshold_per_day <= 0
        ):
            raise ValueError("activity_threshold_per_day must be positive and finite")
        if not math.isfinite(self.incoming_outgoing_ratio_threshold) or (
            self.incoming_outgoing_ratio_threshold <= 0
        ):
            raise ValueError(
                "incoming_outgoing_ratio_threshold must be positive and finite"
            )
        for field in ("concentration_points", "activity_points", "movement_points"):
            if not 0 <= getattr(self, field) <= 100:
                raise ValueError(f"{field} must be between 0 and 100")
        if not 1 <= self.medium_risk_threshold < self.high_risk_threshold <= 100:
            raise ValueError(
                "Risk thresholds must satisfy "
                "1 <= medium_risk_threshold < high_risk_threshold <= 100"
            )


def detect_accounts(transactions, config=None):
    """Calculate account features and rule indicators without reading labels."""
    config = config or RuleEngineConfig()
    _validate_transactions(transactions)
    if transactions.empty:
        raise ValueError("Cannot analyze accounts: transaction dataset is empty")

    successful = transactions.loc[transactions["status"].eq(SUCCESS_STATUS)].copy()
    successful["amount_paise"] = pd.to_numeric(
        successful["amount_paise"], errors="coerce"
    )
    successful["timestamp"] = pd.to_datetime(
        successful["timestamp"], errors="coerce", utc=True
    )
    period_days = 1.0
    if not successful.empty:
        period_days = max(
            (
                successful["timestamp"].max() - successful["timestamp"].min()
            ).total_seconds()
            / 86_400,
            1.0,
        )

    account_ids = sorted(
        set(transactions["sender"].astype(str))
        | set(transactions["receiver"].astype(str))
    )
    account_events = {account_id: [] for account_id in account_ids}
    for row in successful.itertuples(index=False):
        sender = str(row.sender)
        receiver = str(row.receiver)
        timestamp = row.timestamp
        amount = int(row.amount_paise)
        account_events.setdefault(sender, []).append(
            ("outgoing", receiver, amount, timestamp)
        )
        account_events.setdefault(receiver, []).append(
            ("incoming", sender, amount, timestamp)
        )

    results = []
    for account_id in sorted(account_events):
        events = account_events[account_id]
        incoming = [event for event in events if event[0] == "incoming"]
        outgoing = [event for event in events if event[0] == "outgoing"]
        incoming_total = sum(event[2] for event in incoming)
        outgoing_total = sum(event[2] for event in outgoing)
        account_timestamps = sorted(event[3] for event in events)
        max_window_count = _max_window_count(
            account_timestamps, config.concentration_window_minutes
        )
        concentration_share = max_window_count / len(events) if events else 0.0
        transaction_frequency = len(events) / period_days
        amount_ratio = incoming_total / outgoing_total if outgoing_total else None

        features = {
            "incoming_transaction_count": len(incoming),
            "outgoing_transaction_count": len(outgoing),
            "total_incoming_amount_paise": incoming_total,
            "total_outgoing_amount_paise": outgoing_total,
            "unique_incoming_counterparties": len(
                {event[1] for event in incoming}
            ),
            "unique_outgoing_counterparties": len(
                {event[1] for event in outgoing}
            ),
            "transaction_activity_frequency_per_day": transaction_frequency,
            "incoming_outgoing_amount_ratio": amount_ratio,
            "short_window_transaction_count": max_window_count,
            "short_window_transaction_share": concentration_share,
            "successful_transaction_count": len(events),
        }
        reasons = _evaluate_rules(features, config)
        score = min(100, sum(reason["points"] for reason in reasons))
        results.append(
            {
                "account_id": account_id,
                "risk_score": score,
                "risk_category": _risk_category(score, config),
                "reasons": reasons,
                "features": features,
            }
        )
    return results


def load_evaluation_role_manifest(path=DEFAULT_ROLE_MANIFEST_PATH):
    """Load the generator's evaluation-only account-role sidecar."""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Evaluation role manifest not found: {manifest_path}. "
            "Regenerate the synthetic dataset with SyntheticTransactionGenerator."
        )
    try:
        manifest = pd.read_csv(manifest_path)
    except pd.errors.EmptyDataError as error:
        raise ValueError(
            f"Evaluation role manifest is empty: {manifest_path}"
        ) from error
    except pd.errors.ParserError as error:
        raise ValueError(
            f"Could not parse evaluation role manifest {manifest_path}: {error}"
        ) from error

    required_columns = {"account_id", "evaluation_role"}
    missing_columns = sorted(required_columns - set(manifest.columns))
    if missing_columns:
        raise ValueError(
            "Evaluation role manifest is missing required columns: "
            + ", ".join(missing_columns)
        )
    for column in required_columns:
        missing = manifest[column].isna() | manifest[column].astype(
            "string"
        ).str.strip().eq("")
        if missing.any():
            raise ValueError(
                f"Evaluation role manifest contains missing values in '{column}'"
            )
    if manifest["account_id"].duplicated().any():
        raise ValueError("Evaluation role manifest contains duplicate account_id values")

    invalid_roles = ~manifest["evaluation_role"].isin(EVALUATION_ROLES)
    if invalid_roles.any():
        values = sorted(
            manifest.loc[invalid_roles, "evaluation_role"].astype(str).unique()
        )
        raise ValueError(f"Invalid evaluation role value(s): {values}")
    return manifest


def evaluate_results(detections, role_manifest=None, config=None):
    """Evaluate focal-account and participant alerts using sidecar roles only.

    The transaction-level scenario_label is deliberately not used to assign
    account ground truth. The role manifest is evaluation metadata and is not
    passed into account feature generation or detection.
    """
    config = config or RuleEngineConfig()
    if role_manifest is None:
        return {
            "available": False,
            "message": "Evaluation-only account-role manifest is unavailable.",
        }

    if isinstance(role_manifest, (str, Path)):
        role_manifest = load_evaluation_role_manifest(role_manifest)
    elif not isinstance(role_manifest, pd.DataFrame):
        role_manifest = pd.DataFrame(role_manifest)

    missing_columns = sorted(
        {"account_id", "evaluation_role"} - set(role_manifest.columns)
    )
    if missing_columns:
        return {
            "available": False,
            "message": (
                "Evaluation role manifest is missing required columns: "
                + ", ".join(missing_columns)
            ),
        }

    role_manifest = role_manifest.loc[:, ["account_id", "evaluation_role"]].copy()
    if role_manifest.isna().any().any():
        raise ValueError("Evaluation role manifest contains missing values")
    role_manifest["account_id"] = role_manifest["account_id"].astype(str)
    if role_manifest["account_id"].duplicated().any():
        raise ValueError("Evaluation role manifest contains duplicate account_id values")
    invalid_roles = ~role_manifest["evaluation_role"].isin(EVALUATION_ROLES)
    if invalid_roles.any():
        values = sorted(
            role_manifest.loc[invalid_roles, "evaluation_role"].astype(str).unique()
        )
        raise ValueError(f"Invalid evaluation role value(s): {values}")

    detection_accounts = {result["account_id"] for result in detections}
    manifest_accounts = set(role_manifest["account_id"])
    if detection_accounts != manifest_accounts:
        missing_roles = sorted(detection_accounts - manifest_accounts)
        extra_roles = sorted(manifest_accounts - detection_accounts)
        raise ValueError(
            "Detection accounts and evaluation manifest accounts do not match; "
            f"missing roles for {missing_roles[:5]}, extra manifest accounts "
            f"{extra_roles[:5]}"
        )

    roles_by_account = role_manifest.set_index("account_id")["evaluation_role"]
    focal_positive_accounts = set(
        roles_by_account.index[
            roles_by_account.eq("FOCAL_SUSPICIOUS")
        ]
    )
    participant_positive_accounts = set(
        roles_by_account.index[
            roles_by_account.isin(
                {
                    "FOCAL_SUSPICIOUS",
                    "SOURCE_PARTICIPANT",
                    "DESTINATION_PARTICIPANT",
                }
            )
        ]
    )
    predicted_positive_accounts = {
        result["account_id"]
        for result in detections
        if result["risk_score"] >= config.medium_risk_threshold
    }
    evaluated_accounts = manifest_accounts
    focal_metrics = _evaluate_positive_set(
        focal_positive_accounts,
        predicted_positive_accounts,
        evaluated_accounts,
        "FOCAL_SUSPICIOUS",
    )
    participant_metrics = _evaluate_positive_set(
        participant_positive_accounts,
        predicted_positive_accounts,
        evaluated_accounts,
        "SUSPICIOUS_PARTICIPANT",
    )

    return {
        "available": True,
        "ground_truth_source": "evaluation-only account-role manifest",
        "role_counts": {
            role: int(role_manifest["evaluation_role"].eq(role).sum())
            for role in (
                "FOCAL_SUSPICIOUS",
                "SOURCE_PARTICIPANT",
                "DESTINATION_PARTICIPANT",
                "NORMAL",
            )
        },
        "prediction_threshold": config.medium_risk_threshold,
        "evaluated_account_count": len(evaluated_accounts),
        "focal_account_evaluation": focal_metrics,
        "participant_evaluation": participant_metrics,
        "precision": focal_metrics["precision"],
        "recall": focal_metrics["recall"],
        "f1_score": focal_metrics["f1_score"],
        "confusion_matrix": focal_metrics["confusion_matrix"],
    }


def run_rule_engine(transactions=None, config=None, role_manifest=None):
    """Run detection, then optionally evaluate with a separate role manifest."""
    uses_default_dataset = transactions is None
    source = load_transactions() if uses_default_dataset else transactions
    detections = detect_accounts(
        source.drop(columns=["scenario_label"], errors="ignore"), config
    )
    if role_manifest is None and uses_default_dataset:
        if DEFAULT_ROLE_MANIFEST_PATH.is_file():
            role_manifest = load_evaluation_role_manifest()
    elif isinstance(role_manifest, (str, Path)):
        role_manifest = load_evaluation_role_manifest(role_manifest)
    return {
        "accounts": detections,
        "evaluation": evaluate_results(
            detections, role_manifest=role_manifest, config=config
        ),
    }


def print_report(report):
    """Print concise account indicators and optional evaluation metrics."""
    print(f"Accounts analyzed: {len(report['accounts'])}")
    print("\nHighest illustrative risk scores:")
    for account in sorted(
        report["accounts"],
        key=lambda item: item["risk_score"],
        reverse=True,
    )[:10]:
        print(
            f"- {account['account_id']}: {account['risk_score']}/100 "
            f"({account['risk_category']})"
        )
        for reason in account["reasons"]:
            print(f"  - {reason['explanation']}")

    evaluation = report["evaluation"]
    if not evaluation["available"]:
        print(f"\nEvaluation unavailable: {evaluation['message']}")
        return
    print("\nFocal-account evaluation (evaluation-only account roles):")
    print(f"Precision: {evaluation['focal_account_evaluation']['precision']:.3f}")
    print(f"Recall:    {evaluation['focal_account_evaluation']['recall']:.3f}")
    print(f"F1 score:  {evaluation['focal_account_evaluation']['f1_score']:.3f}")
    print("Confusion matrix (actual rows; predicted columns; NORMAL, focal):")
    for row in evaluation["focal_account_evaluation"]["confusion_matrix"][
        "rows_actual_columns_predicted"
    ]:
        print(f"  {row}")
    print("\nBroader participant evaluation (focal + source + destination):")
    print(f"Precision: {evaluation['participant_evaluation']['precision']:.3f}")
    print(f"Recall:    {evaluation['participant_evaluation']['recall']:.3f}")
    print(f"F1 score:  {evaluation['participant_evaluation']['f1_score']:.3f}")
    print(
        "Confusion matrix (actual rows; predicted columns; NORMAL, participant):"
    )
    for row in evaluation["participant_evaluation"]["confusion_matrix"][
        "rows_actual_columns_predicted"
    ]:
        print(f"  {row}")
    print(
        "These synthetic-label metrics are for research evaluation only; "
        "risk indicators are not proof of criminal activity."
    )


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

    invalid_status = ~transactions["status"].isin({"SUCCESS", "DECLINED"})
    if invalid_status.any():
        raise ValueError("status must contain only SUCCESS or DECLINED")

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

    timestamps = pd.to_datetime(
        transactions["timestamp"], errors="coerce", utc=True
    )
    if timestamps.isna().any():
        raise ValueError("timestamp must contain valid date/time values")


def _max_window_count(timestamps, window_minutes):
    window = pd.Timedelta(minutes=window_minutes)
    left = 0
    maximum = 0
    for right, timestamp in enumerate(timestamps):
        while timestamp - timestamps[left] > window:
            left += 1
        maximum = max(maximum, right - left + 1)
    return maximum


def _evaluate_rules(features, config):
    reasons = []
    count = features["successful_transaction_count"]
    concentration_count = features["short_window_transaction_count"]
    concentration_share = features["short_window_transaction_share"]
    if (
        count >= config.concentration_min_transactions
        and concentration_share >= config.concentration_min_share
    ):
        reasons.append(
            {
                "rule": "short_window_concentration",
                "points": config.concentration_points,
                "explanation": (
                    f"{concentration_count} of {count} successful transactions "
                    f"({concentration_share:.0%}) occurred within a "
                    f"{config.concentration_window_minutes}-minute window; "
                    f"configured threshold is "
                    f"{config.concentration_min_share:.0%}."
                ),
            }
        )

    frequency = features["transaction_activity_frequency_per_day"]
    if (
        count >= config.min_transactions_for_rules
        and frequency >= config.activity_threshold_per_day
    ):
        reasons.append(
            {
                "rule": "elevated_activity",
                "points": config.activity_points,
                "explanation": (
                    f"Observed {frequency:.2f} successful transactions per day "
                    f"over the dataset period; configured threshold is "
                    f"{config.activity_threshold_per_day:.2f}."
                ),
            }
        )

    ratio = features["incoming_outgoing_amount_ratio"]
    incoming_count = features["incoming_transaction_count"]
    outgoing_count = features["outgoing_transaction_count"]
    unique_incoming = features["unique_incoming_counterparties"]
    if (
        count >= config.min_transactions_for_rules
        and incoming_count >= config.movement_min_incoming_transactions
        and outgoing_count >= config.movement_min_outgoing_transactions
        and unique_incoming >= config.movement_min_incoming_counterparties
        and ratio is not None
        and ratio >= config.incoming_outgoing_ratio_threshold
    ):
        reasons.append(
            {
                "rule": "incoming_to_outgoing_movement_imbalance",
                "points": config.movement_points,
                "explanation": (
                    f"Received {incoming_count} transactions from "
                    f"{unique_incoming} unique accounts and sent "
                    f"{outgoing_count}; incoming value is {ratio:.2f} times "
                    "outgoing value, meeting the configured movement thresholds."
                ),
            }
        )
    return reasons


def _risk_category(score, config):
    if score >= config.high_risk_threshold:
        return "HIGH"
    if score >= config.medium_risk_threshold:
        return "MEDIUM"
    return "LOW"


def _safe_divide(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def _evaluate_positive_set(
    actual_positive, predicted_positive, evaluated_accounts, positive_label
):
    true_positive = len(predicted_positive & actual_positive)
    false_positive = len(predicted_positive - actual_positive)
    false_negative = len(actual_positive - predicted_positive)
    true_negative = len(
        evaluated_accounts - predicted_positive - actual_positive
    )
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    f1 = _safe_divide(2 * precision * recall, precision + recall)
    return {
        "positive_account_count": len(actual_positive),
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "confusion_matrix": {
            "labels": ["NORMAL", positive_label],
            "rows_actual_columns_predicted": [
                [true_negative, false_positive],
                [false_negative, true_positive],
            ],
        },
    }


def main():
    try:
        print_report(run_rule_engine())
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
