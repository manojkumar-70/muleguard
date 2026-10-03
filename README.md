# MuleGuard AI

MuleGuard AI is a student cybersecurity project for detecting suspicious synthetic financial transaction networks. It will combine rule-based analysis, machine learning, graph analysis, and explainable risk scoring.

The project is planned to use Java for payment simulation, Python for AI and data analysis (Pandas, NumPy, Scikit-learn, and NetworkX), FastAPI for the backend API, and Streamlit for the dashboard.

## Synthetic analysis API

The local FastAPI backend analyzes only synthetic transactions. It does not
connect to payment services or perform account actions. From `ai-engine`, start
the server with:

```powershell
python -m pip install -r .\requirements.txt
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

The API provides `GET /health`, `GET /summary`, `GET /alerts`,
`GET /accounts/{account_id}`, and `POST /analyze`. Run its tests from
`ai-engine` with `python -m unittest -v test_api`.

## Local dashboard

The Streamlit dashboard uses the same local synthetic CSV and analysis modules.
From the project root, install the AI-engine requirements and launch it with:

```powershell
python -m pip install -r .\MuleGuardAI\ai-engine\requirements.txt
python -m streamlit run .\MuleGuardAI\dashboard\app.py
```

The dashboard defaults to `http://localhost:8501`. Run its tests from the
`dashboard` directory with `python -m unittest -v test_dashboard`.

## Chronological synthetic transaction replay

Phase 1 replay processes the existing synthetic transaction records in timestamp
order and re-runs the existing rules, IsolationForest, graph analysis, and risk
aggregation against each growing history prefix. Each event is analyzed
individually; equal timestamps retain their CSV/input order. The optional seed
sets IsolationForest's random state. Evaluation labels are excluded from
detection, and replay performs no real payment, account restriction, recovery,
or intervention.

From `ai-engine`, run replay against the current synthetic CSV with:

```powershell
python -c "from data_loader import load_transactions; from synthetic_replay import replay_transactions; print(replay_transactions(load_transactions())['transaction_count'])"
python -m unittest -v test_synthetic_replay
```

The replay result contains the transaction IDs available at each step, per-step
account risk scores, each account's risk progression, and first-alert timestamp,
transaction count, and signal explanation. Empty input returns an empty replay.
Scores are illustrative and are not proof of criminal activity.

Replay prepares the label-free, chronologically sorted transaction frame once,
then copies only the current prefix for each detector call. This avoids
rebuilding each prefix from Python dictionaries while retaining an isolated
prefix, so future rows are not accessible through the detector's DataFrame
backing memory. The IsolationForest is still fitted on every prefix with all
existing settings and all events are still analyzed. Run the controlled
reference-versus-optimized replay benchmark as part of:

```powershell
python -m unittest -v test_synthetic_replay
```

The full 1,200-event replay remains compute-intensive because the unchanged
200-tree IsolationForest is refitted at every event; the prefix optimization
does not remove or skip any replay analysis step.

### Opt-in frozen-model synthetic streaming

`synthetic_streaming.py` provides a separate streaming protocol and does not
replace strict replay, the evaluator, API routes, or dashboard analysis. Its
first `warmup_events` chronological events train the existing median-imputer,
standard-scaler, 200-tree IsolationForest pipeline on unlabeled account
features. No detection is emitted during warm-up; the fitted model is frozen
for later events. The model seed is explicit. Rule and graph analysis reuse
their existing implementations on each as-of-event prefix, and risk continues
to use the existing aggregation formula and thresholds.

From `ai-engine`, run the new protocol on a loaded synthetic dataset:

```powershell
python -c "from data_loader import load_transactions; from synthetic_streaming import run_synthetic_stream; print(run_synthetic_stream(load_transactions(), warmup_events=50, seed=42)['protocol'])"
python -m unittest -v test_synthetic_streaming
```

Compare it with strict replay on deterministic in-memory synthetic events:

```powershell
python .\benchmark_synthetic_streaming.py
python .\benchmark_synthetic_streaming.py --sizes 50 100 250 --warmup-events 20 --seed 42
```

The benchmark runs both modes in separate processes, reports model-training
time, per-scored-event p50/p95 latency, total runtime, peak process working set,
event/account counts, seed, and warm-up size. It does not read or overwrite
project datasets. The protocols are intentionally not semantically equivalent:
strict replay refits on each prefix, whereas streaming fits only on its initial
unlabeled warm-up and scores later account populations with the frozen model.
Consequently anomaly calibration and detection metrics from one protocol must
not be represented as results from the other. Streaming still re-analyzes the
growing prefix with existing rule and graph analyzers and rebuilds point-in-time
account features; it is not a constant-time incremental graph implementation.
All records remain synthetic, and alerts are illustrative review signals only.

### Replay performance benchmarks

The benchmark generates deterministic, in-memory synthetic inputs and runs each
size in an isolated process. It reports elapsed time and peak process working
set on Windows (or Python-tracked peak allocation on other platforms). It never
reads or writes project CSV files:

```powershell
python .\benchmark_synthetic_replay.py
python .\benchmark_synthetic_replay.py --sizes 50 100 250 --seed 42
```

The benchmark uses exact per-event prefix replay. Model reuse is intentionally
not enabled: IsolationForest is fit against the current prefix's account
features, and aggregate risk ranks anomaly scores within that prefix. Reusing
one model or evaluating multiple prefixes together would change the reference
semantics. Benchmarks are hardware-dependent; process working set includes the
Python runtime and imported libraries.

## Early-warning evaluation

Phase 2 evaluates replay timing against the separate evaluation-role manifest.
It reports first-alert position and timestamp, latency since each account's
first observed transaction, detection/missed counts, checkpoint rates, and the
legitimate-account false-alert rate. Scenario labels are summarized only after
replay; neither labels nor future transactions enter detector inputs. Missed
accounts are excluded from latency averages rather than treated as zero-delay.

Run from `ai-engine`:

```powershell
python .\evaluate_early_warning.py
python .\evaluate_early_warning.py --checkpoints 100 300 600 1200 --seed 42
python -m unittest -v test_early_warning_evaluation
```

The evaluation reads existing synthetic CSV and role-manifest files without
modifying them. Results are synthetic research measurements, not real-world
financial accuracy.

## Reproducible full-pipeline evaluation

The multi-seed evaluator compiles and runs the Java generator in temporary
directories, then evaluates the canonical rules, transaction-graph indicators,
IsolationForest, and existing risk aggregation on each identical dataset.
Evaluation roles are joined only after scoring; `scenario_label` and
`evaluation_role` are excluded from all detector inputs. Project CSV files are
not overwritten. It requires a Java 17+ JDK and uses five fixed seeds by
default. From the project root:

```powershell
Set-Location .\ai-engine
python .\evaluate_rule_engine.py
python .\evaluate_rule_engine.py --seeds 11 22 33 44 55
python -m unittest discover -v
Set-Location ..\dashboard
python -m unittest discover -v
```

Each method reports focal-account and broader source/focal/destination
participant metrics, per seed and as mean and standard deviation. Positives are
`FOCAL_SUSPICIOUS` for focal evaluation and all three suspicious participant
roles for participant evaluation; participant negatives are `NORMAL`. The
matrix order is `[[TN, FP], [FN, TP]]`, with actual rows and predicted columns.
The reported method policies use existing thresholds/configuration without
tuning: rule score at the canonical medium threshold, one or more graph
indicators, IsolationForest's existing anomaly decision, and aggregate MEDIUM
or HIGH risk. Results are synthetic research measurements, not estimates of
real-world banking accuracy.
