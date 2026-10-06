import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pydantic import ValidationError

import synthetic_streaming
from benchmark_synthetic_replay import build_benchmark_transactions
from payment_service.schemas import NormalizedTransactionEvent
from payment_service.streaming_bridge import StreamingDetectionSession
from synthetic_streaming import PROTOCOL, run_synthetic_stream


class StreamingDetectionSessionTests(unittest.TestCase):
    def test_schema_validation_rejects_invalid_fields_and_extra_metadata(self):
        session = StreamingDetectionSession(warmup_events=2)
        with self.assertRaises(ValidationError):
            session.process_event(
                {
                    **_event("TXN-0001").model_dump(),
                    "scenario_label": "NORMAL",
                }
            )
        with self.assertRaises(ValidationError):
            session.process_event(
                _event("TXN-0001").model_dump() | {"amount_paise": 0}
            )
        self.assertEqual(session.warmup_state["observed_events"], 0)

    def test_detector_projection_contains_exactly_six_allowed_columns(self):
        session = StreamingDetectionSession(warmup_events=2)
        original_fit = synthetic_streaming._fit_stream_model
        observed_columns = []

        def capture_fit(transactions, seed):
            observed_columns.append(tuple(transactions.columns))
            return original_fit(transactions, seed)

        with patch(
            "synthetic_streaming._fit_stream_model",
            side_effect=capture_fit,
        ):
            session.process_event(_event("TXN-0001"))
            result = session.process_event(_event("TXN-0002", sender="ACC-0002"))

        self.assertEqual(observed_columns, [_DETECTOR_COLUMNS])
        self.assertNotIn("payment_id", result)
        self.assertNotIn("source", result)

    def test_label_and_evaluation_metadata_cannot_cross_detector_boundary(self):
        session = StreamingDetectionSession(warmup_events=2)
        detector_columns = []
        original_fit = synthetic_streaming._fit_stream_model
        original_features = synthetic_streaming._build_incremental_features

        def capture_fit(transactions, seed):
            detector_columns.append(tuple(transactions.columns))
            return original_fit(transactions, seed)

        def capture_features(transactions):
            detector_columns.append(tuple(transactions.columns))
            return original_features(transactions)

        with (
            patch(
                "synthetic_streaming._fit_stream_model",
                side_effect=capture_fit,
            ),
            patch(
                "synthetic_streaming._build_incremental_features",
                side_effect=capture_features,
            ),
        ):
            session.process_event(_event("TXN-0001"))
            session.process_event(_event("TXN-0002", sender="ACC-0002"))
            session.process_event(_event("TXN-0003", sender="ACC-0003"))

        self.assertEqual(
            detector_columns,
            [_DETECTOR_COLUMNS, _DETECTOR_COLUMNS, _DETECTOR_COLUMNS],
        )
        self.assertNotIn("scenario_label", detector_columns[0])
        self.assertNotIn("evaluation_role", detector_columns[0])
        self.assertNotIn("device_id", detector_columns[0])
        self.assertNotIn("ip_address", detector_columns[0])

    def test_duplicate_transaction_ids_are_rejected_without_changing_state(self):
        session = StreamingDetectionSession(warmup_events=3)
        first = _event("TXN-DUPLICATE")
        session.process_event(first)

        with self.assertRaisesRegex(ValueError, "Duplicate transaction_id"):
            session.process_event(first)
        self.assertEqual(session.warmup_state["observed_events"], 1)

    def test_warmup_boundary_emits_no_scores_until_following_event(self):
        session = StreamingDetectionSession(warmup_events=2)
        first = session.process_event(_event("TXN-0001"))
        second = session.process_event(_event("TXN-0002", sender="ACC-0002"))
        scored = session.process_event(_event("TXN-0003", sender="ACC-0003"))

        for result in (first, second):
            self.assertEqual(result["phase"], "warmup")
            self.assertFalse(result["detection_enabled"])
            self.assertEqual(result["risk_scores"], {})
            self.assertEqual(result["new_alerts"], [])
        self.assertTrue(second["warmup_state"]["complete"])
        self.assertEqual(second["warmup_state"]["remaining_events"], 0)
        self.assertEqual(scored["phase"], "scored")
        self.assertTrue(scored["detection_enabled"])

    def test_insufficient_account_warmup_fails_without_committing_last_event(self):
        session = StreamingDetectionSession(warmup_events=2)
        session.process_event(
            _event("TXN-0001", sender="ACC-SAME", receiver="ACC-SAME")
        )
        with self.assertRaisesRegex(ValueError, "two distinct accounts"):
            session.process_event(
                _event("TXN-0002", sender="ACC-SAME", receiver="ACC-SAME")
            )

        self.assertEqual(session.warmup_state["observed_events"], 1)
        successful = session.process_event(
            _event("TXN-0003", sender="ACC-SAME", receiver="ACC-OTHER")
        )
        self.assertTrue(successful["warmup_state"]["complete"])

    def test_warmup_buffer_is_timestamp_ordered_with_stable_timestamp_ties(self):
        session = StreamingDetectionSession(warmup_events=3)
        captured = {}
        original_fit = synthetic_streaming._fit_stream_model

        def capture_fit(transactions, seed):
            captured["ids"] = transactions["transaction_id"].tolist()
            return original_fit(transactions, seed)

        with patch(
            "synthetic_streaming._fit_stream_model",
            side_effect=capture_fit,
        ):
            session.process_event(_event("TXN-0001", minute=2))
            session.process_event(_event("TXN-0002", minute=1))
            session.process_event(_event("TXN-0003", minute=1, sender="ACC-0002"))

        self.assertEqual(captured["ids"], ["TXN-0002", "TXN-0003", "TXN-0001"])

    def test_post_warmup_rejects_older_timestamp_to_preserve_causality(self):
        session = StreamingDetectionSession(warmup_events=2)
        session.process_event(_event("TXN-0001", minute=1))
        session.process_event(_event("TXN-0002", minute=2, sender="ACC-0002"))

        with self.assertRaisesRegex(ValueError, "cannot precede"):
            session.process_event(_event("TXN-0003", minute=0))
        self.assertEqual(session.warmup_state["observed_events"], 2)

    def test_frozen_model_is_fitted_once_and_reused_for_scoring(self):
        session = StreamingDetectionSession(warmup_events=2)
        original_fit = synthetic_streaming._fit_stream_model
        with patch(
            "synthetic_streaming._fit_stream_model",
            wraps=original_fit,
        ) as fit:
            session.process_event(_event("TXN-0001"))
            session.process_event(_event("TXN-0002", sender="ACC-0002"))
            third = session.process_event(_event("TXN-0003", sender="ACC-0003"))
            fourth = session.process_event(_event("TXN-0004", sender="ACC-0004"))

        self.assertEqual(fit.call_count, 1)
        self.assertTrue(third["warmup_state"]["model_frozen"])
        self.assertTrue(fourth["detection_enabled"])

    def test_scored_events_use_only_current_causal_prefix(self):
        session = StreamingDetectionSession(warmup_events=2)
        captured_prefixes = []
        original_rules = synthetic_streaming.analyze_accounts
        original_graph = synthetic_streaming.analyze_transaction_graph

        def capture_rules(transactions):
            captured_prefixes.append(transactions["transaction_id"].tolist())
            return original_rules(transactions)

        def capture_graph(transactions):
            self.assertEqual(
                tuple(transactions.columns),
                _DETECTOR_COLUMNS,
            )
            return original_graph(transactions)

        with (
            patch(
                "synthetic_streaming.analyze_accounts",
                side_effect=capture_rules,
            ),
            patch(
                "synthetic_streaming.analyze_transaction_graph",
                side_effect=capture_graph,
            ),
        ):
            session.process_event(_event("TXN-0001"))
            session.process_event(_event("TXN-0002", sender="ACC-0002"))
            session.process_event(_event("TXN-0003", sender="ACC-0003"))
            session.process_event(_event("TXN-0004", sender="ACC-0004"))

        self.assertEqual(
            captured_prefixes,
            [
                ["TXN-0001", "TXN-0002", "TXN-0003"],
                ["TXN-0001", "TXN-0002", "TXN-0003", "TXN-0004"],
            ],
        )

    def test_scored_result_is_account_level_not_transaction_level(self):
        session = StreamingDetectionSession(warmup_events=2)
        session.process_event(_event("TXN-0001"))
        session.process_event(_event("TXN-0002", sender="ACC-0002"))

        result = session.process_event(_event("TXN-0003", sender="ACC-0003"))

        self.assertEqual(result["protocol"], PROTOCOL)
        self.assertEqual(result["transaction_id"], "TXN-0003")
        self.assertEqual(result["phase"], "scored")
        self.assertIsInstance(result["risk_scores"], dict)
        self.assertTrue(result["risk_scores"])
        self.assertTrue(
            all(account_id.startswith("ACC-") for account_id in result["risk_scores"])
        )
        self.assertNotIn("transaction_risk_score", result)
        self.assertIsInstance(result["new_alerts"], list)

    def test_existing_batch_streaming_function_regression(self):
        result = run_synthetic_stream(
            build_benchmark_transactions(8),
            warmup_events=2,
            seed=19,
        )
        self.assertEqual(result["protocol"], PROTOCOL)
        self.assertEqual(result["transaction_count"], 8)
        self.assertEqual(result["warmup_event_count"], 2)
        self.assertEqual(result["scored_event_count"], 6)
        self.assertEqual(
            [step["phase"] for step in result["steps"][:2]],
            ["warmup", "warmup"],
        )


_DETECTOR_COLUMNS = (
    "transaction_id",
    "sender",
    "receiver",
    "amount_paise",
    "timestamp",
    "status",
)


def _event(
    transaction_id: str,
    sender: str = "ACC-0001",
    receiver: str = "ACC-DEST",
    minute: int = 0,
) -> NormalizedTransactionEvent:
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(
        minutes=minute
    )
    return NormalizedTransactionEvent(
        transaction_id=transaction_id,
        sender=sender,
        receiver=receiver,
        amount_paise=1000,
        currency="INR",
        timestamp=timestamp,
        status="SUCCESS",
        device_id="DEV-TEST",
        ip_address="192.0.2.1",
        payment_id="PAY-TEST",
        source="PAYMENT_EVENT",
    )


if __name__ == "__main__":
    unittest.main()
