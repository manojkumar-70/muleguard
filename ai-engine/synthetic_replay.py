"""Chronological replay of synthetic transfers through the existing detectors."""

import math

import pandas as pd

from anomaly_detection import detect_account_anomalies
from risk_aggregation import DISCLAIMER, aggregate_risk
from rule_based_analysis import analyze_accounts
from transaction_graph_analysis import analyze_transaction_graph


REQUIRED_COLUMNS = {
    "transaction_id",
    "sender",
    "receiver",
    "amount_paise",
    "timestamp",
    "status",
}


def replay_transactions(transactions, seed=42):
    """Replay one synthetic transaction at a time using only its history prefix.

    Equal timestamps retain input order. Scenario and evaluation-role fields may
    be present for later evaluation but are removed before detector execution.
    """
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")

    if transactions.empty:
        return {
            "transaction_count": 0,
            "seed": seed,
            "steps": [],
            "accounts": {},
            "disclaimer": DISCLAIMER,
        }

    missing_columns = sorted(REQUIRED_COLUMNS - set(transactions.columns))
    if missing_columns:
        raise ValueError(
            "Transactions are missing required columns: "
            + ", ".join(missing_columns)
        )

    replay_data = transactions.copy()
    replay_data["_replay_input_order"] = range(len(replay_data))
    replay_data["_replay_timestamp"] = pd.to_datetime(
        replay_data["timestamp"], errors="coerce", utc=True
    )
    if replay_data["_replay_timestamp"].isna().any():
        raise ValueError("timestamp must contain valid date/time values")

    event_ids = replay_data["transaction_id"]
    if event_ids.isna().any() or event_ids.astype("string").str.strip().eq("").any():
        raise ValueError("transaction_id values must be non-empty")
    if event_ids.duplicated().any():
        raise ValueError("transaction_id values must be unique")

    replay_data = replay_data.sort_values(
        ["_replay_timestamp", "_replay_input_order"], kind="mergesort"
    ).reset_index(drop=True)
    detector_columns = [
        column
        for column in transactions.columns
        if column not in {"scenario_label", "evaluation_role"}
    ]
    detector_history = replay_data.loc[:, detector_columns].reset_index(drop=True)
    ordered_timestamps = replay_data["_replay_timestamp"].tolist()
    detector_history["timestamp"] = replay_data["_replay_timestamp"].array
    ordered_transaction_ids = replay_data["transaction_id"].tolist()
    steps = []
    accounts = {}

    for event_number, (event_timestamp, event_id) in enumerate(
        zip(ordered_timestamps, ordered_transaction_ids), start=1
    ):
        history_frame = detector_history.iloc[:event_number].copy(deep=True)
        step = _analyze_history(history_frame, seed)
        history_ids = ordered_transaction_ids[:event_number]
        new_alerts = []

        rules_by_account = {
            result["account_id"]: result for result in step["rule_results"]
        }
        graph_results_by_account = {
            result["account_id"]: result
            for result in step["graph_results"]["accounts"]
        }
        ml_by_account = {
            result["account_id"]: result for result in step["ml_results"]
        }
        for risk_account in step["risk_results"]["accounts"]:
            account_id = risk_account["account_id"]
            progression = accounts.setdefault(
                account_id,
                {
                    "first_alert_timestamp": None,
                    "transaction_count_at_first_alert": None,
                    "risk_score_progression": [],
                    "first_alert_explanation": None,
                },
            )
            progression["risk_score_progression"].append(
                {
                    "timestamp": event_timestamp.isoformat(),
                    "transaction_count": event_number,
                    "risk_score": risk_account["risk_score"],
                    "risk_level": risk_account["risk_level"],
                }
            )
            if (
                progression["first_alert_timestamp"] is None
                and risk_account["risk_level"] in {"MEDIUM", "HIGH"}
            ):
                explanation = _detection_explanation(
                    account_id,
                    risk_account,
                    rules_by_account,
                    graph_results_by_account,
                    ml_by_account,
                )
                progression["first_alert_timestamp"] = event_timestamp.isoformat()
                progression["transaction_count_at_first_alert"] = event_number
                progression["first_alert_explanation"] = explanation
                new_alerts.append(
                    {
                        "account_id": account_id,
                        "risk_score": risk_account["risk_score"],
                        "risk_level": risk_account["risk_level"],
                        "explanation": explanation,
                    }
                )

        steps.append(
            {
                "step": event_number,
                "transaction_id": event_id,
                "timestamp": event_timestamp.isoformat(),
                "available_transaction_ids": history_ids,
                "risk_scores": {
                    result["account_id"]: result["risk_score"]
                    for result in step["risk_results"]["accounts"]
                },
                "new_alerts": new_alerts,
            }
        )

    return {
        "transaction_count": len(replay_data),
        "seed": seed,
        "steps": steps,
        "accounts": accounts,
        "disclaimer": DISCLAIMER,
    }


def _analyze_history(history, seed):
    rule_results = analyze_accounts(history)
    ml_results = detect_account_anomalies(
        history,
        random_state=seed,
    )
    ml_records = ml_results.to_dict(orient="records")
    graph_results = analyze_transaction_graph(history)
    risk_results = aggregate_risk(rule_results, ml_records, graph_results)
    return {
        "rule_results": rule_results,
        "ml_results": ml_records,
        "graph_results": graph_results,
        "risk_results": risk_results,
    }


def _detection_explanation(
    account_id,
    risk_account,
    rule_results,
    graph_results,
    ml_results,
):
    rule = rule_results[account_id]
    graph = graph_results[account_id]
    ml = ml_results[account_id]
    contributions = risk_account["method_contributions"]

    return {
        "summary": (
            f"Illustrative risk reached {risk_account['risk_level']} at "
            f"{risk_account['risk_score']}/100."
        ),
        "signals": {
            "rule_based": {
                "explanation": contributions["rule_based"]["explanation"],
                "indicators": rule["risk_indicators"],
            },
            "graph_based": {
                "explanation": contributions["graph_based"]["explanation"],
                "indicators": graph["risk_indicators"],
            },
            "ml_anomaly": {
                "explanation": contributions["ml_anomaly"]["explanation"],
                "is_anomaly": bool(ml["is_anomaly"]),
                "anomaly_score": _finite_float(ml["anomaly_score"]),
            },
        },
        "disclaimer": DISCLAIMER,
    }


def _finite_float(value):
    result = float(value)
    return result if math.isfinite(result) else None
