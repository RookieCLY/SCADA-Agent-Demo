import sys
import argparse
from pathlib import Path

from agent.orchestrator import assemble
from eval.schema import load_golden_dataset
from world import MockWorld
from agent.tracer import Tracer

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/F_full_four_in_one.yaml")
    parser.add_argument("--dataset", default="eval/golden_dataset.jsonl")
    parser.add_argument("--provider", default="xiaomi-mimo")
    parser.add_argument("--model", default="mimo-v2.5-pro")
    args = parser.parse_args()

    # Monkeypatch Tracer initialization to always record LLM IO for our debugging
    original_init = Tracer.__init__
    def patched_init(self, *a, **kw):
        kw["record_llm_io"] = True
        original_init(self, *a, **kw)
    Tracer.__init__ = patched_init

    agent = assemble(args.config, model_override=args.model, provider_override=args.provider)
    records = load_golden_dataset(args.dataset)
    
    print(f"Starting test_until_fail with config={args.config}, provider={args.provider}, model={args.model}")
    
    for record in records:
        print(f"Running [{record.id}] ...")
        world = MockWorld()
        if record.initial_world:
            try:
                world = MockWorld.model_validate(record.initial_world)
            except Exception:
                pass
                
        result = agent.run(
            record.query,
            golden_id=record.id,
            initial_world=world,
            complexity=record.complexity,
            domain=record.domain
        )
        
        exe = result["execution"]
        if exe["terminal_state"] != "DONE" or exe["early_terminated"]:
            print(f"!!! FAILURE DETECTED ON {record.id} !!!")
            print(f"Terminal state: {exe['terminal_state']}, Early terminated: {exe['early_terminated']}, Reason: {exe['termination_reason']}")
            print(f"Trace saved to: {agent.tracer.traces_path}")
            
            # Print last LLM call's text and reasoning to debug why it failed
            llm_calls = result.get("llm_calls", [])
            if llm_calls:
                last_call = llm_calls[-1]
                print("\n--- LAST LLM TEXT ---")
                print(last_call.get("text", ""))
                print("\n--- LAST LLM REASONING ---")
                print(last_call.get("reasoning", ""))
            
            # Save the failing trace path to a file so the agent can read it
            with open("failed_trace.txt", "w", encoding="utf-8") as f:
                f.write(str(agent.tracer.traces_path))
            return 1
            
    print("All records succeeded, no failure detected.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
