"""Evaluation-only metrics for chronological synthetic transaction replay."""

import argparse
import json
import statistics
from pathlib import Path

import pandas as pd

from data_loader import DEFAULT_DATASET_PATH, load_transactions
from synthetic_replay import replay_transactions


ROLE_COLUMN = "evaluation_role"
ACCOUNT_COLUMN = "account_id"
VALID_ROLES = {
    "FOCAL_SUSPICIOUS",
    "SOURCE_PARTICIPANT",
    "DESTINATION_PARTICIPANT",
    "NORMAL",
}
SUSPICIOUS_PARTICIPANT_ROLES = {
    "FOCAL_SUSPICIOUS",
    "SOURCE_PARTICIPANT",
    "DESTINATION_PARTICIPANT",
}
REQUIRED_TRANSACTION_COLUMNS = {
    "transaction_id",
    "sender",
    "receiver",
    "timestamp",
}


def evaluate_early_warning(
    transactions,
    role_manifest,
    checkpoints=(100, 250, 500, 1000),
    seed=42,
):
    """Replay synthetic data and evaluate latency using separate metadata.

    Detection always receives transaction data with scenario and role labels
    removed. Labels are used only after replay returns, for metric calculation.
    """
    checkpoint_values = _validate_checkpoints(checkpoints)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    roles = _load_roles(role_manifest)

    if not isinstance(transactions, pd.DataFrame):
        raise TypeError("transactions must be a pandas DataFrame")
    if transactions.empty:
        if roles:
            raise ValueError(
                "Cannot evaluate an empty transaction dataset with a non-empty "
                "evaluation role manifest."
            )
        return {
            "status": "no_data",
            "transaction_count": 0,
            "seed": seed,
            "checkpoints": list(checkpoint_values),
            "groups": _empty_group_results(checkpoint_values),
            "accounts": [],
            "scenario_label_summary": {},
            "limitations": [
                "No transaction events were available for replay or evaluation."
            ],
        }

    missing = sorted(REQUIRED_TRANSACTION_COLUMNS - set(transactions.columns))
    if missing:
        raise ValueError(
            "Transactions are missing required columns: " + ", ".join(missing)
        )
    if "scenario_label" not in transactions.columns:
        raise ValueError("Transactions are missing scenario_label for evaluation.")

    detector_transactions = transactions.drop(
        columns=["scenario_label", "evaluation_role"], errors="ignore"
    )
    replay = replay_transactions(detector_transactions, seed=seed)
    replay_accounts = replay["accounts"]
    account_ids = set(replay_accounts)
    if account_ids != set(roles):
        missing_roles = sorted(account_ids - set(roles))
        extra_roles = sorted(set(roles) - account_ids)
        raise ValueError(
            "Evaluation role manifest does not match observed accounts; "
            f"missing roles={missing_roles}, unknown accounts={extra_roles}."
        )

    accounts = []
    for account_id in sorted(account_ids):
        role = roles[account_id]
        replay_result = replay_accounts.get(account_id)
        progression = (
            replay_result["risk_score_progression"] if replay_result else []
        )
        if not progression:
            raise ValueError(
                f"Replay output is missing first-observation history for {account_id}."
            )
        first_alert_timestamp = (
            replay_result["first_alert_timestamp"] if replay_result else None
        )
        first_alert_count = (
            replay_result["transaction_count_at_first_alert"]
            if replay_result
            else None
        )
        first_observed = progression[0]
        first_observed_timestamp = pd.to_datetime(
            first_observed["timestamp"], utc=True
        )
        first_observed_count = first_observed["transaction_count"]
        latency_seconds = None
        transactions_to_alert = None
        if first_alert_timestamp is not None:
            alert_time = pd.to_datetime(first_alert_timestamp, utc=True)
            latency_seconds = (
                alert_time - first_observed_timestamp
            ).total_seconds()
            if latency_seconds < 0:
                raise ValueError(
                    f"Alert for {account_id} precedes its first observed transaction."
                )
            transactions_to_alert = first_alert_count - first_observed_count + 1
        accounts.append(
            {
                "account_id": account_id,
                "evaluation_role": role,
                "first_observed_timestamp": first_observed_timestamp.isoformat(),
                "first_observed_transaction_count": first_observed_count,
                "first_alert_timestamp": first_alert_timestamp,
                "first_alert_transaction_count": first_alert_count,
                "detected": first_alert_timestamp is not None,
                "detection_latency_seconds": latency_seconds,
                "transactions_from_first_observation_to_alert": transactions_to_alert,
            }
        )

    group_results = {
        "focal_suspicious": _summarize_group(
            accounts,
            {"FOCAL_SUSPICIOUS"},
            checkpoint_values,
            len(detector_transactions),
        ),
        "suspicious_participants": _summarize_group(
            accounts,
            SUSPICIOUS_PARTICIPANT_ROLES,
            checkpoint_values,
            len(detector_transactions),
        ),
        "legitimate_normal": _summarize_group(
            accounts,
            {"NORMAL"},
            checkpoint_values,
            len(detector_transactions),
        ),
    }
    group_results["legitimate_normal"]["false_alert_rate"] = group_results[
        "legitimate_normal"
    ]["detection_rate"]
    scenario_summary = _summarize_scenarios(
        transactions, detector_transactions, accounts
    )

    return {
        "status": "complete",
        "transaction_count": len(detector_transactions),
        "seed": seed,
        "positive_role_definitions": {
            "focal_suspicious": ["FOCAL_SUSPICIOUS"],
            "suspicious_participants": sorted(SUSPICIOUS_PARTICIPANT_ROLES),
            "legitimate_normal": ["NORMAL"],
        },
        "detection_labels_used_as_features": False,
        "checkpoints": list(checkpoint_values),
        "groups": group_results,
        "accounts": accounts,
        "scenario_label_summary": scenario_summary,
        "limitations": [
            "Evaluation uses synthetic transaction data and an evaluation-only role manifest.",
            "Never-alerted accounts are reported as missed and excluded from latency statistics.",
            "Risk alerts are illustrative review signals, not proof of criminal activity.",
        ],
    }


def _load_roles(role_manifest):
    if role_manifest is None:
        raise FileNotFoundError(
            "Evaluation role manifest is required for early-warning metrics."
        )
    if isinstance(role_manifest, (str, Path)):
        path = Path(role_manifest)
        if not path.is_file():
            raise FileNotFoundError(f"Evaluation role manifest not found: {path}")
        try:
            manifest = pd.read_csv(path)
        except pd.errors.EmptyDataError as error:
            raise ValueError(f"Evaluation role manifest is empty: {path}") from error
        except pd.errors.ParserError as error:
            raise ValueError(
                f"Could not parse evaluation role manifest {path}: {error}"
            ) from error
    elif isinstance(role_manifest, pd.DataFrame):
        manifest = role_manifest.copy()
    else:
        raise TypeError("role_manifest must be a CSV path or pandas DataFrame")

    missing = sorted({ACCOUNT_COLUMN, ROLE_COLUMN} - set(manifest.columns))
    if missing:
        raise ValueError(
            "Evaluation role manifest is missing required columns: "
            + ", ".join(missing)
        )
    if manifest.empty:
        return {}
    invalid_rows = (
        manifest[ACCOUNT_COLUMN].isna()
        | manifest[ACCOUNT_COLUMN].astype("string").str.strip().eq("")
        | ~manifest[ROLE_COLUMN].isin(VALID_ROLES)
    )
    if invalid_rows.any():
        raise ValueError(
            "Evaluation role manifest contains missing account IDs or invalid roles."
        )
    duplicates = manifest[ACCOUNT_COLUMN].duplicated()
    if duplicates.any():
        values = sorted(manifest.loc[duplicates, ACCOUNT_COLUMN].astype(str).unique())
        raise ValueError(
            "Duplicate account_id value(s) in evaluation role manifest: "
            + ", ".join(values)
        )
    return dict(zip(manifest[ACCOUNT_COLUMN], manifest[ROLE_COLUMN]))


def _validate_checkpoints(checkpoints):
    values = tuple(checkpoints)
    if not values:
        raise ValueError("At least one transaction checkpoint is required.")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in values
    ):
        raise ValueError("Transaction checkpoints must be positive integers.")
    if len(set(values)) != len(values):
        raise ValueError("Transaction checkpoints must be unique.")
    return tuple(sorted(values))


def _summarize_group(accounts, positive_roles, checkpoints, transaction_count):
    group = [
        account
        for account in accounts
        if account["evaluation_role"] in positive_roles
    ]
    detected = [account for account in group if account["detected"]]
    missed = [account for account in group if not account["detected"]]
    latencies = [account["detection_latency_seconds"] for account in detected]
    transaction_latencies = [
        account["transactions_from_first_observation_to_alert"]
        for account in detected
    ]
    by_checkpoint = []
    for checkpoint in checkpoints:
        detected_by_checkpoint = [
            account
            for account in group
            if account["first_alert_transaction_count"] is not None
            and account["first_alert_transaction_count"] <= checkpoint
        ]
        by_checkpoint.append(
            {
                "transaction_count": checkpoint,
                "checkpoint_reached": checkpoint <= transaction_count,
                "detected_count": len(detected_by_checkpoint),
                "account_count": len(group),
                "detection_rate": (
                    len(detected_by_checkpoint) / len(group) if group else 0.0
                ),
            }
        )

    return {
        "account_count": len(group),
        "detected_count": len(detected),
        "missed_count": len(missed),
        "detection_rate": len(detected) / len(group) if group else 0.0,
        "detected_accounts": sorted(account["account_id"] for account in detected),
        "missed_accounts": sorted(account["account_id"] for account in missed),
        "detection_rate_at_checkpoints": by_checkpoint,
        "detection_latency_seconds": _distribution(latencies),
        "transactions_from_first_observation_to_alert": _distribution(
            transaction_latencies
        ),
    }


def _distribution(values):
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "mean": statistics.mean(ordered) if ordered else None,
        "median": statistics.median(ordered) if ordered else None,
        "values": ordered,
    }


def _summarize_scenarios(transactions, detector_transactions, accounts):
    scenario_by_account = {}
    for label, sender, receiver in zip(
        transactions["scenario_label"].array,
        detector_transactions["sender"].array,
        detector_transactions["receiver"].array,
    ):
        for account_id in (
            str(sender),
            str(receiver),
        ):
            scenario_by_account.setdefault(account_id, set()).add(label)

    account_by_id = {account["account_id"]: account for account in accounts}
    result = {
        "transaction_label_counts": {
            str(label): int(count)
            for label, count in transactions["scenario_label"]
            .value_counts(dropna=False)
            .sort_index()
            .items()
        },
        "accounts_with_synthetic_suspicious_labeled_activity": 0,
        "detected_accounts_with_synthetic_suspicious_labeled_activity": 0,
    }
    labeled_accounts = {
        account_id
        for account_id, labels in scenario_by_account.items()
        if "SYNTHETIC_SUSPICIOUS" in labels
    }
    result["accounts_with_synthetic_suspicious_labeled_activity"] = len(
        labeled_accounts
    )
    result["detected_accounts_with_synthetic_suspicious_labeled_activity"] = sum(
        account_by_id[account_id]["detected"]
        for account_id in labeled_accounts
        if account_id in account_by_id
    )
    return result


def _empty_group_results(checkpoints):
    results = {
        group: _summarize_group([], roles, checkpoints, 0)
        for group, roles in (
            ("focal_suspicious", {"FOCAL_SUSPICIOUS"}),
            ("suspicious_participants", SUSPICIOUS_PARTICIPANT_ROLES),
            ("legitimate_normal", {"NORMAL"}),
        )
    }
    results["legitimate_normal"]["false_alert_rate"] = 0.0
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate early-warning timing on synthetic transactions."
    )
    parser.add_argument(
        "--checkpoints",
        nargs="+",
        type=int,
        default=[100, 250, 500, 1000],
        help="Total transaction counts at which to measure detection (default: 100 250 500 1000).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="IsolationForest random seed used by replay (default: 42).",
    )
    arguments = parser.parse_args()
    role_manifest = DEFAULT_DATASET_PATH.with_name("evaluation_account_roles.csv")
    try:
        transactions = load_transactions(DEFAULT_DATASET_PATH)
        report = evaluate_early_warning(
            transactions,
            role_manifest,
            checkpoints=arguments.checkpoints,
            seed=arguments.seed,
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
