"""Test all free GLM models (glm-4-flash, glm-4v-flash) on a single case and report latency.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Make the repo root importable
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.config import load_config
from agent.orchestrator import assemble, build_demo_world
from agent.llm import build_llm
from agent.tool_registry import build_default_registry


CONFIG_PATH = "configs/glm_smoke.yaml"
QUERY = "给反应釜1的TEMP_101点位加一个高温报警,阈值80度,优先级high,ID用alarm_high_temp_101"
MODELS = ["glm-4-flash", "glm-4v-flash"]


def run_model_test(model_name: str) -> dict:
    print(f"\n======================================== Testing {model_name} ========================================")
    
    # Load config and override model name
    cfg = load_config(CONFIG_PATH)
    cfg.model.name = model_name
    # Restrict to core 39 tools to optimize latency and token limits for free tier
    cfg.tool_count = 39

    # Re-build registry, llm and agent
    registry = build_default_registry(tool_count=cfg.tool_count)
    
    try:
        agent = assemble(CONFIG_PATH)
        # Manually override the built LLM with the custom model
        agent.config.model.name = model_name
        agent.config.tool_count = 39
        agent.registry = registry
        agent.llm = build_llm(cfg.model, registry=registry, arch=cfg.architecture)
        
        world = build_demo_world()
        t_start = time.perf_counter()
        
        record = agent.run(
            QUERY,
            golden_id=f"glm-free-test-{model_name}",
            initial_world=world,
            complexity="simple",
            domain="alarm",
        )
        
        duration = time.perf_counter() - t_start
        exe = record["execution"]
        
        llm_calls = record.get("llm_calls", [])
        total_in_tokens = sum(c.get("input_tokens", 0) for c in llm_calls)
        total_out_tokens = sum(c.get("output_tokens", 0) for c in llm_calls)
        avg_llm_latency = sum(c.get("latency_ms", 0.0) for c in llm_calls) / len(llm_calls) if llm_calls else 0.0
        
        tool_called = len(record["tool_calls"]) > 0
        success = not exe["early_terminated"] and any(c["result_ok"] for c in record["tool_calls"])
        
        return {
            "model": model_name,
            "status": "SUCCESS" if success else "FAILED",
            "early_terminated": exe["early_terminated"],
            "total_turns": exe["total_turns"],
            "tool_calls": len(record["tool_calls"]),
            "input_tokens": total_in_tokens,
            "output_tokens": total_out_tokens,
            "e2e_latency_s": duration,
            "avg_llm_latency_ms": avg_llm_latency,
            "error": None
        }
    except Exception as e:
        print(f"Error testing model {model_name}: {e}")
        return {
            "model": model_name,
            "status": "ERROR",
            "early_terminated": True,
            "total_turns": 0,
            "tool_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "e2e_latency_s": 0.0,
            "avg_llm_latency_ms": 0.0,
            "error": str(e)
        }


def main():
    results = []
    for m in MODELS:
        res = run_model_test(m)
        results.append(res)
        
    print("\n\n======================================== RESULTS SUMMARY ========================================")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    
    # Write to a JSON file
    out_path = Path("results/glm_free_models_latency.json")
    out_path.parent.mkdir(exist_ok=True, parents=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved results to {out_path}")
    
    # Print markdown table
    print("\n| Model | Status | Total Turns | Tool Calls | Input Tokens | Output Tokens | E2E Latency (s) | Avg LLM Latency (ms) |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for r in results:
        if r["status"] == "ERROR":
            print(f"| {r['model']} | `{r['status']}` | - | - | - | - | - | - |")
        else:
            print(f"| {r['model']} | `{r['status']}` | {r['total_turns']} | {r['tool_calls']} | {r['input_tokens']} | {r['output_tokens']} | {r['e2e_latency_s']:.2f}s | {r['avg_llm_latency_ms']:.0f}ms |")


if __name__ == "__main__":
    main()
