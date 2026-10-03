import unittest

from risk_aggregation import (
    DISCLAIMER,
    RiskAggregationConfig,
    aggregate_risk,
)


class RiskAggregationTests(unittest.TestCase):
    def test_normal_account_receives_allow_recommendation(self):
        results = aggregate_risk(
            rule_results=[
                {
                    "account_id": "ACC-NORMAL",
                    "initial_risk_score": 0,
                    "risk_indicators": [],
                }
            ],
            ml_results=[
                {"account_id": "ACC-NORMAL", "anomaly_score": -0.2},
                {"account_id": "ACC-OTHER", "anomaly_score": 0.1},
            ],
            graph_results={
                "accounts": [
                    {"account_id": "ACC-NORMAL", "risk_indicators": []}
                ]
            },
        )
        account = self._account_result(results, "ACC-NORMAL")
        self.assertEqual(account["risk_score"], 0)
        self.assertEqual(account["risk_level"], "LOW")
        self.assertEqual(account["recommendation"], "ALLOW")
        self.assertIn("not proof", account["disclaimer"])

    def test_synthetic_suspicious_signals_are_explained_and_correlated_inputs_capped(self):
        results = aggregate_risk(
            rule_results=[
                {
                    "account_id": "ACC-SYNTHETIC",
                    "initial_risk_score": 80,
                    "risk_indicators": [
                        {
                            "rule": "flow_imbalance",
                            "explanation": "Incoming value exceeds outgoing value.",
                            "points": 25,
                        }
                    ],
                }
            ],
            ml_results=[
                {"account_id": "ACC-TYPICAL", "anomaly_score": -0.2},
                {"account_id": "ACC-SYNTHETIC", "anomaly_score": 0.2},
            ],
            graph_results={
                "accounts": [
                    {
                        "account_id": "ACC-SYNTHETIC",
                        "risk_indicators": [
                            {
                                "rule": "fan_in_then_fan_out",
                                "explanation": "Several incoming and outgoing accounts.",
                            },
                            {
                                "rule": "short_window_activity_concentration",
                                "explanation": "Activity is concentrated in a short window.",
                            },
                        ],
                    }
                ]
            },
        )
        account = self._account_result(results, "ACC-SYNTHETIC")
        contributions = account["method_contributions"]

        self.assertEqual(account["risk_score"], 86)
        self.assertEqual(account["risk_level"], "HIGH")
        self.assertEqual(account["recommendation"], "SIMULATED HOLD")
        self.assertEqual(contributions["rule_based"]["raw_score"], 80)
        self.assertEqual(contributions["graph_based"]["raw_score"], 40)
        self.assertEqual(contributions["graph_based"]["contribution"], 0)
        self.assertEqual(contributions["rule_based"]["contribution"], 56)
        self.assertEqual(contributions["ml_anomaly"]["contribution"], 30)
        self.assertEqual(contributions["graph_based"]["distinct_indicator_count"], 2)
        self.assertIn(
            "several incoming",
            contributions["graph_based"]["indicators"][0]["explanation"].lower(),
        )
        self.assertIn("2 distinct graph indicator", contributions["graph_based"]["explanation"])
        self.assertEqual(results["summary"]["risk_level_counts"]["HIGH"], 1)
        self.assertIn("SIMULATED", DISCLAIMER)

    def test_rule_and_graph_signals_are_not_added_together(self):
        results = aggregate_risk(
            rule_results=[
                {
                    "account_id": "ACC-CORRELATED",
                    "initial_risk_score": 20,
                    "risk_indicators": [{"rule": "rule"}],
                }
            ],
            ml_results=[],
            graph_results={
                "accounts": [
                    {
                        "account_id": "ACC-CORRELATED",
                        "risk_indicators": [
                            {"rule": "graph"},
                            {"rule": "graph"},
                        ],
                    }
                ]
            },
        )
        account = self._account_result(results, "ACC-CORRELATED")
        self.assertEqual(account["risk_score"], 14)
        self.assertEqual(
            account["method_contributions"]["graph_based"]["raw_score"], 20
        )
        self.assertEqual(
            account["method_contributions"]["graph_based"]["distinct_indicator_count"],
            1,
        )
        self.assertEqual(
            account["method_contributions"]["rule_based"]["contribution"], 7
        )
        self.assertEqual(
            account["method_contributions"]["graph_based"]["contribution"], 7
        )

    def test_one_graph_indicator_alone_is_bounded(self):
        results = aggregate_risk(
            rule_results=[],
            ml_results=[],
            graph_results={
                "accounts": [
                    {
                        "account_id": "ACC-GRAPH",
                        "risk_indicators": [{"rule": "fan_in_then_fan_out"}],
                    }
                ]
            },
        )
        account = self._account_result(results, "ACC-GRAPH")
        self.assertEqual(account["method_contributions"]["graph_based"]["raw_score"], 20)
        self.assertEqual(account["method_contributions"]["graph_based"]["contribution"], 14)
        self.assertEqual(account["risk_score"], 14)
        self.assertEqual(account["risk_level"], "LOW")

    def test_multiple_distinct_graph_indicators_are_capped(self):
        results = aggregate_risk(
            rule_results=[],
            ml_results=[],
            graph_results={
                "accounts": [
                    {
                        "account_id": "ACC-GRAPH",
                        "risk_indicators": [
                            {"rule": "fan_in_then_fan_out"},
                            {"rule": "short_window_activity_concentration"},
                            {"rule": "third_distinct_indicator"},
                            {"rule": "fourth_distinct_indicator"},
                        ],
                    }
                ]
            },
        )
        graph = self._account_result(results, "ACC-GRAPH")["method_contributions"][
            "graph_based"
        ]
        self.assertEqual(graph["distinct_indicator_count"], 4)
        self.assertEqual(graph["raw_score"], 60)
        self.assertIn("capped at 60.0", graph["explanation"])

    def test_high_rule_score_with_low_ml_percentile(self):
        results = aggregate_risk(
            rule_results=[
                {
                    "account_id": "ACC-RULE",
                    "initial_risk_score": 80,
                    "risk_indicators": [{"rule": "movement"}],
                }
            ],
            ml_results=[
                {"account_id": "ACC-RULE", "anomaly_score": -0.5},
                {"account_id": "ACC-BASELINE", "anomaly_score": 0.5},
            ],
            graph_results={"accounts": []},
        )
        account = self._account_result(results, "ACC-RULE")
        self.assertEqual(account["method_contributions"]["ml_anomaly"]["raw_score"], 0)
        self.assertEqual(account["risk_score"], 56)

    def test_high_ml_percentile_with_low_rule_score(self):
        results = aggregate_risk(
            rule_results=[
                {
                    "account_id": "ACC-ML",
                    "initial_risk_score": 0,
                    "risk_indicators": [],
                }
            ],
            ml_results=[
                {"account_id": "ACC-ML", "anomaly_score": 0.5},
                {"account_id": "ACC-BASELINE", "anomaly_score": -0.5},
            ],
            graph_results={"accounts": []},
        )
        account = self._account_result(results, "ACC-ML")
        self.assertEqual(account["method_contributions"]["ml_anomaly"]["raw_score"], 100)
        self.assertEqual(account["risk_score"], 30)

    def test_missing_or_empty_graph_results_contribute_zero(self):
        for graph_results in (None, {}, {"accounts": []}):
            with self.subTest(graph_results=graph_results):
                results = aggregate_risk(
                    rule_results=[
                        {
                            "account_id": "ACC-RULE",
                            "initial_risk_score": 40,
                            "risk_indicators": [{"rule": "movement"}],
                        }
                    ],
                    ml_results=[],
                    graph_results=graph_results,
                )
                account = self._account_result(results, "ACC-RULE")
                self.assertEqual(
                    account["method_contributions"]["graph_based"]["raw_score"], 0
                )
                self.assertEqual(
                    account["method_contributions"]["graph_based"]["contribution"], 0
                )
                self.assertEqual(account["risk_score"], 28)

    def test_missing_rule_and_ml_outputs_are_safe_and_explained(self):
        self.assertEqual(aggregate_risk(None, None, None)["accounts"], [])

        results = aggregate_risk(
            [
                {
                    "account_id": "ACC-RULE",
                    "initial_risk_score": 40,
                    "risk_indicators": [{"rule": "movement"}],
                }
            ],
            None,
            None,
        )
        account = self._account_result(results, "ACC-RULE")
        self.assertEqual(account["risk_score"], 28)
        contributions = account["method_contributions"]
        self.assertEqual(contributions["ml_anomaly"]["contribution"], 0)
        self.assertIn("No ML anomaly score", contributions["ml_anomaly"]["explanation"])
        self.assertEqual(contributions["graph_based"]["contribution"], 0)
        self.assertIn("No graph indicators", contributions["graph_based"]["explanation"])

    def test_scores_remain_bounded_at_zero_and_one_hundred(self):
        low = aggregate_risk([], [], None)
        self.assertEqual(low["accounts"], [])

        high = aggregate_risk(
            [
                {
                    "account_id": "ACC-HIGH",
                    "initial_risk_score": 100,
                    "risk_indicators": [{"rule": "movement"}],
                }
            ],
            [],
            None,
            config=RiskAggregationConfig(behavioral_weight=1, ml_weight=0),
        )
        account = self._account_result(high, "ACC-HIGH")
        self.assertEqual(account["risk_score"], 100)
        self.assertTrue(
            all(0 <= result["risk_score"] <= 100 for result in high["accounts"])
        )

    def test_identical_inputs_produce_deterministic_results(self):
        inputs = (
            [{"account_id": "ACC-A", "initial_risk_score": 35, "risk_indicators": []}],
            [
                {"account_id": "ACC-A", "anomaly_score": 0.2},
                {"account_id": "ACC-B", "anomaly_score": -0.2},
            ],
            {
                "accounts": [
                    {
                        "account_id": "ACC-A",
                        "risk_indicators": [{"rule": "fan_in_then_fan_out"}],
                    }
                ]
            },
        )
        first = aggregate_risk(*inputs)
        second = aggregate_risk(*inputs)
        self.assertEqual(first, second)

    def test_thresholds_and_weights_are_configurable(self):
        results = aggregate_risk(
            rule_results=[],
            ml_results=[
                {"account_id": "ACC-A", "anomaly_score": 0.0},
                {"account_id": "ACC-B", "anomaly_score": 1.0},
            ],
            graph_results={"accounts": []},
            config=RiskAggregationConfig(
                behavioral_weight=0,
                ml_weight=1,
                low_risk_max=10,
                medium_risk_max=50,
            ),
        )
        account = self._account_result(results, "ACC-B")
        self.assertEqual(account["risk_score"], 100)
        self.assertEqual(account["risk_level"], "HIGH")
        self.assertEqual(account["recommendation"], "SIMULATED HOLD")

    def test_rejects_invalid_rule_score(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            aggregate_risk(
                rule_results=[
                    {"account_id": "ACC-BAD", "initial_risk_score": 101}
                ],
                ml_results=[],
                graph_results={"accounts": []},
            )

    @staticmethod
    def _account_result(results, account_id):
        return next(
            account
            for account in results["accounts"]
            if account["account_id"] == account_id
        )


if __name__ == "__main__":
    unittest.main()
