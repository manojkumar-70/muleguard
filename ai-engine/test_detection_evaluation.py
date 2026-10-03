import statistics
import unittest

import pandas as pd

from evaluate_rule_engine import analyze_transactions, calculate_metrics, evaluate_seeds
from rule_engine import evaluate_results


class DetectionEvaluationTests(unittest.TestCase):
    def test_seed_runs_are_reproducible_and_independent(self):
        first = evaluate_seeds((17, 29))
        repeated = evaluate_seeds((17, 29))

        self.assertEqual(first, repeated)
        self.assertNotEqual(
            first["per_seed"][0]["transaction_sha256"],
            first["per_seed"][1]["transaction_sha256"],
        )
        self.assertEqual(first["seeds"], [17, 29])
        self.assertFalse(first["scenario_label_used_for_detection"])
        self.assertFalse(first["evaluation_role_used_for_detection"])
        for result in first["per_seed"]:
            self.assertEqual(result["transaction_count"], 1200)
            self.assertEqual(
                result["high_volume_normal_merchant"]["transaction_count"], 60
            )
            self.assertEqual(
                result["role_counts"]["FOCAL_SUSPICIOUS"], 20
            )
            self.assertEqual(result["role_counts"]["SOURCE_PARTICIPANT"], 160)
            self.assertEqual(result["role_counts"]["DESTINATION_PARTICIPANT"], 40)
            self.assertFalse(result["high_volume_normal_merchant"]["detected"])
            self.assertEqual(
                set(result["methods"]),
                {"rule_based", "graph_based", "isolation_forest", "aggregated_risk"},
            )
            for method_results in result["methods"].values():
                for metrics in method_results.values():
                    self.assertEqual(
                        metrics["confusion_matrix"]["labels"],
                        [metrics["negative_class"], metrics["positive_class"]],
                    )
            self.assertEqual(
                set(result["high_volume_normal_merchant"]["detections_by_method"]),
                {"rule_based", "graph_based", "isolation_forest", "aggregated_risk"},
            )
        focal_precision = [
            result["focal_account_evaluation"]["precision"]
            for result in first["per_seed"]
        ]
        self.assertAlmostEqual(
            first["summary"]["focal_account_evaluation"]["precision"]["mean"],
            statistics.mean(focal_precision),
        )
        self.assertAlmostEqual(
            first["summary"]["focal_account_evaluation"]["precision"][
                "standard_deviation"
            ],
            statistics.stdev(focal_precision),
        )

    def test_evaluation_labels_do_not_change_detector_scores(self):
        transactions = self._transactions()
        expected = analyze_transactions(transactions)
        relabeled = transactions.assign(
            scenario_label="NORMAL", evaluation_role="FOCAL_SUSPICIOUS"
        )

        actual = analyze_transactions(relabeled)
        for output_name in (
            "predictions",
            "rule_results",
            "graph_results",
            "ml_results",
            "aggregated_results",
        ):
            self.assertEqual(expected[output_name], actual[output_name])

    def test_metric_calculation_uses_actual_rows_and_predicted_columns(self):
        metrics = calculate_metrics(
            positive_accounts={"A", "B"},
            predicted_positive_accounts={"A", "C", "E"},
            evaluated_accounts={"A", "B", "C", "D", "E", "F"},
            positive_label="FOCAL_SUSPICIOUS",
            negative_label="NON_FOCAL",
        )

        self.assertEqual(
            metrics["confusion_matrix"]["rows_actual_columns_predicted"],
            [[2, 2], [1, 1]],
        )
        self.assertAlmostEqual(metrics["precision"], 1 / 3)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["f1_score"], 0.4)
        self.assertEqual(metrics["false_positive_rate"], 0.5)
        self.assertEqual(metrics["detection_count"], 3)

    def test_aggregated_scores_remain_bounded_and_explain_signals(self):
        analysis = analyze_transactions(self._transactions())

        for account in analysis["aggregated_results"]:
            self.assertGreaterEqual(account["risk_score"], 0)
            self.assertLessEqual(account["risk_score"], 100)
            self.assertEqual(
                set(account["method_contributions"]),
                {"rule_based", "graph_based", "ml_anomaly"},
            )

    def test_metrics_and_confusion_matrix_order_are_correct(self):
        detections = [
            {"account_id": "A", "risk_score": 30},
            {"account_id": "B", "risk_score": 0},
            {"account_id": "C", "risk_score": 30},
            {"account_id": "D", "risk_score": 0},
            {"account_id": "E", "risk_score": 30},
            {"account_id": "F", "risk_score": 0},
        ]
        roles = pd.DataFrame(
            [
                {"account_id": "A", "evaluation_role": "FOCAL_SUSPICIOUS"},
                {"account_id": "B", "evaluation_role": "FOCAL_SUSPICIOUS"},
                {"account_id": "C", "evaluation_role": "SOURCE_PARTICIPANT"},
                {"account_id": "D", "evaluation_role": "DESTINATION_PARTICIPANT"},
                {"account_id": "E", "evaluation_role": "NORMAL"},
                {"account_id": "F", "evaluation_role": "NORMAL"},
            ]
        )

        evaluation = evaluate_results(detections, role_manifest=roles)
        focal = evaluation["focal_account_evaluation"]
        participant = evaluation["participant_evaluation"]

        self.assertEqual(focal["positive_class"], "FOCAL_SUSPICIOUS")
        self.assertEqual(focal["negative_class"], "NON_FOCAL")
        self.assertEqual(
            focal["confusion_matrix"]["labels"],
            ["NON_FOCAL", "FOCAL_SUSPICIOUS"],
        )
        self.assertEqual(
            focal["confusion_matrix"]["rows_actual_columns_predicted"],
            [[2, 2], [1, 1]],
        )
        self.assertAlmostEqual(focal["precision"], 1 / 3)
        self.assertEqual(focal["recall"], 0.5)
        self.assertEqual(focal["f1_score"], 0.4)
        self.assertEqual(focal["false_positive_rate"], 0.5)
        self.assertEqual(focal["detection_count"], 3)

        self.assertEqual(participant["positive_class"], "SUSPICIOUS_PARTICIPANT")
        self.assertEqual(
            participant["confusion_matrix"]["rows_actual_columns_predicted"],
            [[1, 1], [2, 2]],
        )
        self.assertAlmostEqual(participant["precision"], 2 / 3)
        self.assertEqual(participant["recall"], 0.5)
        self.assertAlmostEqual(participant["f1_score"], 4 / 7)
        self.assertEqual(participant["false_positive_rate"], 0.5)
        self.assertEqual(participant["detection_count"], 3)

    def test_duplicate_or_empty_seed_lists_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "At least one"):
            evaluate_seeds(())
        with self.assertRaisesRegex(ValueError, "unique"):
            evaluate_seeds((17, 17))

    @staticmethod
    def _transactions():
        rows = []
        start = pd.Timestamp("2025-01-01T12:00:00Z")
        for index in range(8):
            rows.append(
                {
                    "transaction_id": f"TX-{index:03}",
                    "sender": f"ACC-SOURCE-{index:03}",
                    "receiver": "ACC-MULE-001",
                    "amount_paise": 500_000,
                    "timestamp": start + pd.Timedelta(minutes=index),
                    "status": "SUCCESS",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                    "evaluation_role": "SOURCE_PARTICIPANT",
                }
            )
        for index in range(2):
            rows.append(
                {
                    "transaction_id": f"TX-{8 + index:03}",
                    "sender": "ACC-MULE-001",
                    "receiver": f"ACC-DESTINATION-{index:03}",
                    "amount_paise": 200_000,
                    "timestamp": start + pd.Timedelta(minutes=8 + index),
                    "status": "SUCCESS",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                    "evaluation_role": "DESTINATION_PARTICIPANT",
                }
            )
        return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
