#!/usr/bin/env python3
"""
Generate Phase 5 notebooks that read results/aggregated.parquet with Polars
and visualize only observed data.
"""

import json
from pathlib import Path


NOTEBOOKS_DIR = Path("notebooks")
NOTEBOOKS_DIR.mkdir(exist_ok=True)


def create_notebook(cells: list[dict], filename: str) -> None:
    nb = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 2,
    }
    with (NOTEBOOKS_DIR / filename).open("w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1, ensure_ascii=False)
    print(f"Generated {filename}")


def md(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}


def code(source: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source.splitlines(keepends=True)}


def main() -> None:
    common_setup = r'''import os
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import polars as pl
import scipy.stats as stats

assets_dir = Path("../paper_assets")
assets_dir.mkdir(exist_ok=True)

df = pd.DataFrame(pl.read_parquet("../results/aggregated.parquet").to_dicts())
df = df[df["model"] == "deepseek-v4-flash"]
for column in ["tool_count", "top_k", "experiment", "run_id"]:
    if column not in df.columns:
        df[column] = pd.NA

def map_config_features(config_name):
    name = (config_name or "").lower()
    hierarchical = "a_flat" not in name
    workflow = any(token in name for token in ["d_hier", "d_min", "d_wf", "e_with", "f_full", "four_in_one"])
    return hierarchical, workflow

features = df["config_name"].apply(map_config_features)
df["hierarchical"] = [item[0] for item in features]
df["workflow"] = [item[1] for item in features]

def diagnostic_plot(filename, title, message):
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.axis("off")
    ax.text(0.5, 0.62, title, ha="center", va="center", fontsize=14, weight="bold")
    ax.text(0.5, 0.42, message, ha="center", va="center", wrap=True)
    fig.savefig(assets_dir / filename)
    plt.show()

print(f"Loaded {len(df)} traces from aggregated.parquet")
df.head()'''

    nb1_cells = [
        md("# 01 Main Results Analysis\n\nObserved-data Phase 5 analysis for H1-H5 and failure breakdown."),
        code(common_setup),
        md("## H1: Hierarchical vs Flat Architecture"),
        code(r'''df_main = df[df["experiment"] == "phase4_batch"]
flat_grp = df_main[df_main["config_name"] == "A_flat_baseline"]
hier_grp = df_main[df_main["config_name"] == "B_hierarchical_only"]

if len(flat_grp) > 0 and len(hier_grp) > 0:
    t_stat, p_val = stats.ttest_ind(hier_grp["tool_selection_f1"], flat_grp["tool_selection_f1"], alternative="greater", nan_policy="omit")
    print(f"Flat F1: {flat_grp['tool_selection_f1'].mean():.4f}")
    print(f"Hierarchical F1: {hier_grp['tool_selection_f1'].mean():.4f}")
    print(f"T-test (Hier > Flat): t={t_stat:.4f}, p={p_val:.4f}")
else:
    print("Missing Config A or Config B.")

if df["tool_count"].notna().any():
    h1 = (
        df[df["config_name"].isin(["A_flat_baseline", "D_hier_rag_workflow"])]
        .dropna(subset=["tool_count"])
        .groupby(["config_name", "tool_count"])["tool_selection_f1"]
        .agg(["mean", "std"])
        .reset_index()
        .sort_values("tool_count")
    )
    plt.figure(figsize=(7, 5))
    for name, group in h1.groupby("config_name"):
        plt.errorbar(group["tool_count"], group["mean"], yerr=group["std"].fillna(0), marker="o", label=name)
    plt.title("Tool Selection F1 vs. Total Tool Count")
    plt.xlabel("Total Tool Count")
    plt.ylabel("Tool Selection F1")
    plt.ylim(0, 1)
    plt.grid(True, linestyle=":", alpha=0.6)
    plt.legend()
    plt.savefig(assets_dir / "h1_tool_count_vs_f1.png")
    plt.show()
else:
    diagnostic_plot("h1_tool_count_vs_f1.png", "H1 sweep data missing", "No tool_count column was found in aggregated.parquet.")'''),
        md("## H2: Tool RAG"),
        code(r'''df_main = df[df["experiment"] == "phase4_batch"]
b = df_main[df_main["config_name"] == "B_hierarchical_only"]
c = df_main[df_main["config_name"].isin(["C_hier_rag", "C_hier_rag_workflow"])]

if len(b) > 0 and len(c) > 0:
    strict_b = b["strict_success"].mean()
    func_b = b["functional_success"].mean()
    weighted_b = b["weighted_success"].mean()
    latency_b = b["e2e_latency_ms"].mean() / 1000

    strict_c = c["strict_success"].mean()
    func_c = c["functional_success"].mean()
    weighted_c = c["weighted_success"].mean()
    latency_c = c["e2e_latency_ms"].mean() / 1000

    print(f"B strict={strict_b:.2%}, functional={func_b:.2%}, weighted={weighted_b:.2%}, latency={latency_b:.2f}s")
    print(f"C strict={strict_c:.2%}, functional={func_c:.2%}, weighted={weighted_c:.2%}, latency={latency_c:.2f}s")

    fig, ax1 = plt.subplots(figsize=(7, 5))
    labels = ["B\nNo RAG", "C\nRAG"]
    x = np.arange(len(labels))
    width = 0.2
    
    ax1.bar(x - width - 0.02, [strict_b, strict_c], width, label="Strict Success", color="#1f77b4", alpha=0.85)
    ax1.bar(x - 0.01, [func_b, func_c], width, label="Functional Success", color="#2ca02c", alpha=0.85)
    ax1.bar(x + width, [weighted_b, weighted_c], width, label="Weighted Success", color="#9467bd", alpha=0.85)
    
    ax1.set_ylabel("Success Rate", color="black")
    ax1.set_ylim(0, 1.0)
    ax1.legend(loc="upper left")
    
    ax2 = ax1.twinx()
    ax2.bar(x + 2*width + 0.02, [latency_b, latency_c], width, label="Latency", color="#d62728", alpha=0.85)
    ax2.set_ylabel("Latency (seconds)", color="#d62728")
    
    plt.xticks(x, labels)
    plt.title("Tool RAG: Success Rates and Latency")
    fig.tight_layout()
    plt.savefig(assets_dir / "h2_success_vs_latency.png")
    plt.show()
else:
    diagnostic_plot("h2_success_vs_latency.png", "H2 data incomplete", "Both B_hierarchical_only and C_hier_rag are required.")'''),
        md("## H3-H5 and Failure Breakdown"),
        code(r'''df_main = df[df["experiment"] == "phase4_batch"]
d = df_main[df_main["config_name"].isin(["D_hier_rag_workflow", "D_minimal"])]
e = df_main[df_main["config_name"] == "E_with_state_machine"]
f = df_main[df_main["config_name"] == "F_full_four_in_one"]

if len(d) > 0 and len(e) > 0:
    oos_d, oos_e = d["out_of_scope"].astype(float), e["out_of_scope"].astype(float)
    u, p = stats.mannwhitneyu(oos_d, oos_e, alternative="greater")
    print(f"H3 U={u:.4f}, p={p:.4f}")
    plt.figure(figsize=(6, 5))
    rates = [oos_d.mean() * 100, oos_e.mean() * 100]
    plt.bar(["D\nNo SM", "E\nSM"], rates, color=["#d95f02", "#2b5c8f"], width=0.5)
    plt.title("Out-of-Scope Tool Invocation Rates")
    plt.ylabel("Percentage of Traces with OOS (%)")
    plt.ylim(0, max(rates) * 1.3 if max(rates) > 0 else 10)
    plt.grid(True, axis="y", linestyle=":", alpha=0.6)
    for i, val in enumerate(rates):
        plt.text(i, val + 0.3, f"{val:.2f}%", ha='center', va='bottom', fontweight='bold')
    plt.savefig(assets_dir / "h3_out_of_scope_rate.png")
    plt.show()
else:
    diagnostic_plot("h3_out_of_scope_rate.png", "H3 data incomplete", "Both Config D and Config E are required.")

no_wf = c if len(c) > 0 else b
if len(no_wf) > 0 and len(d) > 0:
    plt.figure(figsize=(6, 5))
    plt.boxplot([no_wf["step_count"].values, d["step_count"].values], tick_labels=["No Workflow", "Workflow"])
    plt.title("Step Count Variation With and Without Workflows")
    plt.ylabel("Tool Calls")
    plt.grid(True, linestyle=":", alpha=0.6)
    plt.savefig(assets_dir / "h4_step_count_boxplot.png")
    plt.show()
else:
    diagnostic_plot("h4_step_count_boxplot.png", "H4 data incomplete", "No-workflow and workflow groups are required.")

if len(d) > 0 and len(f) > 0:
    tools_d, tools_f = d["visible_count_mean"].mean(), f["visible_count_mean"].mean()
    strict_d, strict_f = d["strict_success"].mean(), f["strict_success"].mean()
    func_d, func_f = d["functional_success"].mean(), f["functional_success"].mean()
    weighted_d, weighted_f = d["weighted_success"].mean(), f["weighted_success"].mean()
    plt.figure(figsize=(7, 5))
    plt.scatter([tools_d], [strict_d * 100], s=150, marker="o", label="D (Strict Success)")
    plt.scatter([tools_f], [strict_f * 100], s=150, marker="o", label="F (Strict Success)")
    plt.scatter([tools_d], [func_d * 100], s=150, marker="^", label="D (Functional Success)")
    plt.scatter([tools_f], [func_f * 100], s=150, marker="^", label="F (Functional Success)")
    plt.scatter([tools_d], [weighted_d * 100], s=150, marker="s", label="D (Weighted Success)")
    plt.scatter([tools_f], [weighted_f * 100], s=150, marker="s", label="F (Weighted Success)")
    plt.title("Resources Separation: Visible Tools and Success")
    plt.xlabel("Average Visible Tools")
    plt.ylabel("Success Rate (%)")
    plt.ylim(0, 100)
    plt.grid(True, linestyle=":", alpha=0.6)
    plt.legend()
    plt.savefig(assets_dir / "h5_tool_reduction_vs_success.png")
    plt.show()
else:
    diagnostic_plot("h5_tool_reduction_vs_success.png", "H5 data incomplete", "Both Config D and Config F are required.")

failed = df_main[df_main["task_success"] == False]
counts = {
    "hallucinate": int(failed["hallucinated"].sum()),
    "out-of-scope": int(failed["out_of_scope"].sum()),
    "param error": int((~failed["param_valid"]).sum()),
    "timeout": int(failed["loop_stuck"].sum()),
}
counts["other"] = max(0, len(failed) - sum(counts.values()))
if sum(counts.values()) > 0:
    plt.figure(figsize=(6, 6))
    plt.pie(list(counts.values()), labels=list(counts.keys()), autopct="%1.1f%%", startangle=140)
    plt.title("Failure Cause Breakdown")
    plt.savefig(assets_dir / "failure_categories_pie_chart.png")
    plt.show()
else:
    diagnostic_plot("failure_categories_pie_chart.png", "No failures observed", "No failed traces were present.")'''),
    ]
    create_notebook(nb1_cells, "01_main_results.ipynb")

    nb2_cells = [
        md("# 02 Ablation & Interaction Effects\n\nObserved two-factor interaction analysis."),
        code(common_setup),
        md("## Interaction Heatmap"),
        code(r'''df_main = df[df["experiment"] == "phase4_batch"]
pivot_strict = df_main.groupby(["hierarchical", "workflow"])["strict_success"].mean().unstack().reindex(index=[False, True], columns=[False, True])
pivot_func = df_main.groupby(["hierarchical", "workflow"])["functional_success"].mean().unstack().reindex(index=[False, True], columns=[False, True])
pivot_weighted = df_main.groupby(["hierarchical", "workflow"])["weighted_success"].mean().unstack().reindex(index=[False, True], columns=[False, True])
values = np.ma.masked_invalid(pivot_func.to_numpy(dtype=float))
cmap = plt.cm.Blues.copy()
cmap.set_bad("#eeeeee")

fig, ax = plt.subplots(figsize=(6, 5))
im = ax.imshow(values, cmap=cmap, vmin=0, vmax=1)
fig.colorbar(im, ax=ax, label="Functional Success Rate")
ax.set_xticks(np.arange(2))
ax.set_yticks(np.arange(2))
ax.set_xticklabels(["False", "True"])
ax.set_yticklabels(["False", "True"])
for i in range(2):
    for j in range(2):
        val_strict = pivot_strict.iloc[i, j]
        val_func = pivot_func.iloc[i, j]
        val_weighted = pivot_weighted.iloc[i, j]
        if pd.isna(val_strict) or pd.isna(val_func) or pd.isna(val_weighted):
            text = "N/A"
        else:
            text = f"Strict: {val_strict:.3f}\nFunc: {val_func:.3f}\nWeighted: {val_weighted:.3f}"
        ax.text(j, i, text, ha="center", va="center", color="white" if pd.notna(val_func) and val_func > 0.5 else "black")
ax.set_xlabel("Workflow Enabled")
ax.set_ylabel("Hierarchical Architecture")
ax.set_title("Interaction Effect of Architecture and Workflow")
plt.savefig(assets_dir / "h6_interaction_heatmap.png")
plt.show()

complete_cells = pivot_func.notna().sum().sum()
print(f"Observed interaction cells: {complete_cells}/4")
if complete_cells < 4:
    print("ANOVA is not recommended until all four cells have observed data.")'''),
    ]
    create_notebook(nb2_cells, "02_ablation.ipynb")


if __name__ == "__main__":
    main()
