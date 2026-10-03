"""Isolated exact-replay versus frozen-model streaming benchmarks."""

import argparse
import json
import os
import subprocess
import sys
import time
from statistics import median

from sklearn.pipeline import Pipeline

import synthetic_replay
from benchmark_synthetic_replay import (
    _peak_memory_bytes,
    build_benchmark_transactions,
)
from synthetic_replay import replay_transactions
from synthetic_streaming import run_synthetic_stream


DEFAULT_SIZES = (50, 100, 250)


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _run_worker(transaction_count, warmup_events, seed, mode):
    transactions = build_benchmark_transactions(transaction_count)
    event_latencies = []
    pipeline_fit_seconds = []
    original_pipeline_fit = Pipeline.fit

    def timed_pipeline_fit(model, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_pipeline_fit(model, *args, **kwargs)
        finally:
            pipeline_fit_seconds.append(time.perf_counter() - started)

    Pipeline.fit = timed_pipeline_fit

    if mode == "strict":
        original_analyze_history = synthetic_replay._analyze_history

        def timed_analysis(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original_analyze_history(*args, **kwargs)
            finally:
                event_latencies.append(time.perf_counter() - started)

        synthetic_replay._analyze_history = timed_analysis
        try:
            started = time.perf_counter()
            result = replay_transactions(transactions, seed=seed)
            total_seconds = time.perf_counter() - started
        finally:
            synthetic_replay._analyze_history = original_analyze_history
        scored_event_latencies = event_latencies
        account_count = len(result["accounts"])
        scored_events = len(result["steps"])
        warmup_setup_seconds = None
    elif mode == "streaming":
        started = time.perf_counter()
        result = run_synthetic_stream(
            transactions,
            warmup_events=warmup_events,
            seed=seed,
            include_timing=True,
        )
        total_seconds = time.perf_counter() - started
        warmup_setup_seconds = result["timing"]["model_training_seconds"]
        scored_event_latencies = result["timing"]["per_event_seconds"][
            warmup_events:
        ]
        account_count = max(
            (len(step["risk_scores"]) for step in result["steps"]),
            default=0,
        )
        scored_events = result["scored_event_count"]
    else:
        raise ValueError(f"Unsupported benchmark mode: {mode}")

    Pipeline.fit = original_pipeline_fit
    peak_bytes, memory_metric = _peak_memory_bytes()
    return {
        "mode": mode,
        "protocol": (
            "strict_prefix_refit"
            if mode == "strict"
            else "stream_frozen_model"
        ),
        "transaction_count": transaction_count,
        "analysis_steps": len(result["steps"]),
        "scored_event_count": scored_events,
        "account_count": account_count,
        "seed": seed,
        "warmup_events": warmup_events if mode == "streaming" else 0,
        "model_training_seconds": round(sum(pipeline_fit_seconds), 3),
        "warmup_model_setup_seconds": (
            round(warmup_setup_seconds, 3)
            if warmup_setup_seconds is not None
            else None
        ),
        "event_latency_p50_seconds": round(median(scored_event_latencies), 6),
        "event_latency_p95_seconds": round(
            _percentile(scored_event_latencies, 0.95), 6
        ),
        "total_runtime_seconds": round(total_seconds, 3),
        "peak_memory_bytes": peak_bytes,
        "peak_memory_mib": round(peak_bytes / (1024 * 1024), 2),
        "memory_metric": memory_metric,
        "labels_in_detector_input": False,
    }


def benchmark_size(transaction_count, warmup_events, seed=42):
    """Run strict and streaming modes in separate fresh processes."""
    results = []
    for mode in ("strict", "streaming"):
        command = [
            sys.executable,
            os.path.abspath(__file__),
            "--worker",
            str(transaction_count),
            "--warmup-events",
            str(warmup_events),
            "--seed",
            str(seed),
            "--mode",
            mode,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode:
            raise RuntimeError(
                f"{mode} benchmark failed for {transaction_count} events: "
                f"{completed.stderr.strip()}"
            )
        try:
            results.append(json.loads(completed.stdout))
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"{mode} benchmark returned invalid JSON: {completed.stdout!r}"
            ) from error
    return results


def run_benchmarks(sizes=DEFAULT_SIZES, warmup_events=20, seed=42):
    sizes = tuple(sizes)
    if not sizes or any(size < 1 for size in sizes):
        raise ValueError("sizes must contain positive integers")
    if not isinstance(warmup_events, int) or isinstance(warmup_events, bool):
        raise ValueError("warmup_events must be an integer")
    if warmup_events < 1 or any(warmup_events >= size for size in sizes):
        raise ValueError("warmup_events must be positive and less than every size")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")

    return {
        "benchmark_data": "deterministic in-memory synthetic workload",
        "seed": seed,
        "warmup_events": warmup_events,
        "results_by_size": {
            str(size): benchmark_size(size, warmup_events, seed)
            for size in sizes
        },
        "interpretation": (
            "Strict replay refits IsolationForest on every prefix. Streaming trains "
            "once on the first warm-up events and scores later prefixes with that "
            "frozen model; its metrics and anomaly calibration are not equivalent "
            "to strict chronological evaluation."
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compare strict synthetic replay with frozen-model streaming."
    )
    parser.add_argument("--sizes", nargs="+", type=int, default=DEFAULT_SIZES)
    parser.add_argument("--warmup-events", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--mode",
        choices=("strict", "streaming"),
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args()
    try:
        if arguments.worker is not None:
            result = _run_worker(
                arguments.worker,
                arguments.warmup_events,
                arguments.seed,
                arguments.mode,
            )
        else:
            result = run_benchmarks(
                arguments.sizes,
                arguments.warmup_events,
                arguments.seed,
            )
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
