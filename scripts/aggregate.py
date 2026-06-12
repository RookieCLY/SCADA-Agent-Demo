#!/usr/bin/env python3
"""
scripts/aggregate.py

Recursively walks the results directory to locate runs containing traces.jsonl and _meta.json.
Evaluates trace entries on the fly, computes model pricing cost_usd, joins offline judge metrics,
and saves the final aggregated data to results/aggregated.parquet.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import polars as pl

from eval.schema import load_golden_dataset, GoldenRecord
from eval.metrics import evaluate_trace

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("aggregate")


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Loads a JSONL file into a list of dicts."""
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception as e:
                logger.warning("Failed to parse line %d in %s: %s", line_num, path, e)
    return records


def load_json(path: Path) -> Dict[str, Any]:
    """Loads a JSON file into a dict."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load JSON file %s: %s", path, e)
        return {}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Computes cost in USD on the fly based on token counts and model pricing:
    - mimo-v2.5-pro: $0.0015 / 1K input, $0.005 / 1K output
    - deepseek-chat: $0.00014 / 1K input, $0.00028 / 1K output
    - fallback pricing: $0.002 / 1K input, $0.008 / 1K output
    """
    if not model:
        model = "unknown"
    
    model_lower = model.lower()
    if "mimo-v2.5-pro" in model_lower:
        return (input_tokens / 1000.0) * 0.0015 + (output_tokens / 1000.0) * 0.005
    elif "deepseek-chat" in model_lower or "deepseek-v3" in model_lower:
        return (input_tokens / 1000.0) * 0.00014 + (output_tokens / 1000.0) * 0.00028
    else:
        return (input_tokens / 1000.0) * 0.002 + (output_tokens / 1000.0) * 0.008


def safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except:
        return None

def safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except:
        return None

def safe_str(v) -> str | None:
    if v is None:
        return None
    return str(v)

def safe_bool(v) -> bool | None:
    if v is None:
        return None
    try:
        return bool(v)
    except:
        return None


def main():
    parser = argparse.ArgumentParser(description="Aggregate SCADA evaluation traces and metadata.")
    parser.add_argument(
        "--results-root",
        type=str,
        default="results",
        help="Root directory containing run results."
    )
    parser.add_argument(
        "--golden-dataset",
        type=str,
        default="eval/golden_dataset.jsonl",
        help="Path to the golden dataset JSONL file."
    )
    parser.add_argument(
        "--output",
        type=str,
        default="results/aggregated.parquet",
        help="Path to output Parquet file."
    )
    args = parser.parse_args()

    results_root = Path(args.results_root)
    golden_path = Path(args.golden_dataset)
    output_path = Path(args.output)

    if not golden_path.exists():
        logger.error("Golden dataset not found at: %s", golden_path)
        return

    logger.info("Loading golden dataset from %s", golden_path)
    golden_records = load_golden_dataset(golden_path)
    golden_by_id = {record.id: record for record in golden_records}
    logger.info("Loaded %d golden records.", len(golden_records))

    # We will walk results_root to find folders with traces.jsonl and _meta.json
    logger.info("Scanning %s recursively for runs...", results_root)
    run_dirs = []
    for path in results_root.rglob("traces.jsonl"):
        run_dir = path.parent
        meta_path = run_dir / "_meta.json"
        if meta_path.exists():
            run_dirs.append(run_dir)

    logger.info("Found %d runs to aggregate.", len(run_dirs))

    all_rows = []

    for run_dir in run_dirs:
        logger.info("Processing run: %s", run_dir)
        meta = load_json(run_dir / "_meta.json")
        traces = load_jsonl(run_dir / "traces.jsonl")
        
        # Load judge records if judges.jsonl exists
        judges_path = run_dir / "judges.jsonl"
        judge_records = load_jsonl(judges_path)
        judge_by_trace_id = {}
        for jr in judge_records:
            t_id = jr.get("trace_id")
            if t_id:
                judge_by_trace_id[t_id] = jr

        config_name = meta.get("config_name") or run_dir.parent.parent.name
        model = meta.get("model") or "unknown"

        logger.info("Found %d traces and %d judge entries in %s", len(traces), len(judge_records), run_dir)

        for trace_num, trace in enumerate(traces, 1):
            if not isinstance(trace, dict):
                logger.warning("Trace %d in %s is not a dictionary. Skipping.", trace_num, run_dir)
                continue
            query = trace.get("query") or {}
            golden_id = query.get("golden_id")
            if not golden_id:
                logger.warning("Trace %d in %s has no golden_id. Skipping.", trace_num, run_dir)
                continue

            golden = golden_by_id.get(golden_id)
            if not golden:
                logger.warning("Golden record %s referenced in trace not found. Skipping.", golden_id)
                continue

            try:
                # 1. Deterministic evaluation metrics
                row = evaluate_trace(trace, golden)

                # 2. Adjust or ensure experiment-level metadata
                if not row.get("config_name"):
                    row["config_name"] = config_name
                if not row.get("model"):
                    row["model"] = model

                # 3. Calculate token calling cost on the fly
                input_tokens = row.get("input_tokens") or 0
                output_tokens = row.get("output_tokens") or 0
                row["cost_usd"] = calculate_cost(row["model"], input_tokens, output_tokens)

                # 4. Join with judge metrics if available
                trace_id = row.get("trace_id")
                if trace_id and trace_id in judge_by_trace_id:
                    jr = judge_by_trace_id[trace_id]
                    row["judge_completion"] = jr.get("task_completion")
                    row["judge_tool"] = jr.get("tool_correctness")
                    row["judge_param"] = jr.get("parameter_correctness")
                    row["judge_efficiency"] = jr.get("step_efficiency")
                else:
                    row["judge_completion"] = None
                    row["judge_tool"] = None
                    row["judge_param"] = None
                    row["judge_efficiency"] = None

                all_rows.append(row)
            except Exception as e:
                logger.exception("Error evaluating trace %s in %s: %s", trace.get("trace_id"), run_dir, e)

    if not all_rows:
        logger.error("No traces were aggregated. Parquet file will not be created.")
        return

    logger.info("Aggregated %d trace rows in total. Creating Polars DataFrame...", len(all_rows))
    
    # We will build clean rows containing exactly the 25 key columns with explicit types.
    clean_rows = []
    for row in all_rows:
        clean_row = {
            "config_name": safe_str(row.get("config_name")),
            "model": safe_str(row.get("model")),
            "golden_id": safe_str(row.get("golden_id")),
            "rep": safe_int(row.get("rep")),
            "complexity": safe_str(row.get("complexity")),
            "domain": safe_str(row.get("domain")),
            "visible_count_mean": safe_float(row.get("visible_count_mean")) or 0.0,
            "tool_selection_f1": safe_float(row.get("tool_selection_f1")) or 0.0,
            "hallucinated": safe_bool(row.get("hallucinated")) or False,
            "out_of_scope": safe_bool(row.get("out_of_scope")) or False,
            "param_valid": safe_bool(row.get("param_valid")) or False,
            "task_success": safe_bool(row.get("task_success")) or False,
            "step_count": safe_int(row.get("step_count")) or 0,
            "order_distance": safe_float(row.get("order_distance")),
            "input_tokens": safe_int(row.get("input_tokens")) or 0,
            "output_tokens": safe_int(row.get("output_tokens")) or 0,
            "cost_usd": safe_float(row.get("cost_usd")) or 0.0,
            "e2e_latency_ms": safe_float(row.get("e2e_latency_ms")),
            "judge_completion": safe_float(row.get("judge_completion")),
            "judge_tool": safe_float(row.get("judge_tool")),
            "judge_param": safe_float(row.get("judge_param")),
            "judge_efficiency": safe_float(row.get("judge_efficiency")),
            "trace_id": safe_str(row.get("trace_id")),
            "loop_stuck": safe_bool(row.get("loop_stuck")) or False,
            "error_code": safe_str(row.get("error_code")),
        }
        clean_rows.append(clean_row)

    schema = {
        "config_name": pl.String,
        "model": pl.String,
        "golden_id": pl.String,
        "rep": pl.Int64,
        "complexity": pl.String,
        "domain": pl.String,
        "visible_count_mean": pl.Float64,
        "tool_selection_f1": pl.Float64,
        "hallucinated": pl.Boolean,
        "out_of_scope": pl.Boolean,
        "param_valid": pl.Boolean,
        "task_success": pl.Boolean,
        "step_count": pl.Int64,
        "order_distance": pl.Float64,
        "input_tokens": pl.Int64,
        "output_tokens": pl.Int64,
        "cost_usd": pl.Float64,
        "e2e_latency_ms": pl.Float64,
        "judge_completion": pl.Float64,
        "judge_tool": pl.Float64,
        "judge_param": pl.Float64,
        "judge_efficiency": pl.Float64,
        "trace_id": pl.String,
        "loop_stuck": pl.Boolean,
        "error_code": pl.String,
    }
    df = pl.DataFrame(clean_rows, schema=schema)
    logger.info("DataFrame schema: %s", df.schema)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output_path)
    logger.info("Successfully wrote aggregated results to %s", output_path)


if __name__ == "__main__":
    main()
