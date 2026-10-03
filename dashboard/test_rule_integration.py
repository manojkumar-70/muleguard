import unittest
from pathlib import Path
import sys

import pandas as pd

AI_ENGINE_PATH = Path(__file__).resolve().parents[1] / "ai-engine"
if str(AI_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_PATH))

from api import _run_analysis
from app import run_analysis
from rule_engine import run_rule_engine
from rule_based_analysis import RuleConfig, analyze_accounts


class RuleIntegrationTests(unittest.TestCase):
    def test_cli_api_and_dashboard_use_equivalent_analysis_results(self):
        transactions = self._synthetic_transactions()
        cli_results = run_rule_engine(transactions)["accounts"]
        api_analysis = _run_analysis(transactions)
        dashboard_analysis = run_analysis(transactions)
        api_results = api_analysis["rule_results"]
        dashboard_results = dashboard_analysis["rules"]
        explicit_legacy_config_results = analyze_accounts(
            transactions, RuleConfig()
        )

        self.assertEqual(
            self._canonical_view(cli_results),
            self._compatibility_view(api_results),
        )
        self.assertEqual(
            self._canonical_view(cli_results),
            self._compatibility_view(dashboard_results),
        )
        self.assertEqual(
            self._canonical_view(cli_results),
            self._compatibility_view(explicit_legacy_config_results),
        )
        self.assertEqual(api_results, dashboard_results)
        self.assertEqual(
            api_analysis["graph_results"], dashboard_analysis["graph"]
        )
        self.assertEqual(
            api_analysis["risk_results"], dashboard_analysis["risk"]
        )
        pd.testing.assert_frame_equal(
            pd.DataFrame(api_analysis["ml_results"]).sort_values(
                "account_id"
            ).reset_index(drop=True),
            dashboard_analysis["anomalies"].sort_values(
                "account_id"
            ).reset_index(drop=True),
        )

        relabeled = transactions.assign(
            scenario_label="NORMAL", evaluation_role="NORMAL"
        )
        relabeled_api_analysis = _run_analysis(relabeled)
        relabeled_dashboard_analysis = run_analysis(relabeled)
        self.assertEqual(api_results, relabeled_api_analysis["rule_results"])
        self.assertEqual(
            api_analysis["graph_results"],
            relabeled_api_analysis["graph_results"],
        )
        self.assertEqual(
            api_analysis["risk_results"],
            relabeled_api_analysis["risk_results"],
        )
        pd.testing.assert_frame_equal(
            pd.DataFrame(api_analysis["ml_results"]).sort_values(
                "account_id"
            ).reset_index(drop=True),
            pd.DataFrame(relabeled_api_analysis["ml_results"]).sort_values(
                "account_id"
            ).reset_index(drop=True),
        )
        self.assertEqual(
            dashboard_analysis["risk"], relabeled_dashboard_analysis["risk"]
        )

    @staticmethod
    def _canonical_view(results):
        return {
            account["account_id"]: (
                account["risk_score"],
                sorted(
                    (reason["points"], reason["explanation"])
                    for reason in account["reasons"]
                ),
            )
            for account in results
        }

    @staticmethod
    def _compatibility_view(results):
        return {
            account["account_id"]: (
                account["initial_risk_score"],
                sorted(
                    (indicator["points"], indicator["explanation"])
                    for indicator in account["risk_indicators"]
                ),
            )
            for account in results
        }

    @staticmethod
    def _synthetic_transactions():
        start = pd.Timestamp("2025-01-01T12:00:00Z")
        rows = []
        for index in range(8):
            rows.append(
                {
                    "transaction_id": f"TXN-IN-{index:03}",
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
                    "transaction_id": f"TXN-OUT-{index:03}",
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
