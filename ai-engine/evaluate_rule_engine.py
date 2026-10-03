"""Reproducible multi-seed evaluation for the complete synthetic pipeline."""

import argparse
import hashlib
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

from anomaly_detection import detect_account_anomalies
from data_loader import load_transactions
from risk_aggregation import aggregate_risk
from rule_engine import RuleEngineConfig, detect_accounts
from transaction_graph_analysis import analyze_transaction_graph


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_SOURCE = (
    PROJECT_ROOT / "java-engine" / "src" / "SyntheticTransactionGenerator.java"
)
DEFAULT_SEEDS = (101, 202, 303, 404, 505)
HIGH_VOLUME_NORMAL_MERCHANT_ID = "ACC-N-000504"
METHOD_NAMES = (
    "rule_based",
    "graph_based",
    "isolation_forest",
    "aggregated_risk",
)
METRIC_NAMES = (
    "precision",
    "recall",
    "f1_score",
    "false_positive_rate",
    "detection_count",
)
EVALUATION_ROLES = {
    "FOCAL_SUSPICIOUS",
    "SOURCE_PARTICIPANT",
    "DESTINATION_PARTICIPANT",
    "NORMAL",
}


def calculate_metrics(
    positive_accounts,
    predicted_positive_accounts,
    evaluated_accounts,
    positive_label,
    negative_label,
):
    """Calculate binary metrics with matrix rows actual, columns predicted."""
    evaluated_accounts = set(evaluated_accounts)
    positive_accounts = set(positive_accounts) & evaluated_accounts
    predicted_positive_accounts = set(predicted_positive_accounts) & evaluated_accounts
    negative_accounts = evaluated_accounts - positive_accounts

    true_positive = len(positive_accounts & predicted_positive_accounts)
    false_negative = len(positive_accounts - predicted_positive_accounts)
    false_positive = len(negative_accounts & predicted_positive_accounts)
    true_negative = len(negative_accounts - predicted_positive_accounts)

    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    f1_score = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    false_positive_rate = (
        false_positive / (false_positive + true_negative)
        if false_positive + true_negative
        else 0.0
    )

    return {
        "positive_class": positive_label,
        "negative_class": negative_label,
        "confusion_matrix": {
            "labels": [negative_label, positive_label],
            "rows_actual_columns_predicted": [
                [true_negative, false_positive],
                [false_negative, true_positive],
            ],
        },
        "precision": precision,
        "recall": recall,
        "f1_score": f1_score,
        "false_positive_rate": false_positive_rate,
        "detection_count": len(predicted_positive_accounts),
    }


def analyze_transactions(transactions):
    """Run every detector on the same data with evaluation columns removed."""
    detector_input = transactions.drop(
        columns=["scenario_label", "evaluation_role"], errors="ignore"
    ).copy()

    rule_results = detect_accounts(detector_input, RuleEngineConfig())
    graph_results = analyze_transaction_graph(detector_input)
    ml_results = detect_account_anomalies(detector_input)

    aggregation_rules = [
        {
            "account_id": account["account_id"],
            "initial_risk_score": account["risk_score"],
            "risk_indicators": account["reasons"],
        }
        for account in rule_results
    ]
    aggregated_results = aggregate_risk(
        aggregation_rules,
        ml_results,
        graph_results,
    )

    account_ids = {result["account_id"] for result in rule_results}
    predictions = {
        "rule_based": {
            result["account_id"]: (
                result["risk_score"] >= RuleEngineConfig().medium_risk_threshold
            )
            for result in rule_results
        },
        "graph_based": {
            result["account_id"]: bool(result.get("risk_indicators"))
            for result in graph_results["accounts"]
        },
        "isolation_forest": {
            result["account_id"]: bool(result["is_anomaly"])
            for result in _records(ml_results)
        },
        "aggregated_risk": {
            result["account_id"]: result["risk_level"] in {"MEDIUM", "HIGH"}
            for result in aggregated_results["accounts"]
        },
    }
    for method, method_predictions in predictions.items():
        if set(method_predictions) != account_ids:
            raise ValueError(
                f"{method} analyzed a different account set than the rule detector."
            )

    return {
        "account_count": len(account_ids),
        "predictions": predictions,
        "rule_results": rule_results,
        "graph_results": graph_results,
        "ml_results": _records(ml_results),
        "aggregated_results": aggregated_results["accounts"],
    }


def evaluate_transactions(transactions, role_manifest):
    """Evaluate outputs only after label-free analysis has finished."""
    analysis = analyze_transactions(transactions)
    roles = _validate_role_manifest(role_manifest, analysis["predictions"])
    evaluated_accounts = set(roles)
    focal_accounts = {
        account_id
        for account_id, role in roles.items()
        if role == "FOCAL_SUSPICIOUS"
    }
    participant_accounts = {
        account_id
        for account_id, role in roles.items()
        if role in {
            "FOCAL_SUSPICIOUS",
            "SOURCE_PARTICIPANT",
            "DESTINATION_PARTICIPANT",
        }
    }

    methods = {}
    for method_name, predictions in analysis["predictions"].items():
        predicted_positive = {
            account_id for account_id, predicted in predictions.items() if predicted
        }
        methods[method_name] = {
            "focal_account_evaluation": calculate_metrics(
                focal_accounts,
                predicted_positive,
                evaluated_accounts,
                "FOCAL_SUSPICIOUS",
                "NON_FOCAL",
            ),
            "participant_evaluation": calculate_metrics(
                participant_accounts,
                predicted_positive,
                evaluated_accounts,
                "SUSPICIOUS_PARTICIPANT",
                "NORMAL",
            ),
        }

    merchant_transactions = transactions.loc[
        transactions["receiver"].eq(HIGH_VOLUME_NORMAL_MERCHANT_ID)
        | transactions["sender"].eq(HIGH_VOLUME_NORMAL_MERCHANT_ID)
    ]
    if (
        len(merchant_transactions) != 60
        or roles.get(HIGH_VOLUME_NORMAL_MERCHANT_ID) != "NORMAL"
    ):
        raise ValueError(
            "Generated evaluation dataset is missing the expected "
            "high-volume NORMAL merchant account."
        )

    rule_result = _find_by_account(
        analysis["rule_results"], HIGH_VOLUME_NORMAL_MERCHANT_ID
    )
    graph_result = _find_by_account(
        analysis["graph_results"]["accounts"], HIGH_VOLUME_NORMAL_MERCHANT_ID
    )
    ml_result = _find_by_account(
        analysis["ml_results"], HIGH_VOLUME_NORMAL_MERCHANT_ID
    )
    aggregate_result = _find_by_account(
        analysis["aggregated_results"], HIGH_VOLUME_NORMAL_MERCHANT_ID
    )
    merchant_predictions = {
        method: values[HIGH_VOLUME_NORMAL_MERCHANT_ID]
        for method, values in analysis["predictions"].items()
    }

    role_counts = {
        role: sum(value == role for value in roles.values())
        for role in (
            "FOCAL_SUSPICIOUS",
            "SOURCE_PARTICIPANT",
            "DESTINATION_PARTICIPANT",
            "NORMAL",
        )
    }
    return {
        "transaction_count": len(transactions),
        "account_count": analysis["account_count"],
        "role_counts": role_counts,
        "methods": methods,
        # Keep the original rule-only result keys for existing report consumers.
        "focal_account_evaluation": methods["rule_based"][
            "focal_account_evaluation"
        ],
        "participant_evaluation": methods["rule_based"]["participant_evaluation"],
        "high_volume_normal_merchant": {
            "account_id": HIGH_VOLUME_NORMAL_MERCHANT_ID,
            "transaction_count": len(merchant_transactions),
            "risk_score": rule_result["risk_score"],
            "detected": merchant_predictions["rule_based"],
            "detections_by_method": merchant_predictions,
            "scores": {
                "rule_based": rule_result["risk_score"],
                "graph_indicator_count": len(graph_result.get("risk_indicators", [])),
                "isolation_forest_anomaly_score": ml_result["anomaly_score"],
                "aggregated_risk": aggregate_result["risk_score"],
            },
        },
    }


def evaluate_seeds(seeds=DEFAULT_SEEDS):
    """Generate each requested seed in a temp directory and summarize results."""
    seeds = tuple(seeds)
    if not seeds:
        raise ValueError("At least one evaluation seed is required.")
    if len(set(seeds)) != len(seeds):
        raise ValueError("Evaluation seeds must be unique.")
    if any(not isinstance(seed, int) or isinstance(seed, bool) for seed in seeds):
        raise ValueError("Evaluation seeds must be integers.")

    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        raise RuntimeError(
            "Java 17 or later (javac and java) is required to generate "
            "independent evaluation datasets."
        )

    with tempfile.TemporaryDirectory(prefix="muleguard-pipeline-eval-") as temporary:
        temporary_path = Path(temporary)
        classes = temporary_path / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [javac, "--release", "17", "-d", str(classes), str(GENERATOR_SOURCE)],
            capture_output=True,
            text=True,
            check=False,
        )
        if compile_result.returncode:
            raise RuntimeError(
                "Could not compile the synthetic transaction generator: "
                + compile_result.stderr.strip()
            )

        per_seed = []
        for seed in seeds:
            seed_directory = temporary_path / f"seed-{seed}"
            seed_directory.mkdir()
            transactions_path = seed_directory / "transactions.csv"
            generation = subprocess.run(
                [
                    java,
                    "-cp",
                    str(classes),
                    "SyntheticTransactionGenerator",
                    str(transactions_path),
                    str(seed),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if generation.returncode:
                raise RuntimeError(
                    f"Could not generate dataset for seed {seed}: "
                    + generation.stderr.strip()
                )

            transactions = load_transactions(transactions_path)
            role_path = seed_directory / "evaluation_account_roles.csv"
            role_manifest = _load_role_manifest(role_path)
            per_seed.append(
                {
                    "seed": seed,
                    "transaction_sha256": hashlib.sha256(
                        transactions_path.read_bytes()
                    ).hexdigest(),
                    "role_manifest_sha256": hashlib.sha256(
                        role_path.read_bytes()
                    ).hexdigest(),
                    **evaluate_transactions(transactions, role_manifest),
                }
            )

    summaries_by_method = {
        method: {
            evaluation_name: _summarize(per_seed, method, evaluation_name)
            for evaluation_name in (
                "focal_account_evaluation",
                "participant_evaluation",
            )
        }
        for method in METHOD_NAMES
    }
    return {
        "detector": "canonical rule_engine.detect_accounts, transaction_graph_analysis, "
        "IsolationForest, and risk_aggregation",
        "ground_truth_source": "evaluation-only account-role manifest",
        "scenario_label_used_for_detection": False,
        "evaluation_role_used_for_detection": False,
        "prediction_policies": {
            "rule_based": "risk_score >= RuleEngineConfig.medium_risk_threshold",
            "graph_based": "one or more graph risk indicators",
            "isolation_forest": "is_anomaly is true using the configured "
            "IsolationForest (random_state=42, contamination=0.1)",
            "aggregated_risk": "risk_level is MEDIUM or HIGH under the existing "
            "risk-aggregation thresholds",
        },
        "confusion_matrix_convention": (
            "Focal positive=FOCAL_SUSPICIOUS, negative=NON_FOCAL. Participant "
            "positive=FOCAL_SUSPICIOUS, SOURCE_PARTICIPANT, or "
            "DESTINATION_PARTICIPANT; negative=NORMAL. Labels are [negative, "
            "positive]; matrix rows are actual and columns predicted: "
            "[[TN, FP], [FN, TP]]."
        ),
        "seeds": list(seeds),
        "per_seed": per_seed,
        "summary": {
            "by_method": summaries_by_method,
            # Maintain the former rule-only summary keys.
            "focal_account_evaluation": summaries_by_method["rule_based"][
                "focal_account_evaluation"
            ],
            "participant_evaluation": summaries_by_method["rule_based"][
                "participant_evaluation"
            ],
        },
        "limitations": [
            "Results measure this synthetic generator and illustrative detectors only.",
            "They do not estimate performance on real financial data or in banking.",
            "The normal merchant is a controlled high-volume, low-counterparty "
            "scenario; it does not represent all legitimate merchant behaviour.",
            "Graph positives mean at least one current graph indicator; this is an "
            "evaluation policy, not a calibrated probability threshold.",
            "IsolationForest positives follow its configured contamination setting "
            "and are not calibrated probabilities.",
        ],
    }


def _load_role_manifest(path):
    import pandas as pd

    return pd.read_csv(path)


def _validate_role_manifest(role_manifest, predictions):
    if hasattr(role_manifest, "to_dict"):
        records = role_manifest.to_dict(orient="records")
    else:
        records = role_manifest
    roles = {}
    for record in records:
        account_id = record.get("account_id")
        role = record.get("evaluation_role")
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("Role manifest account_id values must be non-empty.")
        if role not in EVALUATION_ROLES:
            raise ValueError(f"Invalid evaluation role for {account_id}: {role}")
        if account_id in roles:
            raise ValueError(f"Duplicate evaluation role for account_id {account_id}")
        roles[account_id] = role
    if set(roles) != set(predictions["rule_based"]):
        raise ValueError("Role manifest accounts do not match detector accounts.")
    return roles


def _records(results):
    if hasattr(results, "to_dict"):
        return results.to_dict(orient="records")
    return results or []


def _find_by_account(results, account_id):
    for result in results:
        if result["account_id"] == account_id:
            return result
    raise ValueError(f"Analysis output is missing account_id {account_id}")


def _summarize(per_seed, method_name, evaluation_name):
    runs = [result["methods"][method_name][evaluation_name] for result in per_seed]
    summary = {}
    for metric_name in METRIC_NAMES:
        values = [float(run[metric_name]) for run in runs]
        summary[metric_name] = {
            "mean": statistics.mean(values),
            "standard_deviation": (
                statistics.stdev(values) if len(values) > 1 else 0.0
            ),
        }
    return summary


def print_report(report):
    print(report["detector"])
    print(report["confusion_matrix_convention"])
    print("Scenario and account-role labels are excluded from detection.")
    print("Positive-prediction policies:")
    for method, policy in report["prediction_policies"].items():
        print(f"  {method}: {policy}")
    for result in report["per_seed"]:
        print(
            f"\nSeed {result['seed']}: {result['transaction_count']} transactions, "
            f"{result['account_count']} accounts, roles={result['role_counts']}"
        )
        merchant = result["high_volume_normal_merchant"]
        print(
            "  High-volume NORMAL merchant: "
            f"{merchant['account_id']}, {merchant['transaction_count']} "
            f"transactions, detections={merchant['detections_by_method']}"
        )
        for method, evaluations in result["methods"].items():
            for name, metrics in evaluations.items():
                print(
                    f"  {method}/{name}: precision={metrics['precision']:.3f}, "
                    f"recall={metrics['recall']:.3f}, "
                    f"F1={metrics['f1_score']:.3f}, "
                    f"FPR={metrics['false_positive_rate']:.3f}, "
                    f"detections={metrics['detection_count']}, "
                    "confusion_matrix="
                    f"{metrics['confusion_matrix']['rows_actual_columns_predicted']}"
                )
    print("\nAcross-seed mean (standard deviation):")
    for method, evaluations in report["summary"]["by_method"].items():
        print(f"  {method}:")
        for evaluation_name, metric_summary in evaluations.items():
            print(f"    {evaluation_name}:")
            for metric_name, values in metric_summary.items():
                mean = values["mean"]
                deviation = values["standard_deviation"]
                if metric_name == "detection_count":
                    print(f"      {metric_name}: {mean:.2f} ({deviation:.2f})")
                else:
                    print(f"      {metric_name}: {mean:.3f} ({deviation:.3f})")
    for limitation in report["limitations"]:
        print(f"LIMITATION: {limitation}")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate the complete MuleGuard synthetic pipeline across seeds."
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=list(DEFAULT_SEEDS),
        help="Distinct Java Random seeds (default: 101 202 303 404 505).",
    )
    arguments = parser.parse_args()
    try:
        print_report(evaluate_seeds(arguments.seeds))
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
