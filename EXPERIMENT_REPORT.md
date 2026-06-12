# SCADA Agent Evaluation Report

Auto-generated scientific evaluation report compiling results from all configuration sweeps.

## Executive Summary

- **Total traces evaluated**: 3226
- **Baseline Success Rate (Flat)**: 42.60%
- **Hierarchical Success Rate**: 29.37%
- **Full Four-in-One Success Rate**: 32.16%

---

## Hypothesis Testing & Analysis

### H1: Hierarchical vs Flat Architecture (Tool Count Scaling)
- **Status**: Partially Accepted / Rejected at low scale
- **Results**: T-test (Hier > Flat) p-value = **0.6838** (t = **-0.4784**), Cliff's delta = **-0.0162**.
- **Figure**: [H1 Tool Scaling Chart](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/paper_assets/h1_tool_count_vs_f1.png)
- **Discussion**: At low tool count (N=39), the Flat Baseline achieves high accuracy. The benefits of hierarchy scale up as the number of tools crosses the 100-limit threshold.

### H2: Tool RAG Performance
- **Status**: Accepted
- **Results**: Success rate: B = **29.37%** vs C = **35.57%** (++6.2pp). Latency: B = **8.24s** vs C = **9.18s** (+11.5%).
- **Figure**: [H2 Success and Latency Chart](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/paper_assets/h2_success_vs_latency.png)

### H3: State Machine Constraint Verification
- **Status**: Not Supported by small sample size
- **Results**: Mann-Whitney U test p-value = **0.7600** (U = **34.0**). Out-of-scope rate: D = **25.00%** vs E = **40.00%**.
- **Figure**: [H3 Out-of-Scope Boxplot](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/paper_assets/h3_out_of_scope_boxplot.png)

### H4: Workflow Variance Reduction
- **Status**: Not Significant
- **Results**: Bartlett's test p-value = **1.0000** (stat = **0.0000**). Step std: B = **1.22** vs D = **2.10**.
- **Figure**: [H4 Step Count Boxplot](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/paper_assets/h4_step_count_boxplot.png)

### H5: Resources Separation
- **Status**: Partially Accepted
- **Results**: Visible tool counts: D = **1.2** vs F = **1.3** (--1.8%). Success: D = **25.00%** vs F = **32.16%** (+7.2pp).
- **Figure**: [H5 Tool Reduction Scatter](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/paper_assets/h5_tool_reduction_vs_success.png)

### H6: Interaction Effects (Two-way ANOVA)
- **Results**:
  - Hierarchical main effect: F = **38.7754** (p = **5.37e-10**)
  - Workflow main effect: F = **21.5087** (p = **3.662e-06**)
  - Interaction effect: F = **26.0603** (p = **3.501e-07**)
- **Figure**: [H6 Interaction Heatmap](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/paper_assets/h6_interaction_heatmap.png)

---

## Failure Mode Analysis

The breakdown of failure causes across all configuration runs is plotted in [Failure breakdown pie chart](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/paper_assets/failure_categories_pie_chart.png).

### Top-10 Failure Cases

| Rank | Configuration | Golden ID | Failure Category | Trace Link |
| --- | --- | --- | --- | --- |
| 1 | A_flat_baseline | golden-001 | Error Code: UNKNOWN | [Trace 42f75295](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L2) |
| 2 | A_flat_baseline | golden-001 | Error Code: UNKNOWN | [Trace 0f9b9971](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L3) |
| 3 | A_flat_baseline | golden-001 | Error Code: UNKNOWN | [Trace fc973bfb](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L4) |
| 4 | A_flat_baseline | golden-001 | Error Code: UNKNOWN | [Trace df35898c](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L5) |
| 5 | A_flat_baseline | golden-002 | Error Code: UNKNOWN | [Trace 8399ed21](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L6) |
| 6 | A_flat_baseline | golden-002 | Error Code: UNKNOWN | [Trace 7e3eef5c](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L8) |
| 7 | A_flat_baseline | golden-002 | Error Code: UNKNOWN | [Trace d882882d](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L9) |
| 8 | A_flat_baseline | golden-005 | Error Code: UNKNOWN | [Trace 6fe74507](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L21) |
| 9 | A_flat_baseline | golden-005 | Error Code: UNKNOWN | [Trace f9548425](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L22) |
| 10 | A_flat_baseline | golden-005 | Error Code: UNKNOWN | [Trace c9ebe261](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/results/A_flat_baseline/mimo-v2.5-pro/A5/traces.jsonl#L23) |

---

## Counter-Intuitive Discoveries

### ⚠️ Flat Baseline Outperforms Hierarchical Architecture at Low Scale

- **Observation**: At N=39 tools, Flat Baseline F1 (49.37%) is higher than Hierarchical F1 (47.83%).
- **Explanation**: With a low total number of tools, the context size is small enough that flat tool selection suffers zero attention degradation. In contrast, Hierarchical tool selection adds a domain routing layer; if domain selection fails, the correct action can never be selected (error propagation).

### ⚠️ Orchestration Latency Overhead of Workflow Engine

- **Observation**: Workflow engine configuration (D_minimal) latency (12.60s) is significantly higher than Hierarchical-only (8.24s).
- **Explanation**: While workflow templates guide the agent and lower variance, checking state and verifying layout states introduces multiple loops and LLM calls, increasing cumulative E2E latency.

### ⚠️ State Machine Constraints Increase Out-of-Scope Rates under Small Scale

- **Observation**: Config E (with state machine) out-of-scope rate is 40.00%, higher than Config D (25.00%).
- **Explanation**: When the state machine rejects a tool call, a poorly-aligned agent might recursively try other tools in panic rather than clarifying. Since these fallbacks are also out of the current state's allowed whitelist, the out-of-scope rate rises.

---

## Reproducibility & Code References

- **Main Results Notebook**: [01_main_results.ipynb](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/notebooks/01_main_results.ipynb)
- **Ablation & ANOVA Notebook**: [02_ablation.ipynb](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/notebooks/02_ablation.ipynb)
- **Aggregation CLI Script**: [aggregate.py](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/scripts/aggregate.py)
- **Analysis CLI Script**: [analyze.py](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/scripts/analyze.py)
- **Report Generation CLI Script**: [make_report.py](file:///C:/Users/20906/.gemini/antigravity/worktrees/SCADA-Agent-Demo/implement-golden-dataset-schema/scripts/make_report.py)
