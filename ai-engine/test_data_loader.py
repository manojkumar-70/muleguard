import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data_loader import DEFAULT_DATASET_PATH, load_transactions, print_summary


class TransactionLoaderTests(unittest.TestCase):
    def test_loads_project_dataset(self):
        transactions = load_transactions()
        self.assertEqual(len(transactions), 1200)
        self.assertTrue(pd.api.types.is_integer_dtype(transactions["amount_paise"]))
        self.assertTrue(
            pd.api.types.is_datetime64_any_dtype(transactions["timestamp"])
        )

    def test_accepts_amount_paise_camel_case_alias(self):
        row = self._valid_row()
        row["amountPaise"] = row.pop("amount_paise")
        transactions = self._load_rows([row])
        self.assertEqual(transactions.loc[0, "amount_paise"], 12500)

    def test_rejects_missing_required_column(self):
        row = self._valid_row()
        del row["receiver"]
        with self.assertRaisesRegex(ValueError, "receiver"):
            self._load_rows([row])

    def test_rejects_invalid_amount_and_timestamp(self):
        row = self._valid_row()
        row["amount_paise"] = "not-an-amount"
        with self.assertRaisesRegex(ValueError, "Invalid amount_paise"):
            self._load_rows([row])

        row = self._valid_row()
        row["timestamp"] = "not-a-timestamp"
        with self.assertRaisesRegex(ValueError, "Invalid timestamp"):
            self._load_rows([row])

    def test_missing_dataset_has_clear_error(self):
        missing = DEFAULT_DATASET_PATH.parent / "missing-transactions.csv"
        with self.assertRaisesRegex(FileNotFoundError, "Transaction dataset not found"):
            load_transactions(missing)

    def test_prints_summary_counts(self):
        transactions = self._load_rows([self._valid_row()])
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
            from contextlib import redirect_stdout

            with redirect_stdout(output):
                print_summary(transactions)
            output.seek(0)
            summary = output.read()
        self.assertIn("Transaction count: 1", summary)
        self.assertIn("Unique senders: 1", summary)
        self.assertIn("Unique receivers: 1", summary)
        self.assertIn("SUCCESS", summary)

    @staticmethod
    def _valid_row():
        return {
            "transaction_id": "TXN-TEST",
            "sender": "ACC-TEST-001",
            "receiver": "ACC-TEST-002",
            "amount_paise": 12500,
            "currency": "INR",
            "timestamp": "2025-01-01T12:00:00Z",
            "device_id": "DEV-TEST-001",
            "ip_address": "192.0.2.1",
            "status": "SUCCESS",
            "scenario_label": "NORMAL",
        }

    def _load_rows(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transactions.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            return load_transactions(path)


if __name__ == "__main__":
    unittest.main()
