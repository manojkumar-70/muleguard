import unittest

from benchmark_synthetic_streaming import benchmark_size


class SyntheticStreamingBenchmarkTests(unittest.TestCase):
    def test_isolated_benchmark_reports_both_protocols_and_required_metrics(self):
        strict, streaming = benchmark_size(8, warmup_events=2, seed=42)

        self.assertEqual(strict["protocol"], "strict_prefix_refit")
        self.assertEqual(streaming["protocol"], "stream_frozen_model")
        for result in (strict, streaming):
            self.assertEqual(result["transaction_count"], 8)
            self.assertEqual(result["seed"], 42)
            self.assertGreater(result["account_count"], 0)
            self.assertGreaterEqual(result["total_runtime_seconds"], 0)
            self.assertGreaterEqual(result["peak_memory_bytes"], 0)
            self.assertIn("event_latency_p50_seconds", result)
            self.assertIn("event_latency_p95_seconds", result)
            self.assertFalse(result["labels_in_detector_input"])
        self.assertEqual(streaming["warmup_events"], 2)
        self.assertEqual(streaming["scored_event_count"], 6)
        self.assertGreater(streaming["model_training_seconds"], 0)
        self.assertEqual(strict["warmup_events"], 0)


if __name__ == "__main__":
    unittest.main()
