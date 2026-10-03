"""Opt-in low-latency streaming analysis for synthetic payment events.

This protocol trains one IsolationForest on an unlabeled chronological warm-up,
then freezes it. It is intentionally separate from strict per-prefix replay.
"""

import math
import time
from collections import defaultdict

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from anomaly_detection import FEATURE_COLUMNS, _validate_transactions
from risk_aggregation import DISCLAIMER, aggregate_risk
from rule_based_analysis import analyze_accounts
from transaction_graph_analysis import analyze_transaction_graph


PROTOCOL = "stream_frozen_model"


def run_synthetic_stream(
    transactions,
    warmup_events=50,
    seed=42,
    include_timing=False,
):
    """Analyze synthetic events chronologically using a frozen warm-up model.

    The first ``warmup_events`` are used only to fit the anomaly model. No
    detector output or alert is emitted for those events. Every later event is
    analyzed using an isolated transaction prefix containing no future events.
    Evaluation columns are stripped before feature building, model fitting, or
    detector calls.
    """
    if not isinstance(warmup_events, int) or isinstance(warmup_events, bool):
        raise ValueError("warmup_events must be an integer")
    if warmup_events < 1:
        raise ValueError("warmup_events must be at least 1")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not isinstance(transactions, pd.DataFrame):
        raise TypeError("transactions must be a pandas DataFrame")
    if transactions.empty:
        return _empty_result(warmup_events, seed)

    detector_input = transactions.drop(
        columns=["scenario_label", "evaluation_role"], errors="ignore"
    )
    _validate_transactions(detector_input)
    ordered = _prepare_ordered(detector_input)
    if warmup_events >= len(ordered):
        raise ValueError(
            "warmup_events must be less than the transaction count so at least "
            "one event is scored."
        )

    warmup = ordered.iloc[:warmup_events].copy(deep=True)
    training_started = time.perf_counter()
    model, training_features = _fit_stream_model(warmup, seed)
    training_seconds = time.perf_counter() - training_started

    accounts = {}
    steps = []
    alert_accounts = {}
    per_event_seconds = []

    for event_index in range(len(ordered)):
        event_started = time.perf_counter()
        prefix = ordered.iloc[: event_index + 1].copy(deep=True)
        timestamp = prefix.iloc[-1]["timestamp"]
        transaction_id = prefix.iloc[-1]["transaction_id"]

        if event_index < warmup_events:
            steps.append(
                {
                    "protocol": PROTOCOL,
                    "step": event_index + 1,
                    "transaction_id": transaction_id,
                    "timestamp": timestamp.isoformat(),
                    "phase": "warmup",
                    "detection_enabled": False,
                    "risk_scores": {},
                    "new_alerts": [],
                }
            )
            per_event_seconds.append(time.perf_counter() - event_started)
            continue

        prefix_features = _build_incremental_features(prefix)
        current_features = prefix_features.loc[:, FEATURE_COLUMNS]
        predictions = model.predict(current_features)
        anomaly_scores = -model.decision_function(current_features)
        ml_results = prefix_features.copy()
        ml_results["prediction"] = [
            "ANOMALY" if prediction == -1 else "TYPICAL"
            for prediction in predictions
        ]
        ml_results["is_anomaly"] = predictions == -1
        ml_results["anomaly_score"] = anomaly_scores
        ml_records = ml_results.to_dict(orient="records")

        rule_results = analyze_accounts(prefix)
        graph_results = analyze_transaction_graph(prefix)
        risk_results = aggregate_risk(rule_results, ml_records, graph_results)
        risk_by_account = {
            account["account_id"]: account
            for account in risk_results["accounts"]
        }
        rule_by_account = {
            account["account_id"]: account for account in rule_results
        }
        graph_by_account = {
            account["account_id"]: account for account in graph_results["accounts"]
        }
        ml_by_account = {
            account["account_id"]: account for account in ml_records
        }
        new_alerts = []

        for account_id, risk in risk_by_account.items():
            state = accounts.setdefault(
                account_id,
                {
                    "protocol": PROTOCOL,
                    "first_alert_timestamp": None,
                    "transaction_count_at_first_alert": None,
                    "risk_score_progression": [],
                    "first_alert_explanation": None,
                },
            )
            state["risk_score_progression"].append(
                {
                    "protocol": PROTOCOL,
                    "timestamp": timestamp.isoformat(),
                    "transaction_count": event_index + 1,
                    "risk_score": risk["risk_score"],
                    "risk_level": risk["risk_level"],
                }
            )
            if (
                state["first_alert_timestamp"] is None
                and risk["risk_level"] in {"MEDIUM", "HIGH"}
            ):
                explanation = _explanation(
                    account_id,
                    risk,
                    rule_by_account[account_id],
                    graph_by_account[account_id],
                    ml_by_account[account_id],
                )
                state["first_alert_timestamp"] = timestamp.isoformat()
                state["transaction_count_at_first_alert"] = event_index + 1
                state["first_alert_explanation"] = explanation
                alert_accounts[account_id] = state
                new_alerts.append(
                    {
                        "protocol": PROTOCOL,
                        "account_id": account_id,
                        "risk_score": risk["risk_score"],
                        "risk_level": risk["risk_level"],
                        "explanation": explanation,
                    }
                )

        step = {
            "protocol": PROTOCOL,
            "step": event_index + 1,
            "transaction_id": transaction_id,
            "timestamp": timestamp.isoformat(),
            "phase": "scored",
            "detection_enabled": True,
            "risk_scores": {
                account_id: risk["risk_score"]
                for account_id, risk in risk_by_account.items()
            },
            "new_alerts": new_alerts,
        }
        steps.append(step)
        per_event_seconds.append(time.perf_counter() - event_started)

    result = {
        "protocol": PROTOCOL,
        "model_protocol": {
            "protocol": PROTOCOL,
            "training_scope": "first chronological warm-up events only",
            "warmup_event_count": warmup_events,
            "training_account_count": len(training_features),
            "training_seed": seed,
            "training_labels_used": False,
            "model_frozen_after_warmup": True,
            "isolation_forest_contamination": 0.1,
            "isolation_forest_estimators": 200,
        },
        "transaction_count": len(ordered),
        "warmup_event_count": warmup_events,
        "scored_event_count": len(ordered) - warmup_events,
        "seed": seed,
        "steps": steps,
        "accounts": accounts,
        "alerted_accounts": sorted(alert_accounts),
        "disclaimer": DISCLAIMER,
        "limitations": [
            "This frozen-model streaming protocol is not strict chronological evaluation.",
            "The IsolationForest and its preprocessing are trained once on the unlabeled warm-up prefix.",
            "Aggregate ML percentiles are relative to the accounts observed by each event and can change as the population grows.",
            "Rule and graph analyses use the current transaction prefix through existing batch analyzers.",
            "Risk alerts are illustrative synthetic review signals, not proof of criminal activity.",
        ],
    }
    if include_timing:
        result["timing"] = {
            "model_training_seconds": training_seconds,
            "per_event_seconds": per_event_seconds,
        }
    return result


def _prepare_ordered(transactions):
    ordered = transactions.copy()
    ordered["_stream_input_order"] = range(len(ordered))
    ordered["timestamp"] = pd.to_datetime(
        ordered["timestamp"], errors="coerce", utc=True
    )
    if ordered["timestamp"].isna().any():
        raise ValueError("timestamp must contain valid date/time values")
    transaction_ids = ordered["transaction_id"]
    if (
        transaction_ids.isna().any()
        or transaction_ids.astype("string").str.strip().eq("").any()
    ):
        raise ValueError("transaction_id values must be non-empty")
    if transaction_ids.duplicated().any():
        raise ValueError("transaction_id values must be unique")
    ordered = ordered.sort_values(
        ["timestamp", "_stream_input_order"], kind="mergesort"
    ).reset_index(drop=True)
    return ordered.drop(columns="_stream_input_order")


def _fit_stream_model(warmup, seed):
    training_features = _build_incremental_features(warmup)
    if len(training_features) < 2:
        raise ValueError(
            "Warm-up must observe at least two accounts to train IsolationForest."
        )
    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "isolation_forest",
                IsolationForest(
                    contamination=0.1,
                    random_state=seed,
                    n_estimators=200,
                ),
            ),
        ]
    )
    model.fit(training_features.loc[:, FEATURE_COLUMNS])
    return model, training_features


def _build_incremental_features(transactions):
    """Build as-of-account features using only the supplied transaction prefix."""
    _validate_transactions(transactions)
    account_events = defaultdict(
        lambda: {
            "incoming_count": 0,
            "outgoing_count": 0,
            "incoming_total": 0.0,
            "outgoing_total": 0.0,
            "incoming_counterparties": set(),
            "outgoing_counterparties": set(),
            "timestamps": [],
        }
    )
    accounts = set()
    first_successful_timestamp = None
    last_successful_timestamp = None
    successful_events = []

    for row in transactions.itertuples(index=False):
        sender = str(row.sender)
        receiver = str(row.receiver)
        accounts.update((sender, receiver))
        if row.status != "SUCCESS":
            continue
        timestamp = pd.to_datetime(row.timestamp, utc=True)
        amount = float(row.amount_paise)
        successful_events.append((timestamp, sender, receiver, amount))
        first_successful_timestamp = (
            timestamp
            if first_successful_timestamp is None
            else min(first_successful_timestamp, timestamp)
        )
        last_successful_timestamp = (
            timestamp
            if last_successful_timestamp is None
            else max(last_successful_timestamp, timestamp)
        )
        sender_state = account_events[sender]
        sender_state["outgoing_count"] += 1
        sender_state["outgoing_total"] += amount
        sender_state["outgoing_counterparties"].add(receiver)
        sender_state["timestamps"].append(timestamp)

        receiver_state = account_events[receiver]
        receiver_state["incoming_count"] += 1
        receiver_state["incoming_total"] += amount
        receiver_state["incoming_counterparties"].add(sender)
        receiver_state["timestamps"].append(timestamp)

    period_days = 1.0
    if first_successful_timestamp is not None:
        period_days = max(
            (last_successful_timestamp - first_successful_timestamp).total_seconds()
            / 86_400,
            1.0,
        )

    rows = []
    for account_id in sorted(accounts):
        state = account_events[account_id]
        timestamps = sorted(state["timestamps"])
        incoming_total = state["incoming_total"]
        outgoing_total = state["outgoing_total"]
        event_count = state["incoming_count"] + state["outgoing_count"]
        rows.append(
            {
                "account_id": account_id,
                "transaction_count": event_count,
                "incoming_transaction_count": state["incoming_count"],
                "outgoing_transaction_count": state["outgoing_count"],
                "total_incoming_amount_paise": incoming_total,
                "total_outgoing_amount_paise": outgoing_total,
                "unique_incoming_counterparties": len(
                    state["incoming_counterparties"]
                ),
                "unique_outgoing_counterparties": len(
                    state["outgoing_counterparties"]
                ),
                "incoming_outgoing_ratio": (
                    incoming_total / outgoing_total if outgoing_total else 0.0
                ),
                "transaction_frequency_per_day": event_count / period_days,
                "short_window_transaction_concentration": (
                    _max_window_count(timestamps) / len(timestamps)
                    if timestamps
                    else 0.0
                ),
            }
        )
    return pd.DataFrame(rows, columns=("account_id", *FEATURE_COLUMNS))


def _max_window_count(timestamps):
    left = 0
    maximum = 0
    window = pd.Timedelta(minutes=15)
    for right, timestamp in enumerate(timestamps):
        while timestamp - timestamps[left] > window:
            left += 1
        maximum = max(maximum, right - left + 1)
    return maximum


def _explanation(account_id, risk, rule, graph, ml):
    contributions = risk["method_contributions"]
    return {
        "summary": (
            f"Illustrative streaming risk reached {risk['risk_level']} at "
            f"{risk['risk_score']}/100."
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
        "protocol": PROTOCOL,
        "account_id": account_id,
    }


def _finite_float(value):
    result = float(value)
    return result if math.isfinite(result) else None


def _empty_result(warmup_events, seed):
    return {
        "protocol": PROTOCOL,
        "model_protocol": {
            "protocol": PROTOCOL,
            "training_scope": "first chronological warm-up events only",
            "warmup_event_count": warmup_events,
            "training_account_count": 0,
            "training_seed": seed,
            "training_labels_used": False,
            "model_frozen_after_warmup": True,
            "isolation_forest_contamination": 0.1,
            "isolation_forest_estimators": 200,
        },
        "transaction_count": 0,
        "warmup_event_count": warmup_events,
        "scored_event_count": 0,
        "seed": seed,
        "steps": [],
        "accounts": {},
        "alerted_accounts": [],
        "disclaimer": DISCLAIMER,
        "limitations": [
            "No synthetic transactions were supplied; no model was trained or scored."
        ],
    }
