import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from evaluate_early_warning import evaluate_early_warning


class EarlyWarningEvaluationTests(unittest.TestCase):
    def test_reports_first_alert_latency_checkpoints_and_role_groups(self):
        transactions = self._transactions()
        roles = self._roles()

        with patch(
            "evaluate_early_warning.replay_transactions",
            return_value=self._replay_result(),
        ):
            report = evaluate_early_warning(
                transactions,
                roles,
                checkpoints=(1, 2, 3),
                seed=7,
            )

        focal = report["groups"]["focal_suspicious"]
        self.assertEqual(focal["detected_accounts"], ["ACC-A"])
        self.assertEqual(focal["missed_accounts"], [])
        self.assertEqual(focal["detection_rate"], 1)
        self.assertEqual(
            [point["detection_rate"] for point in focal["detection_rate_at_checkpoints"]],
            [0, 1, 1],
        )
        self.assertEqual(
            [point["checkpoint_reached"] for point in focal["detection_rate_at_checkpoints"]],
            [True, True, True],
        )
        latency = focal["detection_latency_seconds"]
        self.assertEqual(latency["mean"], 600)
        self.assertEqual(latency["median"], 600)
        self.assertEqual(latency["values"], [600])

        participants = report["groups"]["suspicious_participants"]
        self.assertEqual(participants["detected_accounts"], ["ACC-A"])
        self.assertEqual(participants["missed_accounts"], ["ACC-B"])
        self.assertEqual(participants["detected_count"], 1)
        self.assertEqual(participants["missed_count"], 1)

        legitimate = report["groups"]["legitimate_normal"]
        self.assertEqual(legitimate["detected_accounts"], ["ACC-D"])
        self.assertAlmostEqual(legitimate["false_alert_rate"], 1 / 3)
        self.assertEqual(
            report["accounts"][0]["transactions_from_first_observation_to_alert"],
            2,
        )
        self.assertEqual(
            report["accounts"][0]["first_alert_transaction_count"],
            2,
        )
        self.assertEqual(
            report["scenario_label_summary"][
                "accounts_with_synthetic_suspicious_labeled_activity"
            ],
            2,
        )

    def test_replay_is_given_no_evaluation_labels_and_receives_seed(self):
        captured = {}

        def fake_replay(transactions, seed):
            captured["columns"] = set(transactions.columns)
            captured["seed"] = seed
            return self._replay_result()

        with patch(
            "evaluate_early_warning.replay_transactions",
            side_effect=fake_replay,
        ):
            evaluate_early_warning(
                self._transactions(),
                self._roles(),
                checkpoints=(1,),
                seed=19,
            )

        self.assertNotIn("scenario_label", captured["columns"])
        self.assertNotIn("evaluation_role", captured["columns"])
        self.assertEqual(captured["seed"], 19)

    def test_evaluates_actual_replay_module_output(self):
        report = evaluate_early_warning(
            self._transactions(),
            self._roles(),
            checkpoints=(1, 2, 3),
            seed=11,
        )

        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["transaction_count"], 3)
        self.assertFalse(report["detection_labels_used_as_features"])
        self.assertEqual(
            report["groups"]["focal_suspicious"]["account_count"],
            1,
        )

    def test_empty_dataset_returns_clear_no_data_report(self):
        report = evaluate_early_warning(
            pd.DataFrame(
                columns=[
                    "transaction_id",
                    "sender",
                    "receiver",
                    "timestamp",
                    "scenario_label",
                ]
            ),
            pd.DataFrame(columns=["account_id", "evaluation_role"]),
            checkpoints=(2,),
        )

        self.assertEqual(report["status"], "no_data")
        self.assertEqual(report["transaction_count"], 0)
        self.assertEqual(
            report["groups"]["focal_suspicious"]["detection_latency_seconds"][
                "mean"
            ],
            None,
        )

    def test_empty_dataset_with_nonempty_roles_is_rejected(self):
        empty = pd.DataFrame(
            columns=[
                "transaction_id",
                "sender",
                "receiver",
                "timestamp",
                "scenario_label",
            ]
        )
        with self.assertRaisesRegex(ValueError, "empty transaction dataset"):
            evaluate_early_warning(
                empty,
                pd.DataFrame(
                    [{"account_id": "ACC-A", "evaluation_role": "FOCAL_SUSPICIOUS"}]
                ),
            )

    def test_missing_or_invalid_role_manifest_is_reported(self):
        with self.assertRaisesRegex(FileNotFoundError, "required"):
            evaluate_early_warning(self._transactions(), None)
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "roles.csv"
            with self.assertRaisesRegex(FileNotFoundError, "not found"):
                evaluate_early_warning(self._transactions(), missing_path)
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            evaluate_early_warning(
                self._transactions(),
                pd.DataFrame([{"account_id": "ACC-A"}]),
            )

    def test_checkpoints_must_be_unique_positive_integers(self):
        for checkpoints in ((), (0,), (1, 1), (1.5,)):
            with self.subTest(checkpoints=checkpoints):
                with self.assertRaises(ValueError):
                    evaluate_early_warning(
                        self._transactions(),
                        self._roles(),
                        checkpoints=checkpoints,
                    )
        with self.assertRaisesRegex(ValueError, "seed must be an integer"):
            evaluate_early_warning(
                self._transactions(),
                self._roles(),
                seed=True,
            )

    def test_unalerted_accounts_are_missed_and_excluded_from_latency(self):
        replay = self._replay_result()
        replay["accounts"]["ACC-A"]["first_alert_timestamp"] = None
        replay["accounts"]["ACC-A"]["transaction_count_at_first_alert"] = None
        with patch(
            "evaluate_early_warning.replay_transactions",
            return_value=replay,
        ):
            report = evaluate_early_warning(
                self._transactions(),
                self._roles(),
                checkpoints=(1, 3),
            )

        focal = report["groups"]["focal_suspicious"]
        self.assertEqual(focal["missed_accounts"], ["ACC-A"])
        self.assertEqual(focal["detection_latency_seconds"]["count"], 0)
        self.assertIsNone(focal["detection_latency_seconds"]["mean"])

    @staticmethod
    def _transactions():
        return pd.DataFrame(
            [
                {
                    "transaction_id": "TX-1",
                    "sender": "ACC-A",
                    "receiver": "ACC-N1",
                    "timestamp": "2025-01-01T00:00:00Z",
                    "scenario_label": "NORMAL",
                    "amount_paise": 100,
                    "status": "SUCCESS",
                },
                {
                    "transaction_id": "TX-2",
                    "sender": "ACC-B",
                    "receiver": "ACC-A",
                    "timestamp": "2025-01-01T00:10:00Z",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                    "amount_paise": 100,
                    "status": "SUCCESS",
                },
                {
                    "transaction_id": "TX-3",
                    "sender": "ACC-C",
                    "receiver": "ACC-D",
                    "timestamp": "2025-01-01T00:20:00Z",
                    "scenario_label": "NORMAL",
                    "amount_paise": 100,
                    "status": "SUCCESS",
                },
            ]
        )

    @staticmethod
    def _roles():
        return pd.DataFrame(
            [
                {"account_id": "ACC-A", "evaluation_role": "FOCAL_SUSPICIOUS"},
                {"account_id": "ACC-B", "evaluation_role": "SOURCE_PARTICIPANT"},
                {"account_id": "ACC-N1", "evaluation_role": "NORMAL"},
                {"account_id": "ACC-C", "evaluation_role": "NORMAL"},
                {"account_id": "ACC-D", "evaluation_role": "NORMAL"},
            ]
        )

    @staticmethod
    def _replay_result():
        return {
            "transaction_count": 3,
            "accounts": {
                "ACC-A": {
                    "first_alert_timestamp": "2025-01-01T00:10:00+00:00",
                    "transaction_count_at_first_alert": 2,
                    "risk_score_progression": [
                        {
                            "timestamp": "2025-01-01T00:00:00+00:00",
                            "transaction_count": 1,
                        },
                        {
                            "timestamp": "2025-01-01T00:10:00+00:00",
                            "transaction_count": 2,
                        },
                    ],
                },
                "ACC-D": {
                    "first_alert_timestamp": "2025-01-01T00:20:00+00:00",
                    "transaction_count_at_first_alert": 3,
                    "risk_score_progression": [
                        {
                            "timestamp": "2025-01-01T00:20:00+00:00",
                            "transaction_count": 3,
                        }
                    ],
                },
                "ACC-B": {
                    "first_alert_timestamp": None,
                    "transaction_count_at_first_alert": None,
                    "risk_score_progression": [
                        {
                            "timestamp": "2025-01-01T00:10:00+00:00",
                            "transaction_count": 2,
                        }
                    ],
                },
                "ACC-N1": {
                    "first_alert_timestamp": None,
                    "transaction_count_at_first_alert": None,
                    "risk_score_progression": [
                        {
                            "timestamp": "2025-01-01T00:00:00+00:00",
                            "transaction_count": 1,
                        }
                    ],
                },
                "ACC-C": {
                    "first_alert_timestamp": None,
                    "transaction_count_at_first_alert": None,
                    "risk_score_progression": [
                        {
                            "timestamp": "2025-01-01T00:20:00+00:00",
                            "transaction_count": 3,
                        }
                    ],
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
