import unittest

import pandas as pd

from data_loader import load_transactions
from rule_engine import (
    RuleEngineConfig,
    detect_accounts,
    evaluate_results,
    load_evaluation_role_manifest,
    run_rule_engine,
)


class RuleEngineTests(unittest.TestCase):
    def test_generated_dataset_produces_account_results_and_evaluation(self):
        transactions = load_transactions()
        manifest = load_evaluation_role_manifest()
        report = run_rule_engine(transactions, role_manifest=manifest)

        self.assertEqual(len(transactions), 1200)
        self.assertTrue(report["accounts"])
        self.assertTrue(report["evaluation"]["available"])
        self.assertEqual(
            report["evaluation"]["role_counts"],
            {
                "FOCAL_SUSPICIOUS": 20,
                "SOURCE_PARTICIPANT": 160,
                "DESTINATION_PARTICIPANT": 40,
                "NORMAL": 488,
            },
        )
        self.assertEqual(
            report["evaluation"]["focal_account_evaluation"][
                "positive_account_count"
            ],
            20,
        )
        self.assertEqual(
            report["evaluation"]["participant_evaluation"][
                "positive_account_count"
            ],
            220,
        )
        self.assertEqual(
            sum(
                sum(row)
                for row in report["evaluation"]["focal_account_evaluation"][
                    "confusion_matrix"
                ]["rows_actual_columns_predicted"]
            ),
            report["evaluation"]["evaluated_account_count"],
        )
        self.assertTrue(
            all(
                0 <= account["risk_score"] <= 100
                for account in report["accounts"]
            )
        )

    def test_synthetic_movement_triggers_explained_indicators(self):
        transactions = self._synthetic_network()
        results = detect_accounts(
            transactions.drop(columns="scenario_label"),
            RuleEngineConfig(),
        )
        mule = self._account(results, "ACC-MULE-001")

        self.assertEqual(mule["features"]["incoming_transaction_count"], 8)
        self.assertEqual(mule["features"]["outgoing_transaction_count"], 2)
        self.assertEqual(mule["features"]["unique_incoming_counterparties"], 8)
        self.assertEqual(mule["features"]["unique_outgoing_counterparties"], 2)
        self.assertEqual(mule["features"]["incoming_outgoing_amount_ratio"], 10.0)
        self.assertEqual(mule["risk_score"], 100)
        self.assertEqual(mule["risk_category"], "HIGH")
        self.assertEqual(
            {reason["rule"] for reason in mule["reasons"]},
            {
                "short_window_concentration",
                "elevated_activity",
                "incoming_to_outgoing_movement_imbalance",
                "diverse_incoming_counterparties",
            },
        )
        self.assertTrue(all(reason["explanation"] for reason in mule["reasons"]))

    def test_high_volume_normal_merchant_is_not_flagged_for_volume_alone(self):
        rows = []
        for index in range(30):
            rows.append(
                {
                    "sender": f"ACC-CUSTOMER-{index % 3}",
                    "receiver": "ACC-MERCHANT-001",
                    "amount_paise": 500_000,
                    "timestamp": (
                        pd.Timestamp("2025-01-01T00:00:00Z")
                        + pd.Timedelta(days=index)
                    ),
                    "status": "SUCCESS",
                }
            )
        account = self._account(detect_accounts(pd.DataFrame(rows)), "ACC-MERCHANT-001")
        self.assertEqual(account["features"]["incoming_transaction_count"], 30)
        self.assertEqual(account["risk_score"], 0)
        self.assertEqual(account["reasons"], [])

    def test_scenario_labels_do_not_change_detection(self):
        transactions = self._synthetic_network()
        with_labels = detect_accounts(
            transactions.drop(columns="scenario_label")
        )
        changed_labels = transactions.copy()
        changed_labels["scenario_label"] = "NORMAL"
        changed_labels["evaluation_role"] = "NORMAL"
        changed_evaluation_labels = detect_accounts(changed_labels)
        self.assertEqual(with_labels, changed_evaluation_labels)
        self.assertEqual(
            [result["risk_score"] for result in with_labels],
            [result["risk_score"] for result in changed_evaluation_labels],
        )

    def test_evaluation_separates_focal_accounts_from_participants(self):
        transactions = self._synthetic_network()
        detections = detect_accounts(transactions.drop(columns="scenario_label"))
        manifest = self._role_manifest(transactions)
        evaluation = evaluate_results(
            detections,
            role_manifest=manifest,
        )
        self.assertTrue(evaluation["available"])
        self.assertEqual(
            evaluation["focal_account_evaluation"]["positive_account_count"], 1
        )
        self.assertEqual(
            evaluation["participant_evaluation"]["positive_account_count"], 11
        )
        self.assertEqual(
            evaluation["focal_account_evaluation"]["confusion_matrix"]["labels"],
            ["NON_FOCAL", "FOCAL_SUSPICIOUS"],
        )

    def test_missing_role_manifest_makes_evaluation_unavailable(self):
        transactions = self._synthetic_network()
        detections = detect_accounts(transactions.drop(columns="scenario_label"))
        evaluation = evaluate_results(detections)
        self.assertFalse(evaluation["available"])
        self.assertIn("manifest", evaluation["message"])

    def test_role_manifest_changes_do_not_change_detection(self):
        transactions = self._synthetic_network().drop(columns="scenario_label")
        detections = detect_accounts(transactions)
        focal_roles = self._role_manifest(self._synthetic_network())
        normal_roles = focal_roles.copy()
        normal_roles["evaluation_role"] = "NORMAL"
        detections_with_role_column = detect_accounts(
            transactions.assign(evaluation_role="NORMAL")
        )

        focal_evaluation = evaluate_results(
            detections, role_manifest=focal_roles
        )
        normal_evaluation = evaluate_results(
            detections, role_manifest=normal_roles
        )
        self.assertEqual(
            detections,
            detections_with_role_column,
        )
        self.assertNotEqual(
            focal_evaluation["focal_account_evaluation"],
            normal_evaluation["focal_account_evaluation"],
        )

    def test_empty_dataset_is_reported(self):
        empty = pd.DataFrame(
            columns=["sender", "receiver", "amount_paise", "timestamp", "status"]
        )
        with self.assertRaisesRegex(ValueError, "empty"):
            detect_accounts(empty)

    def test_account_with_only_declined_transactions_is_returned_without_alerts(self):
        transactions = self._synthetic_network().iloc[[0]].copy()
        transactions["status"] = "DECLINED"
        account = self._account(
            detect_accounts(transactions.drop(columns="scenario_label")),
            "ACC-SOURCE-000",
        )
        self.assertEqual(account["risk_score"], 0)
        self.assertEqual(account["risk_category"], "LOW")
        self.assertEqual(account["features"]["successful_transaction_count"], 0)
        self.assertEqual(account["reasons"], [])

    def test_missing_and_invalid_values_are_rejected(self):
        row = self._synthetic_network().iloc[[0]].copy()
        row.loc[row.index[0], "sender"] = None
        with self.assertRaisesRegex(ValueError, "sender"):
            detect_accounts(row)

        row = self._synthetic_network().iloc[[0]].copy()
        row["amount_paise"] = "invalid"
        with self.assertRaisesRegex(ValueError, "amount_paise"):
            detect_accounts(row)

    @staticmethod
    def _account(results, account_id):
        return next(result for result in results if result["account_id"] == account_id)

    @staticmethod
    def _role_manifest(transactions):
        account_ids = sorted(
            set(transactions["sender"].astype(str))
            | set(transactions["receiver"].astype(str))
        )
        roles = []
        for account_id in account_ids:
            if account_id == "ACC-MULE-001":
                role = "FOCAL_SUSPICIOUS"
            elif account_id.startswith("ACC-SOURCE-"):
                role = "SOURCE_PARTICIPANT"
            elif account_id.startswith("ACC-DESTINATION-"):
                role = "DESTINATION_PARTICIPANT"
            else:
                role = "NORMAL"
            roles.append({"account_id": account_id, "evaluation_role": role})
        return pd.DataFrame(roles)

    @staticmethod
    def _synthetic_network():
        start = pd.Timestamp("2025-01-01T12:00:00Z")
        rows = []
        for index in range(8):
            rows.append(
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
            rows.append(
                {
                    "sender": "ACC-MULE-001",
                    "receiver": f"ACC-DESTINATION-{index:03}",
                    "amount_paise": 200_000,
                    "timestamp": start + pd.Timedelta(minutes=8 + index),
                    "status": "SUCCESS",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                }
            )
        return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
