import unittest
from unittest.mock import patch

import pandas as pd

import synthetic_streaming
from anomaly_detection import build_account_features
from benchmark_synthetic_replay import build_benchmark_transactions
from synthetic_streaming import PROTOCOL, run_synthetic_stream


class SyntheticStreamingTests(unittest.TestCase):
    def test_warmup_is_not_scored_and_training_uses_only_warmup_prefix(self):
        transactions = self._transactions(6)
        original_fit = synthetic_streaming._fit_stream_model
        captured = {}

        def capture_fit(warmup, seed):
            captured["transaction_ids"] = warmup["transaction_id"].tolist()
            captured["seed"] = seed
            return original_fit(warmup, seed)

        with patch(
            "synthetic_streaming._fit_stream_model",
            side_effect=capture_fit,
        ) as fit:
            result = run_synthetic_stream(
                transactions,
                warmup_events=3,
                seed=17,
            )

        self.assertEqual(fit.call_count, 1)
        self.assertEqual(captured["transaction_ids"], ["TX-0", "TX-1", "TX-2"])
        self.assertEqual(captured["seed"], 17)
        self.assertEqual(result["protocol"], PROTOCOL)
        self.assertEqual(result["model_protocol"]["protocol"], PROTOCOL)
        self.assertEqual(result["model_protocol"]["training_seed"], 17)
        self.assertFalse(result["model_protocol"]["training_labels_used"])
        self.assertTrue(result["model_protocol"]["model_frozen_after_warmup"])
        self.assertEqual(
            [step["phase"] for step in result["steps"]],
            ["warmup", "warmup", "warmup", "scored", "scored", "scored"],
        )
        self.assertTrue(
            all(not step["detection_enabled"] for step in result["steps"][:3])
        )
        self.assertTrue(
            all(not step["risk_scores"] for step in result["steps"][:3])
        )
        self.assertTrue(
            all(
                0 <= score <= 100
                for step in result["steps"]
                for score in step["risk_scores"].values()
            )
        )
        for account in result["accounts"].values():
            if account["first_alert_explanation"] is not None:
                self.assertEqual(
                    set(account["first_alert_explanation"]["signals"]),
                    {"rule_based", "graph_based", "ml_anomaly"},
                )

    def test_model_is_fitted_once_and_only_scored_after_warmup(self):
        from sklearn.pipeline import Pipeline

        transactions = self._transactions(6)
        original_fit = Pipeline.fit
        fit_calls = []

        def count_fit(model, *args, **kwargs):
            fit_calls.append(len(args[0]))
            return original_fit(model, *args, **kwargs)

        with patch.object(Pipeline, "fit", count_fit):
            result = run_synthetic_stream(transactions, warmup_events=3, seed=9)

        self.assertEqual(
            fit_calls,
            [result["model_protocol"]["training_account_count"]],
        )
        self.assertGreaterEqual(fit_calls[0], 2)
        self.assertEqual(result["scored_event_count"], 3)

    def test_first_alert_metadata_uses_first_scored_event(self):
        transactions = self._transactions(5)

        def force_alerts(rule_results, ml_results, graph_results):
            return {
                "accounts": [
                    {
                        "account_id": rule["account_id"],
                        "risk_score": 75.0,
                        "risk_level": "HIGH",
                        "method_contributions": {
                            signal: {"explanation": f"{signal} contributed"}
                            for signal in (
                                "rule_based",
                                "graph_based",
                                "ml_anomaly",
                            )
                        },
                    }
                    for rule in rule_results
                ]
            }

        with patch(
            "synthetic_streaming.aggregate_risk",
            side_effect=force_alerts,
        ):
            result = run_synthetic_stream(
                transactions,
                warmup_events=2,
                seed=21,
            )

        expected_timestamp = pd.Timestamp(
            transactions.loc[
                transactions["transaction_id"].eq("TX-2"), "timestamp"
            ].iloc[0]
        ).isoformat()
        self.assertTrue(result["alerted_accounts"])
        for account in result["accounts"].values():
            self.assertEqual(account["protocol"], PROTOCOL)
            self.assertEqual(account["first_alert_timestamp"], expected_timestamp)
            self.assertEqual(account["transaction_count_at_first_alert"], 3)
            self.assertIsNotNone(account["first_alert_explanation"])

    def test_future_event_mutations_do_not_change_earlier_results(self):
        transactions = self._transactions(8)
        initial = run_synthetic_stream(
            transactions,
            warmup_events=3,
            seed=51,
        )
        changed = transactions.copy()
        changed.loc[changed["transaction_id"].isin(["TX-6", "TX-7"]), "amount_paise"] *= 9
        changed_result = run_synthetic_stream(
            changed,
            warmup_events=3,
            seed=51,
        )

        self.assertEqual(
            initial["steps"][:6],
            changed_result["steps"][:6],
        )

    def test_evaluation_labels_never_change_stream_predictions(self):
        transactions = self._transactions(8)
        first = run_synthetic_stream(transactions, warmup_events=3, seed=71)
        relabeled = transactions.assign(
            scenario_label="NORMAL",
            evaluation_role="DESTINATION_PARTICIPANT",
        )
        second = run_synthetic_stream(relabeled, warmup_events=3, seed=71)

        self.assertEqual(first, second)

    def test_timestamp_ties_keep_input_order_and_repeat_deterministically(self):
        transactions = self._transactions(6)
        transactions.loc[transactions["transaction_id"].isin(["TX-0", "TX-1"]), "timestamp"] = (
            "2025-01-01T00:00:00Z"
        )
        first = run_synthetic_stream(transactions, warmup_events=3, seed=10)
        second = run_synthetic_stream(transactions, warmup_events=3, seed=10)

        self.assertEqual(first, second)
        self.assertEqual(
            [step["transaction_id"] for step in first["steps"][:3]],
            ["TX-0", "TX-1", "TX-2"],
        )

    def test_declined_transactions_remain_nodes_but_do_not_change_success_features(self):
        transactions = self._transactions(4)
        transactions.loc[transactions["transaction_id"].eq("TX-3"), "status"] = (
            "DECLINED"
        )
        ordered = synthetic_streaming._prepare_ordered(
            transactions.drop(columns=["scenario_label", "evaluation_role"])
        )
        streaming_features = synthetic_streaming._build_incremental_features(ordered)
        batch_features = build_account_features(ordered)
        pd.testing.assert_frame_equal(
            streaming_features.reset_index(drop=True),
            batch_features.reset_index(drop=True),
        )

        graph = synthetic_streaming.analyze_transaction_graph(ordered)
        self.assertEqual(
            graph["summary"]["excluded_declined_transaction_count"],
            1,
        )

    def test_stream_features_match_existing_feature_builder_for_each_prefix(self):
        transactions = self._transactions(8).drop(
            columns=["scenario_label", "evaluation_role"]
        )
        ordered = synthetic_streaming._prepare_ordered(transactions)

        for end in range(1, len(ordered) + 1):
            prefix = ordered.iloc[:end]
            actual = synthetic_streaming._build_incremental_features(prefix)
            expected = build_account_features(prefix)
            actual = actual.sort_values("account_id").reset_index(drop=True)
            expected = expected.sort_values("account_id").reset_index(drop=True)
            pd.testing.assert_frame_equal(actual, expected)

    def test_existing_rule_and_graph_components_receive_only_point_in_time_prefix(self):
        transactions = self._transactions(6)
        rule_prefix_sizes = []
        graph_prefix_sizes = []
        original_rules = synthetic_streaming.analyze_accounts
        original_graph = synthetic_streaming.analyze_transaction_graph

        def inspect_rules(prefix):
            self.assertNotIn("scenario_label", prefix.columns)
            self.assertNotIn("evaluation_role", prefix.columns)
            rule_prefix_sizes.append(len(prefix))
            return original_rules(prefix)

        def inspect_graph(prefix):
            self.assertNotIn("scenario_label", prefix.columns)
            self.assertNotIn("evaluation_role", prefix.columns)
            graph_prefix_sizes.append(len(prefix))
            return original_graph(prefix)

        with (
            patch("synthetic_streaming.analyze_accounts", side_effect=inspect_rules),
            patch(
                "synthetic_streaming.analyze_transaction_graph",
                side_effect=inspect_graph,
            ),
        ):
            result = run_synthetic_stream(transactions, warmup_events=2, seed=30)

        self.assertEqual(rule_prefix_sizes, [3, 4, 5, 6])
        self.assertEqual(graph_prefix_sizes, [3, 4, 5, 6])
        self.assertTrue(all(step["protocol"] == PROTOCOL for step in result["steps"]))

    def test_streaming_and_strict_protocols_are_explicitly_distinguished(self):
        result = run_synthetic_stream(
            build_benchmark_transactions(6),
            warmup_events=2,
            seed=5,
        )

        self.assertEqual(result["protocol"], "stream_frozen_model")
        self.assertNotEqual(result["protocol"], "strict_prefix_refit")
        self.assertTrue(result["limitations"])

    def test_rejects_invalid_warmup_and_empty_input_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "less than the transaction count"):
            run_synthetic_stream(self._transactions(3), warmup_events=3)
        with self.assertRaisesRegex(ValueError, "at least 1"):
            run_synthetic_stream(self._transactions(3), warmup_events=0)

        empty = run_synthetic_stream(pd.DataFrame(), warmup_events=1)
        self.assertEqual(empty["protocol"], PROTOCOL)
        self.assertEqual(empty["steps"], [])
        self.assertEqual(empty["scored_event_count"], 0)

    def test_training_requires_two_accounts(self):
        transactions = self._transactions(4)
        transactions["sender"] = "ACC-ONLY"
        transactions["receiver"] = "ACC-ONLY"

        with self.assertRaisesRegex(ValueError, "at least two accounts"):
            run_synthetic_stream(transactions, warmup_events=2)

    @staticmethod
    def _transactions(count):
        rows = []
        start = pd.Timestamp("2025-01-01T00:00:00Z")
        for index in range(count):
            rows.append(
                {
                    "transaction_id": f"TX-{index}",
                    "sender": f"ACC-{index % 4}",
                    "receiver": f"ACC-{(index + 1) % 4}",
                    "amount_paise": 1_000 + 100 * index,
                    "timestamp": start + pd.Timedelta(minutes=index),
                    "status": "DECLINED" if index == 4 else "SUCCESS",
                    "scenario_label": "NORMAL" if index % 2 else "SYNTHETIC_SUSPICIOUS",
                    "evaluation_role": "NORMAL" if index % 2 else "FOCAL_SUSPICIOUS",
                }
            )
        return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
