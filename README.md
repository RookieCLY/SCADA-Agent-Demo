# SCADA Agent Demo

Reference implementation backing the paper *"Caging the LLM — Constraint architecture and functional-safety boundary of an industrial SCADA agent"*.

This repository contains a **pure-Python**, single-machine demo that lets each of the four architecture layers (hierarchical tools, Tool RAG, workflow engine, state machine) be toggled independently for ablation studies.

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 0 — Environment | scaffolding, pyproject, .env | done |
| 1 — Core skeleton | Mock World, Mock Tools, registry, dispatcher, state machine, mock orchestrator, JSONL tracer | done |
| 2 — Four-in-one | Tool RAG, Workflow engine, Resources, real LLM client | pending |
| 3 — Evaluation | Golden dataset, judge, metrics | pending |
| 4 — Experiments | run matrix, traces | pending |
| 5 — Analysis | aggregation, report | pending |

## Phase 1 — quick start (no API keys required)

```bash
# 1. install deps (only Pydantic / PyYAML / loguru needed for Phase 1)
python -m venv venv && source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -e .[dev]

# 2. run unit tests
pytest -q

# 3. end-to-end smoke (mock LLM, no API key)
python -m agent.orchestrator --config configs/D_minimal.yaml --query "给反应釜1加个高温报警,超过80度告警"
# → produces results/D_minimal/mock/<run_id>/traces.jsonl
```

The `mock` LLM provider is a deterministic, scripted backend that maps a small set of pre-known queries to a fixed tool-call sequence. It exists so the orchestrator can be exercised end-to-end without any LLM provider credentials. Real LLM providers (Anthropic, OpenAI, DeepSeek) are wired in Phase 2.

## Layout

See §1.8 of `SCADA-Agent-Demo-开发计划.md` for the full directory plan.

```
agent/        # orchestrator, registry, dispatcher, state machine, llm, tracer
tools/        # Mock tools (Domain + Atomic) all writing through the Mock World
world/        # Mock World — pydantic models + in-memory backend
resources/    # Read-only views over the world (Phase 2)
workflows/    # YAML workflow definitions (Phase 2)
configs/      # Experiment YAMLs (A/B/C/D/E/F + sweeps)
eval/         # Golden dataset, judges, metrics, runner (Phase 3+)
tests/        # Unit + E2E tests
```

## License

Internal experiment artefact; license TBD.
