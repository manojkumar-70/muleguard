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

        self.assertEqual(account["risk_score"], 100)
        self.assertEqual(account["risk_level"], "HIGH")
        self.assertEqual(account["recommendation"], "SIMULATED HOLD")
        self.assertEqual(contributions["rule_based"]["raw_score"], 80)
        self.assertEqual(contributions["graph_based"]["raw_score"], 100)
        self.assertEqual(contributions["graph_based"]["contribution"], 70)
        self.assertEqual(contributions["rule_based"]["contribution"], 0)
        self.assertEqual(contributions["ml_anomaly"]["contribution"], 30)
        self.assertIn(
            "several incoming",
            contributions["graph_based"]["indicators"][0]["explanation"].lower(),
        )
        self.assertEqual(results["summary"]["risk_level_counts"]["HIGH"], 1)
        self.assertIn("SIMULATED", DISCLAIMER)

    def test_rule_and_graph_signals_are_not_added_together(self):
        results = aggregate_risk(
            rule_results=[
                {
                    "account_id": "ACC-CORRELATED",
                    "initial_risk_score": 100,
                    "risk_indicators": [{"rule": "rule"}],
                }
            ],
            ml_results=[],
            graph_results={
                "accounts": [
                    {
                        "account_id": "ACC-CORRELATED",
                        "risk_indicators": [{"rule": "graph"}],
                    }
                ]
            },
        )
        account = self._account_result(results, "ACC-CORRELATED")
        self.assertEqual(account["risk_score"], 70)
        self.assertEqual(
            account["method_contributions"]["rule_based"]["contribution"], 35
        )
        self.assertEqual(
            account["method_contributions"]["graph_based"]["contribution"], 35
        )

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
