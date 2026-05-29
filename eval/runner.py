import argparse
import sys
from pathlib import Path

from agent.orchestrator import assemble
from eval.schema import load_golden_dataset
from world import MockWorld

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run golden dataset tests through the agent")
    parser.add_argument("--config", required=True, help="Path to experiment YAML config (e.g., configs/F_full_four_in_one.yaml)")
    parser.add_argument("--dataset", default="eval/golden_dataset.jsonl", help="Path to the golden dataset JSONL")
    parser.add_argument("--golden-ids", help="Comma-separated list of golden IDs to run (e.g., golden-042,golden-001)")
    parser.add_argument("--all", action="store_true", help="Run all records in the dataset")
    parser.add_argument("--reps", type=int, default=1, help="Number of repetitions per test")
    parser.add_argument("--model", help="Override the LLM model name (e.g., gpt-4o)")
    parser.add_argument("--provider", help="Override the LLM provider (e.g., openai, mock, anthropic)")
    
    args = parser.parse_args(argv)
    
    if not args.all and not args.golden_ids:
        print("Error: Must specify either --golden-ids or --all", file=sys.stderr)
        return 1
        
    records = load_golden_dataset(args.dataset)
    
    if not args.all:
        target_ids = set(args.golden_ids.split(","))
        records = [r for r in records if r.id in target_ids]
        if not records:
            print(f"Error: No records found matching the provided IDs: {target_ids}", file=sys.stderr)
            return 1
            
    print(f"Loaded {len(records)} golden records to evaluate.")
    
    # Initialize the agent once per run session
    agent = assemble(args.config, model_override=args.model, provider_override=args.provider)
    print(f"Agent assembled with config {args.config} (Provider: {agent.config.model.provider}, Model: {agent.config.model.name})")
    
    success_count = 0
    total_runs = len(records) * args.reps
    
    for record in records:
        print(f"\n[{record.id}] Query: {record.query}")
        
        for rep in range(args.reps):
            rep_label = f" (Rep {rep+1}/{args.reps})" if args.reps > 1 else ""
            print(f"  -> Running execution{rep_label}...")
            
            # Reconstruct initial world for each run
            world = MockWorld()
            if record.initial_world:
                try:
                    world = MockWorld.model_validate(record.initial_world)
                except Exception as e:
                    print(f"     Warning: Could not strictly validate initial_world: {e}")
                    # Fallback or empty depending on strictness
                    
            try:
                result = agent.run(
                    record.query,
                    golden_id=record.id,
                    initial_world=world,
                    rep_index=rep,
                    complexity=record.complexity,
                    domain=record.domain
                )
                term_state = result["execution"]["terminal_state"]
                print(f"     Finished in {result['execution']['total_turns']} turns. Terminal state: {term_state}")
                print(f"     Trace log saved to: {agent.tracer.traces_path}")
                success_count += 1
            except Exception as e:
                print(f"     [!] Execution failed with exception: {e}")
                
    print(f"\n=== Runner Complete ===")
    print(f"Successfully ran {success_count}/{total_runs} evaluations.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
