"""Combine defensive rule, anomaly, and transaction-graph signals."""

from dataclasses import dataclass
import math


DISCLAIMER = (
    "Risk scores are illustrative indicators for review and are not proof of "
    "fraud or criminal activity. Recommendations do not perform real account "
    "actions; SIMULATED HOLD is a research label only."
)


@dataclass(frozen=True)
class RiskAggregationConfig:
    behavioral_weight: float = 0.7
    ml_weight: float = 0.3
    low_risk_max: float = 29.0
    medium_risk_max: float = 64.0

    def __post_init__(self):
        if self.behavioral_weight < 0 or self.ml_weight < 0:
            raise ValueError("Risk weights must not be negative")
        if self.behavioral_weight + self.ml_weight <= 0:
            raise ValueError("At least one risk weight must be positive")
        if not 0 <= self.low_risk_max < self.medium_risk_max <= 100:
            raise ValueError(
                "Risk thresholds must satisfy 0 <= low_risk_max "
                "< medium_risk_max <= 100"
            )


def aggregate_risk(rule_results, ml_results, graph_results, config=None):
    """Produce account-level scores and illustrative recommendations.

    Rule and graph signals can describe overlapping behavioural evidence.
    They are therefore consolidated with ``max`` rather than added together.
    The consolidated behavioural signal and relative ML anomaly percentile
    are then combined using configurable weights.
    """
    config = config or RiskAggregationConfig()
    rules_by_account = _index_results(rule_results, "rule")
    ml_by_account = _index_results(_records(ml_results), "ML")
    graph_accounts = graph_results.get("accounts", [])
    graph_by_account = _index_results(graph_accounts, "graph")
    anomaly_percentiles = _percentile_scores(ml_by_account)

    all_account_ids = sorted(
        set(rules_by_account) | set(ml_by_account) | set(graph_by_account)
    )
    weight_total = config.behavioral_weight + config.ml_weight
    behavioral_weight = config.behavioral_weight / weight_total
    ml_weight = config.ml_weight / weight_total
    accounts = []

    for account_id in all_account_ids:
        rule_result = rules_by_account.get(account_id, {})
        ml_result = ml_by_account.get(account_id, {})
        graph_result = graph_by_account.get(account_id, {})

        rule_score = _validate_score(
            rule_result.get("initial_risk_score", 0), account_id, "rule"
        )
        graph_indicators = graph_result.get("risk_indicators", [])
        graph_score = 100.0 if graph_indicators else 0.0
        anomaly_score = anomaly_percentiles.get(account_id, 0.0)

        behavioral_score = max(rule_score, graph_score)
        behavioral_contribution = behavioral_score * behavioral_weight
        ml_contribution = anomaly_score * ml_weight
        total_score = round(
            min(100.0, max(0.0, behavioral_contribution + ml_contribution)), 2
        )

        contributions = {
            "rule_based": {
                "raw_score": rule_score,
                "contribution": 0.0,
                "indicators": rule_result.get("risk_indicators", []),
                "explanation": "Included in the consolidated behavioural signal.",
            },
            "graph_based": {
                "raw_score": graph_score,
                "contribution": 0.0,
                "indicators": graph_indicators,
                "explanation": (
                    "Graph structure indicators are consolidated with rule "
                    "indicators to reduce double-counting."
                ),
            },
            "ml_anomaly": {
                "raw_score": anomaly_score,
                "source_anomaly_score": ml_result.get("anomaly_score"),
                "contribution": round(ml_contribution, 2),
                "indicators": [],
                "explanation": (
                    "Relative percentile of the IsolationForest anomaly score "
                    "among scored accounts; it is not a probability."
                ),
            },
        }

        if rule_score > graph_score:
            contributions["rule_based"]["contribution"] = round(
                behavioral_contribution, 2
            )
            contributions["rule_based"]["explanation"] = (
                "Rule-based score supplied the consolidated behavioural signal."
            )
        elif graph_score > rule_score:
            contributions["graph_based"]["contribution"] = round(
                behavioral_contribution, 2
            )
            contributions["graph_based"]["explanation"] = (
                "Graph indicators supplied the consolidated behavioural signal."
            )
        elif behavioral_score > 0:
            contributions["rule_based"]["contribution"] = round(
                behavioral_contribution / 2, 2
            )
            contributions["graph_based"]["contribution"] = round(
                behavioral_contribution - contributions["rule_based"]["contribution"],
                2,
            )
            contributions["rule_based"]["explanation"] = (
                "Rule-based and graph signals tied; the consolidated behavioural "
                "contribution is split between them."
            )
            contributions["graph_based"]["explanation"] = (
                "Rule-based and graph signals tied; the consolidated behavioural "
                "contribution is split between them."
            )
        else:
            contributions["rule_based"]["explanation"] = (
                "No rule-based indicators contributed to the behavioural signal."
            )
            contributions["graph_based"]["explanation"] = (
                "No graph indicators contributed to the behavioural signal."
            )

        risk_level = _risk_level(total_score, config)
        accounts.append(
            {
                "account_id": account_id,
                "risk_score": total_score,
                "risk_level": risk_level,
                "recommendation": _recommendation(risk_level),
                "method_contributions": contributions,
                "disclaimer": DISCLAIMER,
            }
        )

    return {
        "summary": {
            "account_count": len(accounts),
            "risk_level_counts": {
                level: sum(account["risk_level"] == level for account in accounts)
                for level in ("LOW", "MEDIUM", "HIGH")
            },
            "thresholds": {
                "low_max": config.low_risk_max,
                "medium_max": config.medium_risk_max,
            },
            "weights": {
                "behavioral": behavioral_weight,
                "ml_anomaly": ml_weight,
            },
            "disclaimer": DISCLAIMER,
        },
        "accounts": accounts,
    }


def _records(results):
    if hasattr(results, "to_dict"):
        return results.to_dict(orient="records")
    return results


def _index_results(results, source):
    indexed = {}
    for result in results:
        account_id = result.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            raise ValueError(f"Each {source} result must have a non-empty account_id")
        if account_id in indexed:
            raise ValueError(f"Duplicate {source} result for account_id {account_id}")
        indexed[account_id] = result
    return indexed


def _percentile_scores(ml_by_account):
    scored = []
    for account_id, result in ml_by_account.items():
        value = result.get("anomaly_score")
        if value is None:
            continue
        try:
            score = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invalid ML anomaly_score for account_id {account_id}"
            ) from error
        if not math.isfinite(score):
            raise ValueError(
                f"Invalid ML anomaly_score for account_id {account_id}: "
                "score must be finite"
            )
        scored.append((account_id, score))

    if not scored:
        return {}
    if len(scored) == 1:
        return {scored[0][0]: 50.0}

    ordered = sorted(scored, key=lambda item: item[1])
    percentile_by_account = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = (start + end - 1) / 2
        percentile = average_rank / (len(ordered) - 1) * 100
        for account_id, _ in ordered[start:end]:
            percentile_by_account[account_id] = percentile
        start = end
    return percentile_by_account


def _validate_score(value, account_id, source):
    try:
        score = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Invalid {source} score for account_id {account_id}"
        ) from error
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError(
            f"{source} score for account_id {account_id} must be between 0 and 100"
        )
    return score


def _risk_level(score, config):
    if score <= config.low_risk_max:
        return "LOW"
    if score <= config.medium_risk_max:
        return "MEDIUM"
    return "HIGH"


def _recommendation(risk_level):
    return {
        "LOW": "ALLOW",
        "MEDIUM": "REVIEW",
        "HIGH": "SIMULATED HOLD",
    }[risk_level]
