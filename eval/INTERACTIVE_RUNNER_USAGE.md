# Interactive Runner Usage

The interactive runner is a developer/evaluator REPL for manually exercising the SCADA agent against golden cases or ad-hoc worlds.

## Start the runner

```powershell
.\.venv\Scripts\python.exe -m eval.interactive_runner --provider mock --model mock
```

Optional startup flags:

```powershell
.\.venv\Scripts\python.exe -m eval.interactive_runner --config configs\D_minimal.yaml --dataset eval\golden_dataset.jsonl --provider mock --model mock
```

Useful commands:

```text
golden golden-001
world demo
world reset
world add point TEMP_201 analog °C 0 200
world add page p1 Main 1920 1080 #000000
world add widget p1 w1 thermometer 10 20 80 200
world save-json tmp/world.json
world load-json tmp/world.json
inspect world
inspect points
inspect path pages.p1.widgets.w1
display llm-output on
display reasoning off
display world on
display trace on
llm mock mock
config configs/F_full_four_in_one.yaml
query 给TEMP_101加个高温报警,超过80度告警
trace
save eval/golden_cases/adhoc_interactive.json
exit
```

## Manual smoke sequence

Run this sequence to verify the interactive runner manually:

```text
1. Start runner with mock model.
2. Load demo world: world demo
3. Inspect world: inspect world
4. Run a point/alarm query: query 给TEMP_101加个高温报警,超过80度告警
5. Observe real-time state, tool, and world diff output.
6. Toggle LLM output off: display llm-output off
7. Run another query: query 新建一个温度点位 TEMP_201 量程 0~200
8. Load golden-001: golden golden-001
9. Run golden query or another mock-supported query.
10. Save ad-hoc case JSON: save eval/golden_cases/adhoc_interactive.json
11. Exit cleanly: exit
```

The smoke sequence passes when every step completes without exceptions and produces trace output under the configured interactive results directory.

## Non-interactive command mode

For tests or quick checks, pass one or more `--command` flags:

```powershell
.\.venv\Scripts\python.exe -m eval.interactive_runner --provider mock --model mock --command "world demo" --command "query 给TEMP_101加个高温报警,超过80度告警" --command "trace"
```
