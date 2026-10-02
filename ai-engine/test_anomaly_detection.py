import unittest

import pandas as pd

from anomaly_detection import (
    FEATURE_COLUMNS,
    build_account_features,
    detect_account_anomalies,
)
from data_loader import load_transactions


class AnomalyDetectionTests(unittest.TestCase):
    def test_project_dataset_returns_numeric_features_and_scores(self):
        transactions = load_transactions()
        features = build_account_features(transactions)
        results = detect_account_anomalies(transactions, random_state=17)

        self.assertEqual(len(results), len(features))
        self.assertEqual(len(transactions), 1200)
        self.assertNotIn("transaction_id", features.columns)
        self.assertNotIn("scenario_label", features.columns)
        self.assertNotIn("account_id", FEATURE_COLUMNS)
        self.assertTrue(
            all(pd.api.types.is_numeric_dtype(features[column]) for column in FEATURE_COLUMNS)
        )
        self.assertTrue(results["prediction"].isin({"ANOMALY", "TYPICAL"}).all())
        self.assertTrue(results["anomaly_score"].notna().all())
        self.assertTrue(
            results["is_anomaly"].equals(results["prediction"].eq("ANOMALY"))
        )

    def test_synthetic_mule_is_flagged_and_labels_do_not_affect_model(self):
        transactions = self._synthetic_transactions()
        first = detect_account_anomalies(transactions, contamination=0.05)

        relabeled = transactions.copy()
        relabeled["scenario_label"] = "NORMAL"
        relabeled["transaction_id"] = [
            f"UNRELATED-{index}" for index in range(len(relabeled))
        ]
        second = detect_account_anomalies(relabeled, contamination=0.05)

        first = first.sort_values("account_id").reset_index(drop=True)
        second = second.sort_values("account_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(first, second)
        mule = first.loc[first["account_id"].eq("ACC-MULE-001")].iloc[0]
        self.assertTrue(mule["is_anomaly"])
        self.assertGreater(
            mule["anomaly_score"],
            first.loc[first["account_id"].ne("ACC-MULE-001"), "anomaly_score"].median(),
        )

    def test_rejects_invalid_contamination(self):
        with self.assertRaisesRegex(ValueError, "contamination"):
            detect_account_anomalies(
                self._synthetic_transactions(), contamination=0
            )

    def test_rejects_missing_required_feature_source(self):
        transactions = self._synthetic_transactions().drop(columns="timestamp")
        with self.assertRaisesRegex(ValueError, "timestamp"):
            build_account_features(transactions)

    @staticmethod
    def _synthetic_transactions():
        rows = []
        start = pd.Timestamp("2025-01-01T12:00:00Z")
        for center in range(16):
            account = f"ACC-CENTRE-{center:03}"
            for event in range(4):
                timestamp = start + pd.Timedelta(days=event * 7)
                rows.append(
                    {
                        "transaction_id": f"N-IN-{center}-{event}",
                        "sender": f"ACC-SOURCE-{center:03}-{event:02}",
                        "receiver": account,
                        "amount_paise": 10_000,
                        "timestamp": timestamp,
                        "status": "SUCCESS",
                        "scenario_label": "NORMAL",
                    }
                )
                rows.append(
                    {
                        "transaction_id": f"N-OUT-{center}-{event}",
                        "sender": account,
                        "receiver": f"ACC-DEST-{center:03}-{event:02}",
                        "amount_paise": 10_000,
                        "timestamp": timestamp + pd.Timedelta(minutes=2),
                        "status": "SUCCESS",
                        "scenario_label": "NORMAL",
                    }
                )

        for source in range(8):
            rows.append(
                {
                    "transaction_id": f"S-IN-{source}",
                    "sender": f"ACC-SYN-SOURCE-{source:02}",
                    "receiver": "ACC-MULE-001",
                    "amount_paise": 500_000,
                    "timestamp": start + pd.Timedelta(minutes=source),
                    "status": "SUCCESS",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                }
            )
        for destination in range(2):
            rows.append(
                {
                    "transaction_id": f"S-OUT-{destination}",
                    "sender": "ACC-MULE-001",
                    "receiver": f"ACC-SYN-DEST-{destination:02}",
                    "amount_paise": 400_000,
                    "timestamp": start + pd.Timedelta(minutes=8 + destination),
                    "status": "SUCCESS",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                }
            )
        return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
