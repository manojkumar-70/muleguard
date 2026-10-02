"""Unsupervised account anomaly detection over synthetic transactions.

An anomaly is a behaviour that differs from this input dataset; it is not
evidence of fraud or other wrongdoing.
"""

from collections import defaultdict

import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


REQUIRED_COLUMNS = {"sender", "receiver", "amount_paise", "timestamp", "status"}
FEATURE_COLUMNS = (
    "transaction_count",
    "incoming_transaction_count",
    "outgoing_transaction_count",
    "total_incoming_amount_paise",
    "total_outgoing_amount_paise",
    "unique_incoming_counterparties",
    "unique_outgoing_counterparties",
    "incoming_outgoing_ratio",
    "transaction_frequency_per_day",
    "short_window_transaction_concentration",
)
SHORT_WINDOW = pd.Timedelta(minutes=15)


def build_account_features(transactions):
    """Aggregate successful transaction activity into numerical account features.

    Account identifiers are retained only as output keys. They are not included
    in ``FEATURE_COLUMNS`` and must not be passed to the estimator.
    """
    _validate_transactions(transactions)
    if transactions.empty:
        raise ValueError("Cannot detect anomalies: transaction dataset is empty")

    successful = transactions.loc[transactions["status"].eq("SUCCESS")].copy()
    successful["amount_paise"] = pd.to_numeric(
        successful["amount_paise"], errors="coerce"
    )
    successful["timestamp"] = pd.to_datetime(
        successful["timestamp"], errors="coerce", utc=True
    )

    all_accounts = sorted(
        set(transactions["sender"].astype(str))
        | set(transactions["receiver"].astype(str))
    )
    events = defaultdict(list)
    for row in successful.itertuples(index=False):
        amount = float(row.amount_paise)
        timestamp = row.timestamp
        sender = str(row.sender)
        receiver = str(row.receiver)
        events[sender].append((timestamp, "outgoing", receiver, amount))
        events[receiver].append((timestamp, "incoming", sender, amount))

    rows = []
    for account_id in all_accounts:
        account_events = events[account_id]
        incoming = [event for event in account_events if event[1] == "incoming"]
        outgoing = [event for event in account_events if event[1] == "outgoing"]
        incoming_total = sum(event[3] for event in incoming)
        outgoing_total = sum(event[3] for event in outgoing)
        timestamps = sorted(event[0] for event in account_events)
        elapsed_days = (
            (timestamps[-1] - timestamps[0]).total_seconds() / 86_400
            if len(timestamps) > 1
            else 0.0
        )

        rows.append(
            {
                "account_id": account_id,
                "transaction_count": len(account_events),
                "incoming_transaction_count": len(incoming),
                "outgoing_transaction_count": len(outgoing),
                "total_incoming_amount_paise": incoming_total,
                "total_outgoing_amount_paise": outgoing_total,
                "unique_incoming_counterparties": len(
                    {event[2] for event in incoming}
                ),
                "unique_outgoing_counterparties": len(
                    {event[2] for event in outgoing}
                ),
                "incoming_outgoing_ratio": (
                    incoming_total / outgoing_total if outgoing_total else 0.0
                ),
                "transaction_frequency_per_day": (
                    len(account_events) / max(elapsed_days, 1.0)
                    if account_events
                    else 0.0
                ),
                "short_window_transaction_concentration": (
                    _max_window_count(timestamps) / len(timestamps)
                    if timestamps
                    else 0.0
                ),
            }
        )

    return pd.DataFrame(rows, columns=("account_id", *FEATURE_COLUMNS))


def detect_account_anomalies(
    transactions, contamination=0.1, random_state=42
):
    """Fit an IsolationForest and return account features and anomaly outputs.

    ``anomaly_score`` is the negated IsolationForest decision function, so
    larger values indicate more anomalous observations relative to this fit.
    It is not a probability or a fraud score.
    """
    if not 0 < contamination <= 0.5:
        raise ValueError("contamination must be greater than 0 and at most 0.5")

    account_features = build_account_features(transactions)
    model_inputs = account_features.loc[:, FEATURE_COLUMNS]
    pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "isolation_forest",
                IsolationForest(
                    contamination=contamination,
                    random_state=random_state,
                    n_estimators=200,
                ),
            ),
        ]
    )
    pipeline.fit(model_inputs)
    estimator = pipeline.named_steps["isolation_forest"]
    estimator = pipeline.named_steps["isolation_forest"]
    scaled_features = pipeline[:-1].transform(model_inputs)
    predictions = estimator.predict(scaled_features)
    decision_scores = estimator.decision_function(scaled_features)
    results = account_features.copy()
    results["prediction"] = [
        "ANOMALY" if prediction == -1 else "TYPICAL"
        for prediction in predictions
    ]
    results["is_anomaly"] = predictions == -1
    results["anomaly_score"] = -decision_scores
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

    invalid_status = ~transactions["status"].isin({"SUCCESS", "DECLINED"})
    if invalid_status.any():
        raise ValueError("status must contain only SUCCESS or DECLINED")

    amounts = pd.to_numeric(transactions["amount_paise"], errors="coerce")
    invalid_amounts = (
        amounts.isna()
        | amounts.isin([float("inf"), float("-inf")])
        | amounts.le(0)
    )
    if invalid_amounts.any():
        raise ValueError("amount_paise must contain positive finite numeric values")

    timestamps = pd.to_datetime(transactions["timestamp"], errors="coerce", utc=True)
    if timestamps.isna().any():
        raise ValueError("timestamp must contain valid date/time values")


def _max_window_count(timestamps):
    left = 0
    maximum = 0
    for right, timestamp in enumerate(timestamps):
        while timestamp - timestamps[left] > SHORT_WINDOW:
            left += 1
        maximum = max(maximum, right - left + 1)
    return maximum
