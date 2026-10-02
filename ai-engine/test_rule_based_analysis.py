import unittest

import pandas as pd

from rule_based_analysis import RuleConfig, analyze_accounts


class RuleBasedAnalysisTests(unittest.TestCase):
    def test_normal_high_volume_merchant_is_not_flagged_for_volume_alone(self):
        transactions = []
        for index in range(25):
            transactions.append(
                {
                    "sender": f"ACC-CUSTOMER-{index % 3}",
                    "receiver": "ACC-MERCHANT-001",
                    "amount_paise": 2_000 + index,
                    "timestamp": (
                        pd.Timestamp("2025-01-01T00:00:00Z")
                        + pd.Timedelta(days=index)
                    ),
                    "status": "SUCCESS",
                    "scenario_label": "NORMAL",
                }
            )

        merchant = self._account_result(
            analyze_accounts(pd.DataFrame(transactions)), "ACC-MERCHANT-001"
        )
        self.assertEqual(merchant["features"]["transaction_count"], 25)
        self.assertEqual(merchant["risk_indicators"], [])
        self.assertEqual(merchant["initial_risk_score"], 0)

    def test_synthetic_suspicious_flow_has_explained_indicators(self):
        transactions = []
        start = pd.Timestamp("2025-02-01T12:00:00Z")
        for index in range(8):
            transactions.append(
                {
                    "sender": f"ACC-SOURCE-{index:03}",
                    "receiver": "ACC-MULE-001",
                    "amount_paise": 500_000,
                    "timestamp": start + pd.Timedelta(minutes=index),
                    "status": "SUCCESS",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                }
            )
        for index in range(2):
            transactions.append(
                {
                    "sender": "ACC-MULE-001",
                    "receiver": f"ACC-DESTINATION-{index:03}",
                    "amount_paise": 400_000,
                    "timestamp": start + pd.Timedelta(minutes=8 + index),
                    "status": "SUCCESS",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                }
            )

        mule = self._account_result(
            analyze_accounts(pd.DataFrame(transactions)), "ACC-MULE-001"
        )
        self.assertEqual(mule["features"]["incoming_transaction_count"], 8)
        self.assertEqual(mule["features"]["outgoing_transaction_count"], 2)
        self.assertEqual(mule["features"]["unique_incoming_counterparties"], 8)
        self.assertEqual(mule["features"]["unique_outgoing_counterparties"], 2)
        self.assertEqual(mule["initial_risk_score"], 100)
        self.assertEqual(
            {indicator["rule"] for indicator in mule["risk_indicators"]},
            {
                "diverse_incoming_counterparties",
                "incoming_outgoing_flow_imbalance",
                "elevated_transaction_frequency",
                "short_window_transaction_concentration",
            },
        )
        self.assertTrue(
            all(indicator["explanation"] for indicator in mule["risk_indicators"])
        )

    def test_custom_thresholds_are_applied(self):
        transactions = [
            {
                "sender": "ACC-SOURCE-001",
                "receiver": "ACC-TEST-001",
                "amount_paise": 10_000,
                "timestamp": "2025-01-01T12:00:00Z",
                "status": "SUCCESS",
            }
        ]
        config = RuleConfig(min_transactions_for_rules=2)
        result = analyze_accounts(pd.DataFrame(transactions), config)
        account = self._account_result(result, "ACC-TEST-001")
        self.assertEqual(account["initial_risk_score"], 0)
        self.assertEqual(account["features"]["total_incoming_amount_paise"], 10_000)
        self.assertIsNone(account["features"]["incoming_outgoing_ratio"])

    def test_rejects_invalid_transaction_amount(self):
        transactions = pd.DataFrame(
            [
                {
                    "sender": "ACC-SOURCE-001",
                    "receiver": "ACC-TEST-001",
                    "amount_paise": "invalid",
                    "timestamp": "2025-01-01T12:00:00Z",
                    "status": "SUCCESS",
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "amount_paise"):
            analyze_accounts(transactions)

    @staticmethod
    def _account_result(results, account_id):
        return next(result for result in results if result["account_id"] == account_id)


if __name__ == "__main__":
    unittest.main()
