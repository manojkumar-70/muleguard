import unittest

from fastapi.testclient import TestClient

import api


class MuleGuardApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def test_health_does_not_expose_environment(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"status": "ok", "mode": "synthetic-only"}
        )
        self.assertNotIn("environment", response.json())

    def test_summary_and_alerts_use_existing_analysis_modules(self):
        summary = self.client.get("/summary")
        alerts = self.client.get("/alerts")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["transaction_count"], 1200)
        self.assertEqual(summary.json()["risk"]["account_count"], 708)
        self.assertEqual(alerts.status_code, 200)
        self.assertGreater(alerts.json()["count"], 0)
        self.assertIn("risk_score", alerts.json()["alerts"][0])

    def test_account_found_and_missing(self):
        found = self.client.get("/accounts/ACC-M-000001")
        missing = self.client.get("/accounts/ACC-DOES-NOT-EXIST")
        self.assertEqual(found.status_code, 200)
        self.assertIn("method_contributions", found.json())
        self.assertEqual(missing.status_code, 404)

    def test_analyze_accepts_synthetic_request_and_returns_structured_results(self):
        response = self.client.post(
            "/analyze",
            json={
                "transactions": [
                    self._transaction("TXN-TEST-001", "ACC-TEST-001", "ACC-TEST-002"),
                    self._transaction("TXN-TEST-002", "ACC-TEST-002", "ACC-TEST-003"),
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["transaction_count"], 2)
        self.assertIn("summary", response.json())
        self.assertEqual(len(response.json()["accounts"]), 3)
        self.assertIn("disclaimer", response.json()["summary"])

    def test_analyze_rejects_non_synthetic_data_and_duplicate_ids(self):
        real_network = self._transaction(
            "TXN-REAL-IP", "ACC-TEST-001", "ACC-TEST-002"
        )
        real_network["ip_address"] = "8.8.8.8"
        rejected_ip = self.client.post(
            "/analyze", json={"transactions": [real_network]}
        )
        self.assertEqual(rejected_ip.status_code, 422)

        duplicate = self._transaction(
            "TXN-DUPLICATE", "ACC-TEST-001", "ACC-TEST-002"
        )
        second = self._transaction(
            "TXN-DUPLICATE", "ACC-TEST-002", "ACC-TEST-003"
        )
        rejected_duplicate = self.client.post(
            "/analyze", json={"transactions": [duplicate, second]}
        )
        self.assertEqual(rejected_duplicate.status_code, 422)

    def test_analyze_rejects_extra_or_malformed_request_fields(self):
        transaction = self._transaction(
            "TXN-EXTRA", "ACC-TEST-001", "ACC-TEST-002"
        )
        transaction["secret"] = "must-not-be-accepted"
        response = self.client.post(
            "/analyze", json={"transactions": [transaction]}
        )
        self.assertEqual(response.status_code, 422)

        response = self.client.post("/analyze", json={"transactions": []})
        self.assertEqual(response.status_code, 422)

    @staticmethod
    def _transaction(transaction_id, sender, receiver):
        return {
            "transaction_id": transaction_id,
            "sender": sender,
            "receiver": receiver,
            "amount_paise": 12500,
            "currency": "INR",
            "timestamp": "2025-01-01T12:00:00Z",
            "device_id": "DEV-TEST-001",
            "ip_address": "192.0.2.10",
            "status": "SUCCESS",
            "scenario_label": "NORMAL",
        }


if __name__ == "__main__":
    unittest.main()
