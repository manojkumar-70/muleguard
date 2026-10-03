"""Benchmark exact chronological replay on deterministic in-memory data."""

import argparse
import json
import os
import subprocess
import sys
import time

import pandas as pd

from synthetic_replay import replay_transactions


DEFAULT_SIZES = (50, 100, 250)


def build_benchmark_transactions(transaction_count):
    """Create a repeatable synthetic event stream without touching project data."""
    if not isinstance(transaction_count, int) or isinstance(transaction_count, bool):
        raise ValueError("transaction_count must be an integer")
    if transaction_count < 1:
        raise ValueError("transaction_count must be positive")

    rows = []
    start = pd.Timestamp("2025-01-01T00:00:00Z")
    for index in range(transaction_count):
        sender_index = index % 24
        receiver_index = (index * 7 + 11) % 24
        if sender_index == receiver_index:
            receiver_index = (receiver_index + 1) % 24
        rows.append(
            {
                "transaction_id": f"BENCH-{index:05d}",
                "sender": f"ACC-BENCH-{sender_index:03d}",
                "receiver": f"ACC-BENCH-{receiver_index:03d}",
                "amount_paise": 10_000 + (index * 137) % 500_000,
                "timestamp": start + pd.Timedelta(seconds=30 * index),
                "status": "DECLINED" if index % 13 == 0 else "SUCCESS",
                "scenario_label": (
                    "SYNTHETIC_SUSPICIOUS" if index % 5 == 0 else "NORMAL"
                ),
                "evaluation_role": (
                    "FOCAL_SUSPICIOUS" if index % 5 == 0 else "NORMAL"
                ),
            }
        )
    return pd.DataFrame(rows)


def benchmark_size(transaction_count, seed=42):
    """Run one benchmark in a fresh subprocess for isolated peak memory."""
    command = [
        sys.executable,
        os.path.abspath(__file__),
        "--worker",
        str(transaction_count),
        "--seed",
        str(seed),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(
            f"Replay benchmark failed for {transaction_count} events: "
            f"{completed.stderr.strip()}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Replay benchmark returned invalid JSON: {completed.stdout!r}"
        ) from error
    return result


def _peak_memory_bytes():
    import ctypes
    from ctypes import wintypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise RuntimeError("Peak working-set benchmarking requires Windows.")

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = windll.kernel32.GetCurrentProcess()
    get_info = windll.psapi.GetProcessMemoryInfo
    get_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    get_info.restype = wintypes.BOOL
    if not get_info(handle, ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.PeakWorkingSetSize), "peak_process_working_set"


def _run_worker(transaction_count, seed):
    transactions = build_benchmark_transactions(transaction_count)
    started = time.perf_counter()
    result = replay_transactions(transactions, seed=seed)
    runtime_seconds = time.perf_counter() - started

    peak_memory, memory_metric = _peak_memory_bytes()

    return {
        "transaction_count": transaction_count,
        "analysis_steps": len(result["steps"]),
        "account_count": len(result["accounts"]),
        "seed": seed,
        "runtime_seconds": round(runtime_seconds, 3),
        "peak_memory_bytes": peak_memory,
        "peak_memory_mib": (
            round(peak_memory / (1024 * 1024), 2)
            if peak_memory is not None
            else None
        ),
        "memory_metric": memory_metric,
        "execution_mode": "exact_chronological_prefix_refit",
        "labels_in_detector_input": False,
    }


def run_benchmarks(sizes=DEFAULT_SIZES, seed=42):
    """Run benchmark sizes in isolated child processes."""
    sizes = tuple(sizes)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not sizes or any(
        not isinstance(size, int) or isinstance(size, bool) or size < 1
        for size in sizes
    ):
        raise ValueError("sizes must contain positive integers")
    return {
        "mode": "exact chronological replay; no batching, skipped events, or model reuse",
        "seed": seed,
        "results": [benchmark_size(size, seed) for size in sizes],
        "model_reuse_assessment": (
            "Not enabled: each prefix changes account features and the IsolationForest "
            "fit population, while aggregate risk ranks anomaly scores within that "
            "prefix. Reusing a fitted model or scoring multiple future prefixes "
            "together would change detector semantics or expose future population "
            "information."
        ),
        "limitations": [
            "Synthetic workload and timings are hardware/runtime dependent.",
            "Peak process working set includes interpreter and dependencies, not just replay allocations.",
            "No 1,200-event runtime is extrapolated as a measured result.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark exact chronological synthetic replay."
    )
    parser.add_argument("--sizes", nargs="+", type=int, default=DEFAULT_SIZES)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    arguments = parser.parse_args()

    try:
        if arguments.worker is not None:
            result = _run_worker(arguments.worker, arguments.seed)
        else:
            result = run_benchmarks(arguments.sizes, arguments.seed)
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
