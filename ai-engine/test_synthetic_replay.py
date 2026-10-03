import unittest
import time
from unittest.mock import patch

import numpy as np
import pandas as pd

import synthetic_replay
from benchmark_synthetic_replay import build_benchmark_transactions
from synthetic_replay import replay_transactions


class SyntheticReplayTests(unittest.TestCase):
    def test_events_are_replayed_chronologically_and_one_at_a_time(self):
        result = replay_transactions(self._transactions())

        self.assertEqual(
            [step["transaction_id"] for step in result["steps"]],
            ["TX-1", "TX-2", "TX-3"],
        )
        self.assertEqual(
            [step["available_transaction_ids"] for step in result["steps"]],
            [["TX-1"], ["TX-1", "TX-2"], ["TX-1", "TX-2", "TX-3"]],
        )
        self.assertEqual(
            [step["timestamp"] for step in result["steps"]],
            [
                "2025-01-01T00:00:00+00:00",
                "2025-01-01T00:01:00+00:00",
                "2025-01-01T00:02:00+00:00",
            ],
        )

    def test_detector_receives_only_current_history_not_future_rows_or_labels(self):
        observed = []
        from synthetic_replay import _analyze_history

        def inspect_history(history, seed):
            observed.append((history["transaction_id"].tolist(), set(history.columns)))
            return _analyze_history(history, seed)

        with patch("synthetic_replay._analyze_history", side_effect=inspect_history):
            replay_transactions(self._transactions(), seed=9)

        self.assertEqual(
            [event_ids for event_ids, _ in observed],
            [["TX-1"], ["TX-1", "TX-2"], ["TX-1", "TX-2", "TX-3"]],
        )
        for _, columns in observed:
            self.assertNotIn("scenario_label", columns)
            self.assertNotIn("evaluation_role", columns)

    def test_history_prefixes_are_copied_without_future_backing_data(self):
        captured = []

        def capture(history, _seed):
            self.assertEqual(_seed, 42)
            captured.append(history)
            return {
                "rule_results": [],
                "ml_results": [],
                "graph_results": {"accounts": []},
                "risk_results": {"accounts": []},
            }

        with patch("synthetic_replay._analyze_history", side_effect=capture):
            replay_transactions(self._transactions())

        self.assertEqual(len(captured), 3)
        self.assertFalse(
            np.shares_memory(
                captured[0].to_numpy(copy=False),
                captured[1].to_numpy(copy=False),
            )
        )
        self.assertFalse(
            np.shares_memory(
                captured[1].to_numpy(copy=False),
                captured[2].to_numpy(copy=False),
            )
        )
        self.assertEqual([len(frame) for frame in captured], [1, 2, 3])

    def test_small_benchmark_matches_previous_replay_behavior(self):
        transactions = self._benchmark_transactions(6)

        started = time.perf_counter()
        expected = self._legacy_reference_replay(transactions, seed=31)
        legacy_seconds = time.perf_counter() - started

        started = time.perf_counter()
        actual = replay_transactions(transactions, seed=31)
        optimized_seconds = time.perf_counter() - started

        self.assertEqual(expected, actual)
        print(
            "Controlled replay benchmark (6 events, existing detectors): "
            f"reference={legacy_seconds:.3f}s, optimized={optimized_seconds:.3f}s, "
            f"speedup={legacy_seconds / optimized_seconds:.2f}x"
        )

    def test_prefix_preparation_microbenchmark_matches_previous_behavior(self):
        transactions = self._benchmark_transactions(250)
        empty_analysis = {
            "rule_results": [],
            "ml_results": [],
            "graph_results": {"accounts": []},
            "risk_results": {"accounts": []},
        }

        with patch.object(
            SyntheticReplayTests,
            "_legacy_analyze_history",
            return_value=empty_analysis,
        ):
            started = time.perf_counter()
            expected = self._legacy_reference_replay(transactions, seed=31)
            legacy_seconds = time.perf_counter() - started

        with patch(
            "synthetic_replay._analyze_history",
            return_value=empty_analysis,
        ):
            started = time.perf_counter()
            actual = replay_transactions(transactions, seed=31)
            optimized_seconds = time.perf_counter() - started

        self.assertEqual(expected, actual)
        print(
            "Prefix preparation benchmark (250 events, detector work stubbed): "
            f"reference={legacy_seconds:.3f}s, "
            f"optimized={optimized_seconds:.3f}s, "
            f"speedup={legacy_seconds / optimized_seconds:.2f}x"
        )

    def test_replay_is_reproducible_for_the_same_seed(self):
        transactions = self._transactions()
        first = replay_transactions(transactions, seed=23)
        second = replay_transactions(transactions, seed=23)

        self.assertEqual(first, second)

    def test_each_chronological_prefix_fits_anomaly_model_exactly_once(self):
        transactions = self._benchmark_transactions(3)
        from anomaly_detection import detect_account_anomalies

        with patch(
            "synthetic_replay.detect_account_anomalies",
            wraps=detect_account_anomalies,
        ) as detect:
            result = replay_transactions(transactions, seed=41)

        self.assertEqual(detect.call_count, 3)
        self.assertEqual(len(result["steps"]), 3)

    def test_benchmark_input_is_deterministic_and_contains_only_synthetic_labels(self):
        first = build_benchmark_transactions(50)
        second = build_benchmark_transactions(50)

        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(len(first), 50)
        self.assertTrue(
            first["scenario_label"].isin({"NORMAL", "SYNTHETIC_SUSPICIOUS"}).all()
        )
        self.assertTrue(
            first["evaluation_role"].isin({"NORMAL", "FOCAL_SUSPICIOUS"}).all()
        )
        self.assertTrue(first["transaction_id"].is_unique)

    def test_first_alert_records_timestamp_count_and_explanation_once(self):
        def mock_analysis(history, _seed):
            self.assertEqual(_seed, 42)
            score = 31 if len(history) >= 2 else 0
            level = "MEDIUM" if score >= 30 else "LOW"
            return {
                "rule_results": [
                    {
                        "account_id": "ACC-A",
                        "risk_indicators": [
                            {
                                "rule": "synthetic_test_rule",
                                "explanation": "A test indicator.",
                            }
                        ],
                    }
                ],
                "graph_results": {
                    "accounts": [
                        {"account_id": "ACC-A", "risk_indicators": []}
                    ]
                },
                "ml_results": [
                    {
                        "account_id": "ACC-A",
                        "is_anomaly": True,
                        "anomaly_score": 0.25,
                    }
                ],
                "risk_results": {
                    "accounts": [
                        {
                            "account_id": "ACC-A",
                            "risk_score": score,
                            "risk_level": level,
                            "method_contributions": {
                                name: {"explanation": f"{name} signal"}
                                for name in (
                                    "rule_based",
                                    "graph_based",
                                    "ml_anomaly",
                                )
                            },
                        }
                    ]
                },
            }

        with patch("synthetic_replay._analyze_history", side_effect=mock_analysis):
            result = replay_transactions(self._transactions())

        account = result["accounts"]["ACC-A"]
        self.assertEqual(account["first_alert_timestamp"], "2025-01-01T00:01:00+00:00")
        self.assertEqual(account["transaction_count_at_first_alert"], 2)
        self.assertEqual(len(account["risk_score_progression"]), 3)
        self.assertIn(
            "Illustrative risk reached MEDIUM",
            account["first_alert_explanation"]["summary"],
        )
        self.assertEqual(
            account["first_alert_explanation"]["signals"]["rule_based"][
                "indicators"
            ][0]["rule"],
            "synthetic_test_rule",
        )
        self.assertEqual(
            [len(step["new_alerts"]) for step in result["steps"]],
            [0, 1, 0],
        )

    def test_empty_input_returns_empty_replay_without_detection(self):
        with patch("synthetic_replay._analyze_history") as analyze:
            result = replay_transactions(pd.DataFrame())

        self.assertEqual(result["transaction_count"], 0)
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["accounts"], {})
        analyze.assert_not_called()

    @staticmethod
    def _transactions():
        return pd.DataFrame(
            [
                {
                    "transaction_id": "TX-3",
                    "sender": "ACC-C",
                    "receiver": "ACC-D",
                    "amount_paise": 200,
                    "timestamp": "2025-01-01T00:02:00Z",
                    "status": "SUCCESS",
                    "scenario_label": "NORMAL",
                    "evaluation_role": "NORMAL",
                },
                {
                    "transaction_id": "TX-1",
                    "sender": "ACC-A",
                    "receiver": "ACC-B",
                    "amount_paise": 100,
                    "timestamp": "2025-01-01T00:00:00Z",
                    "status": "SUCCESS",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                    "evaluation_role": "FOCAL_SUSPICIOUS",
                },
                {
                    "transaction_id": "TX-2",
                    "sender": "ACC-B",
                    "receiver": "ACC-C",
                    "amount_paise": 100,
                    "timestamp": "2025-01-01T00:01:00Z",
                    "status": "DECLINED",
                    "scenario_label": "SYNTHETIC_SUSPICIOUS",
                    "evaluation_role": "SOURCE_PARTICIPANT",
                },
            ]
        )

    @staticmethod
    def _benchmark_transactions(count):
        rows = []
        start = pd.Timestamp("2025-02-01T00:00:00Z")
        for index in range(count):
            rows.append(
                {
                    "transaction_id": f"BENCH-{index:03}",
                    "sender": f"ACC-{index % 3}",
                    "receiver": f"ACC-{(index + 1) % 3}",
                    "amount_paise": 1200 + index,
                    "timestamp": start + pd.Timedelta(minutes=index),
                    "status": "SUCCESS" if index % 4 else "DECLINED",
                    "scenario_label": (
                        "SYNTHETIC_SUSPICIOUS" if index % 2 else "NORMAL"
                    ),
                    "evaluation_role": (
                        "FOCAL_SUSPICIOUS" if index % 2 else "NORMAL"
                    ),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _legacy_reference_replay(transactions, seed):
        replay_data = transactions.copy()
        replay_data["_replay_input_order"] = range(len(replay_data))
        replay_data["_replay_timestamp"] = pd.to_datetime(
            replay_data["timestamp"], errors="coerce", utc=True
        )
        replay_data = replay_data.sort_values(
            ["_replay_timestamp", "_replay_input_order"], kind="mergesort"
        ).reset_index(drop=True)
        detector_columns = [
            column
            for column in transactions.columns
            if column not in {"scenario_label", "evaluation_role"}
        ]
        history = []
        accounts = {}
        steps = []
        for event_number, (_, row) in enumerate(
            replay_data.iterrows(), start=1
        ):
            event = row.to_dict()
            history.append({column: event[column] for column in detector_columns})
            step = SyntheticReplayTests._legacy_analyze_history(
                pd.DataFrame(history, columns=detector_columns), seed
            )
            new_alerts = []
            rules = {item["account_id"]: item for item in step["rule_results"]}
            graph = {
                item["account_id"]: item
                for item in step["graph_results"]["accounts"]
            }
            ml = {item["account_id"]: item for item in step["ml_results"]}
            for risk in step["risk_results"]["accounts"]:
                account_id = risk["account_id"]
                state = accounts.setdefault(
                    account_id,
                    {
                        "first_alert_timestamp": None,
                        "transaction_count_at_first_alert": None,
                        "risk_score_progression": [],
                        "first_alert_explanation": None,
                    },
                )
                state["risk_score_progression"].append(
                    {
                        "timestamp": event["_replay_timestamp"].isoformat(),
                        "transaction_count": event_number,
                        "risk_score": risk["risk_score"],
                        "risk_level": risk["risk_level"],
                    }
                )
                if (
                    state["first_alert_timestamp"] is None
                    and risk["risk_level"] in {"MEDIUM", "HIGH"}
                ):
                    explanation = synthetic_replay._detection_explanation(
                        account_id,
                        risk,
                        rules,
                        graph,
                        ml,
                    )
                    state["first_alert_timestamp"] = (
                        event["_replay_timestamp"].isoformat()
                    )
                    state["transaction_count_at_first_alert"] = event_number
                    state["first_alert_explanation"] = explanation
                    new_alerts.append(
                        {
                            "account_id": account_id,
                            "risk_score": risk["risk_score"],
                            "risk_level": risk["risk_level"],
                            "explanation": explanation,
                        }
                    )
            steps.append(
                {
                    "step": event_number,
                    "transaction_id": event["transaction_id"],
                    "timestamp": event["_replay_timestamp"].isoformat(),
                    "available_transaction_ids": [
                        item["transaction_id"] for item in history
                    ],
                    "risk_scores": {
                        item["account_id"]: item["risk_score"]
                        for item in step["risk_results"]["accounts"]
                    },
                    "new_alerts": new_alerts,
                }
            )
        return {
            "transaction_count": len(replay_data),
            "seed": seed,
            "steps": steps,
            "accounts": accounts,
            "disclaimer": synthetic_replay.DISCLAIMER,
        }

    @staticmethod
    def _legacy_analyze_history(history, seed):
        detector_input = history.drop(
            columns=["scenario_label", "evaluation_role"], errors="ignore"
        )
        rule_results = synthetic_replay.analyze_accounts(detector_input)
        ml_results = synthetic_replay.detect_account_anomalies(
            detector_input,
            random_state=seed,
        )
        graph_results = synthetic_replay.analyze_transaction_graph(detector_input)
        risk_results = synthetic_replay.aggregate_risk(
            rule_results,
            ml_results,
            graph_results,
        )
        return {
            "rule_results": rule_results,
            "ml_results": ml_results.to_dict(orient="records"),
            "graph_results": graph_results,
            "risk_results": risk_results,
        }


if __name__ == "__main__":
    unittest.main()
