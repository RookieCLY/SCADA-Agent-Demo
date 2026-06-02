import json
import os
from pathlib import Path

def main():
    input_file = Path("eval/golden_dataset.jsonl")
    output_dir = Path("eval/golden_cases")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    count = 0
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            case_id = data.get("id", f"case_{count}")
            output_path = output_dir / f"{case_id}.json"
            
            with open(output_path, "w", encoding="utf-8") as out:
                json.dump(data, out, indent=2, ensure_ascii=False)
            count += 1
            
    print(f"Successfully created {count} JSON files in {output_dir}")

if __name__ == "__main__":
    main()
