"""Explainable graph heuristics for synthetic transaction networks.

Alerts describe unusual observed structures for review; they do not identify
criminals or prove fraud. Scenario labels are not used by this module.
"""

from dataclasses import dataclass

import networkx as nx
import pandas as pd


REQUIRED_COLUMNS = {"transaction_id", "sender", "receiver", "amount_paise", "timestamp", "status"}
VALID_STATUSES = {"SUCCESS", "DECLINED"}


@dataclass(frozen=True)
class GraphRuleConfig:
    min_successful_transactions: int = 8
    min_unique_incoming_accounts: int = 6
    min_unique_outgoing_accounts: int = 2
    short_window_minutes: int = 15
    short_window_concentration_threshold: float = 0.7

    def __post_init__(self):
        if self.min_successful_transactions < 1:
            raise ValueError("min_successful_transactions must be at least 1")
        if self.min_unique_incoming_accounts < 1:
            raise ValueError("min_unique_incoming_accounts must be at least 1")
        if self.min_unique_outgoing_accounts < 1:
            raise ValueError("min_unique_outgoing_accounts must be at least 1")
        if self.short_window_minutes < 1:
            raise ValueError("short_window_minutes must be at least 1")
        if not 0 < self.short_window_concentration_threshold <= 1:
            raise ValueError(
                "short_window_concentration_threshold must be in (0, 1]"
            )


def analyze_transaction_graph(transactions, config=None):
    """Return graph metrics, explainable account alerts, and connected clusters.

    Only successful synthetic transfers are included as directed graph edges.
    Account nodes seen only in declined transactions remain in the graph with
    zero successful-edge metrics.
    """
    config = config or GraphRuleConfig()
    _validate_transactions(transactions)

    graph = nx.MultiDiGraph()
    for row in transactions.itertuples(index=False):
        sender = str(row.sender)
        receiver = str(row.receiver)
        graph.add_node(sender)
        graph.add_node(receiver)
        if row.status != "SUCCESS":
            continue
        graph.add_edge(
            sender,
            receiver,
            key=str(row.transaction_id),
            amount_paise=int(pd.to_numeric(row.amount_paise)),
            timestamp=pd.to_datetime(row.timestamp, utc=True),
        )

    successful_count = graph.number_of_edges()
    alerts_by_account = {}
    accounts = []
    for account_id in sorted(graph.nodes):
        incoming_degree = graph.in_degree(account_id)
        outgoing_degree = graph.out_degree(account_id)
        incoming_edges = list(graph.in_edges(account_id, keys=True, data=True))
        outgoing_edges = list(graph.out_edges(account_id, keys=True, data=True))
        incoming_accounts = {source for source, _, _, _ in incoming_edges}
        outgoing_accounts = {target for _, target, _, _ in outgoing_edges}
        incoming_total = sum(edge[3]["amount_paise"] for edge in incoming_edges)
        outgoing_total = sum(edge[3]["amount_paise"] for edge in outgoing_edges)
        unique_connected = incoming_accounts | outgoing_accounts
        indicators = _account_indicators(
            incoming_degree=incoming_degree,
            outgoing_degree=outgoing_degree,
            unique_incoming=len(incoming_accounts),
            unique_outgoing=len(outgoing_accounts),
            incoming_edges=incoming_edges,
            outgoing_edges=outgoing_edges,
            config=config,
        )
        if indicators:
            alerts_by_account[account_id] = indicators
        accounts.append(
            {
                "account_id": account_id,
                "incoming_degree": incoming_degree,
                "outgoing_degree": outgoing_degree,
                "unique_incoming_accounts": len(incoming_accounts),
                "unique_outgoing_accounts": len(outgoing_accounts),
                "unique_connected_accounts": len(unique_connected),
                "weighted_incoming_amount_paise": incoming_total,
                "weighted_outgoing_amount_paise": outgoing_total,
                "risk_indicators": indicators,
            }
        )

    clusters = _connected_alert_clusters(graph, alerts_by_account)
    return {
        "summary": {
            "account_count": graph.number_of_nodes(),
            "successful_transaction_count": successful_count,
            "excluded_declined_transaction_count": len(transactions) - successful_count,
            "alert_account_count": len(alerts_by_account),
            "connected_alert_cluster_count": len(clusters),
        },
        "accounts": accounts,
        "clusters": clusters,
    }


def _account_indicators(
    incoming_degree,
    outgoing_degree,
    unique_incoming,
    unique_outgoing,
    incoming_edges,
    outgoing_edges,
    config,
):
    indicators = []
    successful_count = incoming_degree + outgoing_degree

    if (
        successful_count >= config.min_successful_transactions
        and unique_incoming >= config.min_unique_incoming_accounts
        and unique_outgoing >= config.min_unique_outgoing_accounts
    ):
        indicators.append(
            {
                "rule": "fan_in_then_fan_out",
                "explanation": (
                    f"Observed {incoming_degree} incoming and {outgoing_degree} "
                    f"outgoing successful transactions involving "
                    f"{unique_incoming} incoming and {unique_outgoing} outgoing "
                    "accounts, meeting the configured fan-in/fan-out thresholds."
                ),
            }
        )

    edges = incoming_edges + outgoing_edges
    timestamps = sorted(edge[3]["timestamp"] for edge in edges)
    if (
        successful_count >= config.min_successful_transactions
        and timestamps
    ):
        concentration = _max_window_count(
            timestamps, config.short_window_minutes
        ) / len(timestamps)
        if concentration >= config.short_window_concentration_threshold:
            indicators.append(
                {
                    "rule": "short_window_activity_concentration",
                    "explanation": (
                        f"{concentration:.0%} of this account's successful "
                        f"transactions fall within one {config.short_window_minutes}-"
                        f"minute window, meeting the configured "
                        f"{config.short_window_concentration_threshold:.0%} threshold."
                    ),
                }
            )
    return indicators


def _max_window_count(timestamps, window_minutes):
    window_seconds = window_minutes * 60
    left = 0
    maximum = 0
    for right, timestamp in enumerate(timestamps):
        while (timestamp - timestamps[left]).total_seconds() > window_seconds:
            left += 1
        maximum = max(maximum, right - left + 1)
    return maximum


def _connected_alert_clusters(graph, alerts_by_account):
    clusters = []
    for component in nx.weakly_connected_components(graph):
        flagged_accounts = sorted(component.intersection(alerts_by_account))
        if not flagged_accounts:
            continue
        clusters.append(
            {
                "cluster_id": f"CLUSTER-{len(clusters) + 1:04d}",
                "account_ids": sorted(component),
                "flagged_account_ids": flagged_accounts,
                "risk_indicators": [
                    {
                        "account_id": account_id,
                        "indicators": alerts_by_account[account_id],
                    }
                    for account_id in flagged_accounts
                ],
                "interpretation": (
                    "Connected component containing one or more accounts with "
                    "configured unusual-structure indicators; review context "
                    "before drawing conclusions."
                ),
            }
        )
    return clusters


def _validate_transactions(transactions):
    missing_columns = sorted(REQUIRED_COLUMNS - set(transactions.columns))
    if missing_columns:
        raise ValueError(
            "Transactions are missing required columns: "
            + ", ".join(missing_columns)
        )
    if transactions.empty:
        raise ValueError("Cannot analyze transaction graph: dataset is empty")

    for column in ("transaction_id", "sender", "receiver", "status"):
        missing = transactions[column].isna() | transactions[column].astype(
            "string"
        ).str.strip().eq("")
        if missing.any():
            raise ValueError(f"Transactions contain missing values in '{column}'")

    if transactions["transaction_id"].duplicated().any():
        raise ValueError("transaction_id values must be unique")

    invalid_statuses = ~transactions["status"].isin(VALID_STATUSES)
    if invalid_statuses.any():
        values = sorted(
            transactions.loc[invalid_statuses, "status"].unique().tolist()
        )
        raise ValueError(f"Invalid transaction status value(s): {values}")

    amounts = pd.to_numeric(transactions["amount_paise"], errors="coerce")
    invalid_amounts = (
        amounts.isna()
        | amounts.isin([float("inf"), float("-inf")])
        | amounts.le(0)
        | amounts.mod(1).ne(0)
    )
    if invalid_amounts.any():
        raise ValueError("amount_paise must contain positive whole-number paise")

    timestamps = pd.to_datetime(transactions["timestamp"], errors="coerce", utc=True)
    if timestamps.isna().any():
        raise ValueError("timestamp must contain valid date/time values")
