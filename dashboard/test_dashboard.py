import unittest
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import make_network_figure, make_risk_distribution_figure, run_analysis


class DashboardTests(unittest.TestCase):
    def test_dashboard_analysis_uses_existing_modules(self):
        transactions = pd.DataFrame(
            [
                self._transaction("TXN-001", "ACC-A", "ACC-MULE-001", 100_000, 0),
                self._transaction("TXN-002", "ACC-B", "ACC-MULE-001", 100_000, 1),
                self._transaction("TXN-003", "ACC-C", "ACC-MULE-001", 100_000, 2),
                self._transaction("TXN-004", "ACC-D", "ACC-MULE-001", 100_000, 3),
                self._transaction("TXN-005", "ACC-E", "ACC-MULE-001", 100_000, 4),
                self._transaction("TXN-006", "ACC-F", "ACC-MULE-001", 100_000, 5),
                self._transaction("TXN-007", "ACC-MULE-001", "ACC-OUT-1", 100_000, 6),
                self._transaction("TXN-008", "ACC-MULE-001", "ACC-OUT-2", 100_000, 7),
            ]
        )
        analysis = run_analysis(transactions)
        self.assertEqual(analysis["risk"]["summary"]["account_count"], 9)
        account = next(
            entry
            for entry in analysis["risk"]["accounts"]
            if entry["account_id"] == "ACC-MULE-001"
        )
        self.assertIn(account["risk_level"], {"MEDIUM", "HIGH"})

    def test_plotly_figures_are_created(self):
        transactions = pd.DataFrame(
            [
                self._transaction("TXN-001", "ACC-A", "ACC-B", 1000, 0),
                self._transaction("TXN-002", "ACC-B", "ACC-C", 2000, 1),
            ]
        )
        analysis = run_analysis(transactions)
        risk_figure = make_risk_distribution_figure(analysis["risk"])
        network_figure = make_network_figure(
            transactions, analysis["risk"], "ACC-B"
        )
        self.assertTrue(risk_figure.data)
        self.assertTrue(network_figure.data)
        self.assertGreaterEqual(len(network_figure.data), 2)

    @staticmethod
    def _transaction(transaction_id, sender, receiver, amount, minute):
        return {
            "transaction_id": transaction_id,
            "sender": sender,
            "receiver": receiver,
            "amount_paise": amount,
            "currency": "INR",
            "timestamp": (
                pd.Timestamp("2025-01-01T12:00:00Z")
                + pd.Timedelta(minutes=minute)
            ),
            "device_id": "DEV-TEST-001",
            "ip_address": "192.0.2.10",
            "status": "SUCCESS",
            "scenario_label": (
                "SYNTHETIC_SUSPICIOUS" if "MULE" in sender or "MULE" in receiver
                else "NORMAL"
            ),
        }


if __name__ == "__main__":
    unittest.main()
