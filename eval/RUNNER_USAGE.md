# Eval Runner Usage

`eval/runner.py` runs Golden Dataset cases through the SCADA agent and writes trace artifacts for later deterministic metrics and LLM-as-Judge scoring.

## Basic Commands

Run one or more specific cases:

```powershell
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --golden-ids golden-001,golden-002 --reps 1
```

Run a small sample:

```powershell
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --dataset-sample 5 --reps 1
```

Run the full dataset using config defaults:

```powershell
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --all
```

Use mock provider for a cheap smoke test:

```powershell
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --golden-ids golden-001 --provider mock --model mock --reps 1 --max-reruns 0
```

## Output Layout

Each run writes one directory:

```text
results/{config_name}/{model}/{run_id}/
```

Expected files:

- `traces.jsonl`: one trace per golden case repetition
- `_meta.json`: run metadata, config hash, model, dataset split, counts
- `_config.yaml`: copied config snapshot
- `_failures.jsonl`: technical failures and retry exhaustion records
- `judges.jsonl`: placeholder for later offline judge output

## Resume And Retry

Use a fixed run ID if you want to resume later:

```powershell
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --dataset-sample 5 --run-id dev_sample_01
```

Resume the same run:

```powershell
uv run python -m eval.runner --config configs\F_full_four_in_one.yaml --dataset-sample 5 --run-id dev_sample_01 --resume
```

`--max-reruns 3` is the default. It retries technical failures such as exceptions or unknown/early-terminated traces. Business-level tool errors are preserved in the trace and scored later by metrics.

## Compute Metrics

After a runner pass, compute deterministic metrics:

```powershell
uv run python -m eval.metrics --dataset eval\golden_dataset.jsonl --traces results\F_full_four_in_one\mock\dev_sample_01\traces.jsonl --output results\F_full_four_in_one\mock\dev_sample_01\metrics.jsonl --summary-output results\F_full_four_in_one\mock\dev_sample_01\metrics_summary.json
```

Metrics are deterministic. Rubrics are only used later by the LLM-as-Judge layer.

## Quick Test Checklist

Run CLI help:

```powershell
uv run python -m eval.runner --help
uv run python -m eval.metrics --help
```

Run focused tests:

```powershell
uv run python -m pytest tests\test_eval_metrics.py tests\test_golden_schema.py tests\test_tracer.py tests\test_configs.py -q
```

Lint touched eval files:

```powershell
uv run ruff check eval\runner.py eval\metrics.py
```

