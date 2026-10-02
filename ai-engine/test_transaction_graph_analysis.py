import unittest

import pandas as pd

from data_loader import load_transactions
from transaction_graph_analysis import (
    GraphRuleConfig,
    analyze_transaction_graph,
)


class TransactionGraphAnalysisTests(unittest.TestCase):
    def test_generated_synthetic_network_produces_explained_clusters(self):
        transactions = load_transactions()
        results = analyze_transaction_graph(transactions)

        self.assertEqual(len(transactions), 1200)
        self.assertGreater(results["summary"]["account_count"], 0)
        self.assertGreater(
            results["summary"]["successful_transaction_count"], 0
        )
        self.assertGreater(results["summary"]["connected_alert_cluster_count"], 0)
        self.assertTrue(results["clusters"])

        mule = self._account_result(results, "ACC-M-000001")
        self.assertGreaterEqual(mule["incoming_degree"], 6)
        self.assertGreaterEqual(mule["outgoing_degree"], 2)
        self.assertGreater(mule["unique_connected_accounts"], 0)
        self.assertGreater(mule["weighted_incoming_amount_paise"], 0)
        self.assertGreater(mule["weighted_outgoing_amount_paise"], 0)
        self.assertTrue(mule["risk_indicators"])
        self.assertTrue(
            all(indicator["explanation"] for indicator in mule["risk_indicators"])
        )
        self.assertTrue(
            any(
                "ACC-M-000001" in cluster["flagged_account_ids"]
                for cluster in results["clusters"]
            )
        )

    def test_graph_results_do_not_depend_on_evaluation_labels(self):
        transactions = load_transactions()
        original = analyze_transaction_graph(transactions)
        relabeled = transactions.copy()
        relabeled["scenario_label"] = "NORMAL"

        self.assertEqual(original, analyze_transaction_graph(relabeled))

    def test_normal_high_volume_merchant_is_not_flagged_by_volume_alone(self):
        rows = []
        for index in range(20):
            rows.append(
                {
                    "transaction_id": f"TXN-{index:03}",
                    "sender": f"ACC-CUSTOMER-{index % 2}",
                    "receiver": "ACC-MERCHANT-001",
                    "amount_paise": 100_000,
                    "timestamp": (
                        pd.Timestamp("2025-01-01T00:00:00Z")
                        + pd.Timedelta(days=index)
                    ),
                    "status": "SUCCESS",
                }
            )
        results = analyze_transaction_graph(pd.DataFrame(rows))
        merchant = self._account_result(results, "ACC-MERCHANT-001")
        self.assertEqual(merchant["incoming_degree"], 20)
        self.assertEqual(merchant["unique_incoming_accounts"], 2)
        self.assertEqual(merchant["risk_indicators"], [])
        self.assertEqual(results["clusters"], [])

    def test_thresholds_are_configurable(self):
        transactions = pd.DataFrame(
            [
                {
                    "transaction_id": "TXN-001",
                    "sender": "ACC-A",
                    "receiver": "ACC-B",
                    "amount_paise": 100,
                    "timestamp": "2025-01-01T00:00:00Z",
                    "status": "SUCCESS",
                }
            ]
        )
        results = analyze_transaction_graph(
            transactions,
            GraphRuleConfig(
                min_successful_transactions=1,
                min_unique_incoming_accounts=1,
                min_unique_outgoing_accounts=1,
            ),
        )
        account = self._account_result(results, "ACC-A")
        self.assertEqual(account["outgoing_degree"], 1)

    @staticmethod
    def _account_result(results, account_id):
        return next(
            account
            for account in results["accounts"]
            if account["account_id"] == account_id
        )


if __name__ == "__main__":
    unittest.main()
