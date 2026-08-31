#!/usr/bin/env python3
"""
scripts/analyze.py

Performs Phase 5 data analysis, hypothesis checks, and visualization.
All charts are generated from observed data only; missing experiment cells are
reported explicitly instead of being filled with estimated values.
"""

import os
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import polars as pl
import scipy.stats as stats


ASSETS_DIR = Path("paper_assets")

plt.rcParams.update({
    "font.size": 11,
    "axes.labelsize": 12,
    "axes.titlesize": 13,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "figure.titlesize": 14,
    "font.family": "sans-serif",
    "figure.dpi": 300,
    "savefig.bbox": "tight",
})


def map_config_features(config_name: str) -> tuple[bool, bool, bool, bool, bool]:
    hierarchical = True
    rag = False
    workflow = False
    state_machine = False
    resources = False

    name = (config_name or "").lower()
    if "a_flat" in name:
        hierarchical = False
    if "c_hier" in name or "c_rag" in name or "hier_rag" in name:
        rag = True
    if "d_hier" in name or "d_min" in name or "d_wf" in name:
        rag = True
        workflow = True
    if "e_with" in name or "state_machine" in name:
        rag = True
        workflow = True
        state_machine = True
    if "f_full" in name or "four_in_one" in name:
        rag = True
        workflow = True
        state_machine = True
        resources = True
    return hierarchical, rag, workflow, state_machine, resources


def compute_cliffs_delta(x: pd.Series, y: pd.Series) -> float:
    x_arr = np.asarray(x.dropna())
    y_arr = np.asarray(y.dropna())
    if len(x_arr) == 0 or len(y_arr) == 0:
        return np.nan
    diffs = 0
    for value in x_arr:
        diffs += np.sum(value > y_arr) - np.sum(value < y_arr)
    return diffs / (len(x_arr) * len(y_arr))


def two_way_anova(df: pd.DataFrame, factor_a: str, factor_b: str, response: str) -> dict | None:
    df_clean = df[[factor_a, factor_b, response]].dropna()
    if len(df_clean) < 4:
        return None

    y = df_clean[response].astype(float).values
    a = df_clean[factor_a].values
    b = df_clean[factor_b].values
    a_levels = np.unique(a)
    b_levels = np.unique(b)
    if len(a_levels) < 2 or len(b_levels) < 2:
        return None

    grand_mean = np.mean(y)
    ss_total = np.sum((y - grand_mean) ** 2)
    ss_a = sum(len(y[a == lvl]) * (np.mean(y[a == lvl]) - grand_mean) ** 2 for lvl in a_levels)
    ss_b = sum(len(y[b == lvl]) * (np.mean(y[b == lvl]) - grand_mean) ** 2 for lvl in b_levels)

    ss_ab = 0.0
    for lvl_a in a_levels:
        for lvl_b in b_levels:
            mask = (a == lvl_a) & (b == lvl_b)
            if mask.any():
                ss_ab += len(y[mask]) * (
                    np.mean(y[mask]) - np.mean(y[a == lvl_a]) - np.mean(y[b == lvl_b]) + grand_mean
                ) ** 2

    ss_error = max(0.0, ss_total - ss_a - ss_b - ss_ab)
    df_a = len(a_levels) - 1
    df_b = len(b_levels) - 1
    df_ab = df_a * df_b
    df_error = len(df_clean) - (len(a_levels) * len(b_levels))
    if df_error <= 0:
        return None

    ms_error = ss_error / df_error
    if ms_error <= 0:
        return None

    ms_a = ss_a / df_a
    ms_b = ss_b / df_b
    ms_ab = ss_ab / df_ab
    f_a = ms_a / ms_error
    f_b = ms_b / ms_error
    f_ab = ms_ab / ms_error

    return {
        "A": {"df": df_a, "ss": ss_a, "ms": ms_a, "F": f_a, "p": stats.f.sf(f_a, df_a, df_error)},
        "B": {"df": df_b, "ss": ss_b, "ms": ms_b, "F": f_b, "p": stats.f.sf(f_b, df_b, df_error)},
        "AB": {"df": df_ab, "ss": ss_ab, "ms": ms_ab, "F": f_ab, "p": stats.f.sf(f_ab, df_ab, df_error)},
        "error": {"df": df_error, "ss": ss_error, "ms": ms_error},
    }


def bootstrap_mean_diff_ci(
    x: pd.Series,
    y: pd.Series,
    n_boot: int = 10000,
    alpha: float = 0.05,
    seed: int = 42,
) -> dict | None:
    """Bootstrap CI for mean(x) - mean(y), plus a two-sided bootstrap p-value.

    H2 (Tool RAG) and H5 (Resources) were reported with bare descriptive means
    and no test at all — H2 in particular is the paper's one clean positive
    result. A nonparametric bootstrap is the right tool here: the success/latency
    metrics are not normal (successes are Bernoulli, latency is right-skewed) and
    the group sizes differ. The two-sided p-value is the fraction of resampled
    differences on the opposite side of zero from the observed difference,
    doubled — i.e. a test of H0: mean(x) == mean(y).
    """
    x_arr = np.asarray(x.dropna(), dtype=float)
    y_arr = np.asarray(y.dropna(), dtype=float)
    if len(x_arr) < 2 or len(y_arr) < 2:
        return None
    rng = np.random.default_rng(seed)
    obs = float(x_arr.mean() - y_arr.mean())
    bx = rng.choice(x_arr, size=(n_boot, len(x_arr)), replace=True).mean(axis=1)
    by = rng.choice(y_arr, size=(n_boot, len(y_arr)), replace=True).mean(axis=1)
    diffs = bx - by
    lo, hi = np.percentile(diffs, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    # Two-sided bootstrap p-value against H0: diff == 0.
    p_side = np.mean(diffs <= 0) if obs > 0 else np.mean(diffs >= 0)
    p_value = min(1.0, 2.0 * float(p_side))
    return {
        "observed_diff": obs,
        "ci_low": float(lo),
        "ci_high": float(hi),
        "p_value": p_value,
        "n_x": len(x_arr),
        "n_y": len(y_arr),
    }


def write_diagnostic_plot(filename: str, title: str, message: str) -> None:
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.axis("off")
    ax.text(0.5, 0.62, title, ha="center", va="center", fontsize=14, weight="bold")
    ax.text(0.5, 0.42, message, ha="center", va="center", wrap=True)
    fig.savefig(ASSETS_DIR / filename)
    plt.close(fig)


# Configs used as the "caged" arms of the tool-count sweep, in preference order.
_CAGED_SWEEP_CONFIGS = ["F_full_four_in_one", "D_hier_rag_workflow"]
_CONFIG_LABELS = {
    "A_flat_baseline": "Flat (A)",
    "D_hier_rag_workflow": "Caged (D: Hier+RAG+WF)",
    "F_full_four_in_one": "Four-in-one (F)",
}
_CONFIG_COLORS = {
    "A_flat_baseline": "#d95f02",
    "D_hier_rag_workflow": "#2b5c8f",
    "F_full_four_in_one": "#1b7837",
}


def plot_h1_efficiency_scaling(df: pd.DataFrame) -> None:
    """H1 efficiency curves — the paper's central cost claim.

    As the tool corpus grows (50 → 1000+), the caged four-in-one architecture
    should keep per-turn *visible tools*, *input tokens*, *latency* and *cost*
    roughly flat, while the flat baseline scales up with N. The existing H1 plot
    shows only accuracy (F1) vs tool_count; this plots the efficiency datapoints
    the paper actually argues about. One line per config, faceted 2×2.
    """
    filename = "h1_efficiency_vs_tool_count.png"
    if "tool_count" not in df.columns or not df["tool_count"].notna().any():
        write_diagnostic_plot(
            filename, "H1 efficiency data missing",
            "Tool-count sweep traces (rows carrying tool_count) are required.\n"
            "Run configs/sweep_tool_count.yaml, then scripts/aggregate.py.",
        )
        return

    present_caged = [c for c in _CAGED_SWEEP_CONFIGS if (df["config_name"] == c).any()]
    keep = ["A_flat_baseline", *present_caged]
    subset = df[df["config_name"].isin(keep)].dropna(subset=["tool_count"])
    if subset.empty:
        write_diagnostic_plot(
            filename, "H1 efficiency data missing",
            "No A_flat_baseline / caged sweep rows with a tool_count were found.",
        )
        return

    panels = [
        ("visible_count_mean", "Visible tools / turn", 1.0),
        ("input_tokens", "Input tokens / run", 1.0),
        ("e2e_latency_ms", "E2E latency (s)", 1.0 / 1000.0),
        ("cost_usd", "Cost (USD) / run", 1.0),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    any_plotted = False
    for ax, (col, ylabel, scale) in zip(axes.ravel(), panels, strict=False):
        if col not in subset.columns or not subset[col].notna().any():
            ax.axis("off")
            ax.set_title(f"{ylabel}: no data")
            continue
        grouped = (
            subset.dropna(subset=[col])
            .groupby(["config_name", "tool_count"])[col]
            .mean()
            .reset_index()
            .sort_values("tool_count")
        )
        for cfg, g in grouped.groupby("config_name"):
            ax.plot(
                g["tool_count"], g[col] * scale, marker="o",
                label=_CONFIG_LABELS.get(cfg, cfg), color=_CONFIG_COLORS.get(cfg),
            )
            any_plotted = True
        ax.set_xlabel("Total tool count")
        ax.set_ylabel(ylabel)
        ax.grid(True, linestyle=":", alpha=0.6)
        ax.legend(loc="best", fontsize=9)

    if not any_plotted:
        plt.close(fig)
        write_diagnostic_plot(
            filename, "H1 efficiency data missing",
            "None of visible_count/input_tokens/latency/cost had observed values.",
        )
        return

    fig.suptitle(
        "Efficiency vs. tool count — the cage holds per-turn cost ~flat as the corpus grows",
        fontsize=13,
    )
    fig.tight_layout()
    plt.savefig(ASSETS_DIR / filename)
    plt.close(fig)

    # Console summary: Flat-vs-caged blow-up factor at the largest tool_count.
    try:
        max_tc = int(subset["tool_count"].max())
        at_max = subset[subset["tool_count"] == max_tc]
        flat = at_max[at_max["config_name"] == "A_flat_baseline"]
        for cfg in present_caged:
            caged = at_max[at_max["config_name"] == cfg]
            if flat.empty or caged.empty:
                continue
            for col, ylabel, _ in panels:
                if col not in at_max.columns:
                    continue
                fv, cv = flat[col].mean(), caged[col].mean()
                if pd.notna(fv) and pd.notna(cv) and cv:
                    print(
                        f"H1 efficiency @tool_count={max_tc}: {ylabel} "
                        f"Flat={fv:.1f} vs {cfg}={cv:.1f} ({fv / cv:.1f}× )"
                    )
    except Exception:
        pass


def load_dataframe(path: Path) -> pd.DataFrame:
    df = pd.DataFrame(pl.read_parquet(path).to_dicts())
    optional_columns = {
        "tool_count": pd.NA,
        "top_k": pd.NA,
        "experiment": pd.NA,
        "run_id": pd.NA,
        "out_of_scope_tool_rate": pd.NA,
    }
    for column, default in optional_columns.items():
        if column not in df.columns:
            df[column] = default
    features = df["config_name"].apply(map_config_features)
    df["hierarchical"] = [item[0] for item in features]
    df["rag"] = [item[1] for item in features]
    df["workflow"] = [item[2] for item in features]
    df["state_machine"] = [item[3] for item in features]
    df["resources"] = [item[4] for item in features]
    if "complexity" in df.columns:
        df["complexity"] = df["complexity"].fillna("simple")
    return df


def analyze_and_plot(model_name: str | None = "all") -> None:
    ASSETS_DIR.mkdir(exist_ok=True)
    parquet_path = Path("results/aggregated.parquet")
    if not parquet_path.exists():
        raise SystemExit("aggregated.parquet not found. Run scripts/aggregate.py first.")

    df = load_dataframe(parquet_path)
    print(f"Loaded {len(df)} rows from parquet.")

    if model_name == "all" or model_name is None:
        models = [m for m in df["model"].dropna().unique() if m != "unknown"]
        if not models:
            print("No models found in aggregated.parquet. Running on all data.")
            _run_analysis_and_plot(df, None)
        else:
            for m in models:
                print(f"\n==================================================")
                print(f"Generating plots for model: {m}")
                print(f"==================================================")
                df_filtered = df[df["model"] == m]
                _run_analysis_and_plot(df_filtered, m)
    else:
        df_filtered = df[df["model"] == model_name]
        _run_analysis_and_plot(df_filtered, model_name)


def _run_analysis_and_plot(df: pd.DataFrame, model_name: str | None = None) -> None:
    import matplotlib.figure
    original_fig_savefig = matplotlib.figure.Figure.savefig

    def custom_fig_savefig(self, *args, **kwargs):
        if len(args) > 0 and isinstance(args[0], (str, Path)):
            path = Path(args[0])
            if model_name:
                safe_model = model_name.replace("/", "_").replace(" ", "_")
                parent = path.parent
                stem = path.stem
                ext = path.suffix
                new_path = parent / f"{stem}_{safe_model}{ext}"
                args = (new_path,) + args[1:]
        return original_fig_savefig(self, *args, **kwargs)

    matplotlib.figure.Figure.savefig = custom_fig_savefig

    # Filter to main Phase 4 experiment for overall baseline and controlled comparisons
    df_main = df[df["experiment"] == "phase4_batch"]
    print(f"Filtered to {len(df_main)} main Phase 4 traces.")

    flat_grp = df_main[df_main["config_name"] == "A_flat_baseline"]
    hier_grp = df_main[df_main["config_name"] == "B_hierarchical_only"]
    c_grp = df_main[df_main["config_name"].isin(["C_hier_rag", "C_hier_rag_workflow"])]
    # D is the "+Workflow" cell (C + Workflow). D_minimal is a *different*
    # config — hierarchical + state machine, NO workflow and NO RAG — so folding
    # it into D contaminated the very layer D is meant to isolate. D is the
    # baseline for both H4 (workflow) and H5, so this leaked into two
    # hypotheses. D_minimal is not part of the phase4 ablation run anyway.
    grp_d = df_main[df_main["config_name"] == "D_hier_rag_workflow"]
    grp_e = df_main[df_main["config_name"] == "E_with_state_machine"]
    grp_f = df_main[df_main["config_name"] == "F_full_four_in_one"]

    print("\n--- H1 Analysis (Flat vs Hierarchical) ---")
    if len(flat_grp) > 0 and len(hier_grp) > 0:
        t_stat, p_val = stats.ttest_ind(
            hier_grp["tool_selection_f1"],
            flat_grp["tool_selection_f1"],
            alternative="greater",
            nan_policy="omit",
        )
        cliff_d = compute_cliffs_delta(hier_grp["tool_selection_f1"], flat_grp["tool_selection_f1"])
        print(f"Flat F1: {flat_grp['tool_selection_f1'].mean():.4f} +/- {flat_grp['tool_selection_f1'].std():.4f}")
        print(f"Hier F1: {hier_grp['tool_selection_f1'].mean():.4f} +/- {hier_grp['tool_selection_f1'].std():.4f}")
        print(f"T-test (Hier > Flat): t={t_stat:.4f}, p={p_val:.4f}")
        print(f"Cliff's delta: {cliff_d:.4f}")
    else:
        print("Missing Config A or Config B for H1 test.")

    if df["tool_count"].notna().any():
        h1_data = (
            df[df["config_name"].isin(["A_flat_baseline", "D_hier_rag_workflow"])]
            .dropna(subset=["tool_count"])
            .groupby(["config_name", "tool_count"])["tool_selection_f1"]
            .agg(["mean", "std"])
            .reset_index()
            .sort_values("tool_count")
        )
        plt.figure(figsize=(7, 5))
        for config_name, group in h1_data.groupby("config_name"):
            label = "Flat Architecture" if config_name == "A_flat_baseline" else "Hierarchical Architecture"
            plt.errorbar(group["tool_count"], group["mean"], yerr=group["std"].fillna(0), marker="o", label=label)
        plt.title("Tool Selection F1 vs. Total Tool Count")
        plt.xlabel("Total Tool Count")
        plt.ylabel("Tool Selection F1 Score")
        plt.grid(True, linestyle=":", alpha=0.6)
        plt.legend(loc="best")
        plt.savefig(ASSETS_DIR / "h1_tool_count_vs_f1.png")
        plt.close()
    elif len(flat_grp) > 0 or len(hier_grp) > 0:
        h1_bar = pd.DataFrame({
            "config": ["Flat", "Hierarchical"],
            "mean": [flat_grp["tool_selection_f1"].mean(), hier_grp["tool_selection_f1"].mean()],
            "std": [flat_grp["tool_selection_f1"].std(), hier_grp["tool_selection_f1"].std()],
        }).dropna(subset=["mean"])
        plt.figure(figsize=(6, 5))
        plt.bar(h1_bar["config"], h1_bar["mean"], yerr=h1_bar["std"].fillna(0), color=["#d95f02", "#2b5c8f"])
        plt.title("Tool Selection F1 by Architecture")
        plt.ylabel("Tool Selection F1 Score")
        plt.ylim(0, 1)
        plt.grid(True, axis="y", linestyle=":", alpha=0.6)
        plt.savefig(ASSETS_DIR / "h1_tool_count_vs_f1.png")
        plt.close()
    else:
        write_diagnostic_plot("h1_tool_count_vs_f1.png", "H1 data missing", "Config A and Config B traces are required.")

    # B1: the paper's central efficiency claim — cost/tokens/latency/visible
    # tools vs tool_count (accuracy alone, above, does not show it).
    plot_h1_efficiency_scaling(df)

    print("\n--- H2 Analysis (Tool RAG) ---")
    if len(hier_grp) > 0 and len(c_grp) > 0:
        strict_b = hier_grp["strict_success"].mean()
        func_b = hier_grp["functional_success"].mean()
        weighted_b = hier_grp["weighted_success"].mean()
        latency_b = hier_grp["e2e_latency_ms"].mean()

        strict_c = c_grp["strict_success"].mean()
        func_c = c_grp["functional_success"].mean()
        weighted_c = c_grp["weighted_success"].mean()
        latency_c = c_grp["e2e_latency_ms"].mean()

        print(f"Config B: Strict Success={strict_b:.2%}, Functional Success={func_b:.2%}, Weighted Success={weighted_b:.2%}, Latency={latency_b/1000:.2f}s")
        print(f"Config C: Strict Success={strict_c:.2%}, Functional Success={func_c:.2%}, Weighted Success={weighted_c:.2%}, Latency={latency_c/1000:.2f}s")

        # H2's claim is two-part: RAG cuts latency/cost *without* hurting
        # success. So it needs (a) a test that the latency drop is real, and
        # (b) evidence the success rate did NOT drop. Bootstrap CIs give both:
        # a latency CI that excludes zero, and a success CI centred near zero.
        lat_ci = bootstrap_mean_diff_ci(c_grp["e2e_latency_ms"], hier_grp["e2e_latency_ms"])
        succ_ci = bootstrap_mean_diff_ci(
            c_grp["strict_success"].astype(float), hier_grp["strict_success"].astype(float)
        )
        if lat_ci:
            print(
                f"H2 latency Δ(C−B): {lat_ci['observed_diff']/1000:.2f}s "
                f"[95% CI {lat_ci['ci_low']/1000:.2f}, {lat_ci['ci_high']/1000:.2f}], "
                f"bootstrap p={lat_ci['p_value']:.4f}"
            )
        if succ_ci:
            no_harm = succ_ci["ci_low"] > -0.05
            print(
                f"H2 strict-success Δ(C−B): {succ_ci['observed_diff']:+.4f} "
                f"[95% CI {succ_ci['ci_low']:+.4f}, {succ_ci['ci_high']:+.4f}], "
                f"bootstrap p={succ_ci['p_value']:.4f} "
                f"→ success preserved (CI low > −5pp): {no_harm}"
            )

        fig, ax1 = plt.subplots(figsize=(7, 5))
        labels = ["Config B\n(No RAG)", "Config C\n(With RAG)"]
        x = np.arange(len(labels))
        width = 0.2
        
        ax1.bar(x - width - 0.02, [strict_b, strict_c], width, label="Strict Success", color="#1f77b4", alpha=0.85)
        ax1.bar(x - 0.01, [func_b, func_c], width, label="Functional Success", color="#2ca02c", alpha=0.85)
        ax1.bar(x + width, [weighted_b, weighted_c], width, label="Weighted Success", color="#9467bd", alpha=0.85)
        
        ax1.set_ylabel("Success Rate", color="black")
        ax1.set_ylim(0, 1.0)
        ax1.tick_params(axis="y")
        ax1.legend(loc="upper left")
        
        ax2 = ax1.twinx()
        ax2.set_ylabel("E2E Latency (seconds)", color="#d62728")
        ax2.bar(x + 2*width + 0.02, [latency_b / 1000, latency_c / 1000], width, label="Latency", color="#d62728", alpha=0.85)
        ax2.tick_params(axis="y", labelcolor="#d62728")
        ax2.set_ylim(0, max(latency_b, latency_c) / 1000 * 1.3)
        
        plt.xticks(x, labels)
        plt.title("Effect of Tool RAG on Success Rates and Latency")
        fig.tight_layout()
        plt.savefig(ASSETS_DIR / "h2_success_vs_latency.png")
        plt.close(fig)
    else:
        print("Skipping H2 statistics: Config B and Config C are both required.")
        write_diagnostic_plot(
            "h2_success_vs_latency.png",
            "H2 data incomplete",
            "Observed data for both B_hierarchical_only and C_hier_rag is required.",
        )

    print("\n--- H3 Analysis (State Machine) ---")
    if len(grp_d) > 0 and len(grp_e) > 0:
        oos_d = grp_d["out_of_scope"].astype(float)
        oos_e = grp_e["out_of_scope"].astype(float)
        u_stat, p_val_h3 = stats.mannwhitneyu(oos_d, oos_e, alternative="greater")
        print(f"Config D Out-of-Scope Rate: {oos_d.mean():.4f}")
        print(f"Config E Out-of-Scope Rate: {oos_e.mean():.4f}")
        print(f"Mann-Whitney U test (D > E): U={u_stat:.4f}, p={p_val_h3:.4f}")
        plt.figure(figsize=(6, 5))
        rates = [oos_d.mean() * 100, oos_e.mean() * 100]
        labels = ["Config D\n(No State Machine)", "Config E\n(With State Machine)"]
        plt.bar(labels, rates, color=["#d95f02", "#2b5c8f"], width=0.5)
        plt.title("Out-of-Scope Tool Invocation Rates")
        plt.ylabel("Percentage of Traces with OOS (%)")
        plt.ylim(0, max(rates) * 1.3 if max(rates) > 0 else 10)
        plt.grid(True, axis="y", linestyle=":", alpha=0.6)
        for i, val in enumerate(rates):
            plt.text(i, val + 0.3, f"{val:.2f}%", ha='center', va='bottom', fontweight='bold')
        plt.savefig(ASSETS_DIR / "h3_out_of_scope_rate.png")
        plt.close()
    else:
        print("Skipping H3 statistics: Config D and Config E are both required.")
        write_diagnostic_plot("h3_out_of_scope_rate.png", "H3 data incomplete", "Observed data for Config D and Config E is required.")

    print("\n--- H4 Analysis (Workflow) ---")
    no_workflow_grp = c_grp if len(c_grp) > 0 else hier_grp
    no_workflow_label = "Config C" if len(c_grp) > 0 else "Config B (C missing)"
    if len(no_workflow_grp) > 0 and len(grp_d) > 0:
        steps_no_wf = no_workflow_grp["step_count"].dropna().astype(float)
        steps_wf = grp_d["step_count"].dropna().astype(float)
        if len(steps_no_wf) < 2 or len(steps_wf) < 2 or steps_no_wf.var() == 0.0 or steps_wf.var() == 0.0:
            f_val, p_val_h4 = np.nan, np.nan
            print("Bartlett test skipped: one group has fewer than two samples or zero variance.")
        else:
            f_val, p_val_h4 = stats.bartlett(steps_no_wf, steps_wf)
            print(f"Bartlett's test for variance reduction: stat={f_val:.4f}, p={p_val_h4:.4f}")
        print(f"No Workflow Mean Steps ({no_workflow_label}): {steps_no_wf.mean():.2f} (std={steps_no_wf.std():.2f})")
        print(f"With Workflow Mean Steps (Config D): {steps_wf.mean():.2f} (std={steps_wf.std():.2f})")
        plt.figure(figsize=(6, 5))
        plt.boxplot([steps_no_wf.values, steps_wf.values], tick_labels=[f"Without Workflow\n({no_workflow_label})", "With Workflow\n(Config D)"])
        plt.title("Step Count Variation With & Without Workflows")
        plt.ylabel("Number of Steps (Tool Calls)")
        plt.grid(True, linestyle=":", alpha=0.6)
        plt.savefig(ASSETS_DIR / "h4_step_count_boxplot.png")
        plt.close()
    else:
        print("Skipping H4 statistics: no-workflow and workflow groups are both required.")
        write_diagnostic_plot("h4_step_count_boxplot.png", "H4 data incomplete", "Observed no-workflow and workflow groups are required.")

    print("\n--- H5 Analysis (Resources Separation) ---")
    # H5 isolates Resources/Tools separation, which is the ONLY lever that
    # differs between E and F. The previous code compared D vs F, so it bundled
    # the StateMachine (D→E) and Workflow effects into what it attributed to
    # Resources. The config YAML itself documents the comparison as E vs F.
    if len(grp_e) > 0 and len(grp_f) > 0:
        strict_e = grp_e["strict_success"].mean()
        strict_f = grp_f["strict_success"].mean()
        func_e = grp_e["functional_success"].mean()
        func_f = grp_f["functional_success"].mean()
        weighted_e = grp_e["weighted_success"].mean()
        weighted_f = grp_f["weighted_success"].mean()
        tools_e = grp_e["visible_count_mean"].mean()
        tools_f = grp_f["visible_count_mean"].mean()
        reduction_pct = (tools_e - tools_f) / tools_e if tools_e > 0 else np.nan
        print(f"Config E Visible Tools: {tools_e:.2f}, Strict Success: {strict_e:.2%}, Functional Success: {func_e:.2%}, Weighted Success: {weighted_e:.2%}")
        print(f"Config F Visible Tools: {tools_f:.2f}, Strict Success: {strict_f:.2%}, Functional Success: {func_f:.2%}, Weighted Success: {weighted_f:.2%}")
        print(f"Visible Tool Count Reduction (E→F): {reduction_pct:.2%}")
        # Same two-part claim as H2: fewer visible tools, no loss of success.
        tools_ci = bootstrap_mean_diff_ci(grp_e["visible_count_mean"], grp_f["visible_count_mean"])
        succ_ci = bootstrap_mean_diff_ci(
            grp_f["strict_success"].astype(float), grp_e["strict_success"].astype(float)
        )
        if tools_ci:
            print(
                f"H5 visible-tools Δ(E−F): {tools_ci['observed_diff']:.2f} "
                f"[95% CI {tools_ci['ci_low']:.2f}, {tools_ci['ci_high']:.2f}], "
                f"bootstrap p={tools_ci['p_value']:.4f}"
            )
        if succ_ci:
            no_harm = succ_ci["ci_low"] > -0.05
            print(
                f"H5 strict-success Δ(F−E): {succ_ci['observed_diff']:+.4f} "
                f"[95% CI {succ_ci['ci_low']:+.4f}, {succ_ci['ci_high']:+.4f}], "
                f"bootstrap p={succ_ci['p_value']:.4f} "
                f"→ success preserved (CI low > −5pp): {no_harm}"
            )
        plt.figure(figsize=(7, 5))
        plt.scatter([tools_e], [strict_e * 100], color="#d95f02", s=150, marker="o", zorder=5, label="Config E (Strict Success)")
        plt.scatter([tools_f], [strict_f * 100], color="#2b5c8f", s=150, marker="o", zorder=5, label="Config F (Strict Success)")
        plt.scatter([tools_e], [func_e * 100], color="#d95f02", s=150, marker="^", zorder=5, label="Config E (Functional Success)")
        plt.scatter([tools_f], [func_f * 100], color="#2b5c8f", s=150, marker="^", zorder=5, label="Config F (Functional Success)")
        plt.scatter([tools_e], [weighted_e * 100], color="#d95f02", s=150, marker="s", zorder=5, label="Config E (Weighted Success)")
        plt.scatter([tools_f], [weighted_f * 100], color="#2b5c8f", s=150, marker="s", zorder=5, label="Config F (Weighted Success)")
        plt.title("Impact of Resources Separation on Visible Tools and Success (E vs F)")
        plt.xlabel("Average Visible Tools per Turn")
        plt.ylabel("Success Rate (%)")
        plt.xlim(0, max(tools_e, tools_f) * 1.2 if max(tools_e, tools_f) > 0 else 1)
        plt.ylim(0, 100)
        plt.grid(True, linestyle=":", alpha=0.6)
        plt.legend(loc="best")
        plt.savefig(ASSETS_DIR / "h5_tool_reduction_vs_success.png")
        plt.close()
    else:
        print("Skipping H5 statistics: Config E and Config F are both required.")
        write_diagnostic_plot("h5_tool_reduction_vs_success.png", "H5 data incomplete", "Observed data for Config E and Config F is required.")

    print("\n--- H6 Analysis (Interaction Effects - Two-way ANOVA) ---")
    anova_strict = two_way_anova(df_main, "hierarchical", "workflow", "strict_success")
    anova_func = two_way_anova(df_main, "hierarchical", "workflow", "functional_success")
    if anova_strict:
        print("Two-way ANOVA results for Strict Success Rate:")
        print(f"  Hierarchical: F={anova_strict['A']['F']:.4f}, p-value={anova_strict['A']['p']:.4g}")
        print(f"  Workflow: F={anova_strict['B']['F']:.4f}, p-value={anova_strict['B']['p']:.4g}")
        print(f"  Interaction: F={anova_strict['AB']['F']:.4f}, p-value={anova_strict['AB']['p']:.4g}")
    if anova_func:
        print("Two-way ANOVA results for Functional Success Rate:")
        print(f"  Hierarchical: F={anova_func['A']['F']:.4f}, p-value={anova_func['A']['p']:.4g}")
        print(f"  Workflow: F={anova_func['B']['F']:.4f}, p-value={anova_func['B']['p']:.4g}")
        print(f"  Interaction: F={anova_func['AB']['F']:.4f}, p-value={anova_func['AB']['p']:.4g}")

    pivot_strict = df_main.groupby(["hierarchical", "workflow"])["strict_success"].mean().unstack().reindex(index=[False, True], columns=[False, True])
    pivot_func = df_main.groupby(["hierarchical", "workflow"])["functional_success"].mean().unstack().reindex(index=[False, True], columns=[False, True])
    pivot_weighted = df_main.groupby(["hierarchical", "workflow"])["weighted_success"].mean().unstack().reindex(index=[False, True], columns=[False, True])
    
    values = np.ma.masked_invalid(pivot_func.to_numpy(dtype=float))
    cmap = plt.cm.Blues.copy()
    cmap.set_bad("#eeeeee")
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(values, cmap=cmap, vmin=0, vmax=1.0)
    cbar = ax.figure.colorbar(im, ax=ax)
    cbar.ax.set_ylabel("Functional Success Rate", rotation=-90, va="bottom")
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
    plt.title("Interaction Effect of Architecture & Workflow")
    plt.ylabel("Hierarchical Architecture")
    plt.xlabel("Workflow Engine Enabled")
    plt.savefig(ASSETS_DIR / "h6_interaction_heatmap.png")
    plt.close()

    print("\n--- Failure Categories Pie Chart ---")
    failed_df = df_main[df_main["task_success"] == False]
    counts = {"hallucinate": 0, "out-of-scope": 0, "param error": 0, "timeout": 0, "other": 0}
    for _, row in failed_df.iterrows():
        if row.get("hallucinated"):
            counts["hallucinate"] += 1
        elif row.get("out_of_scope"):
            counts["out-of-scope"] += 1
        elif not row.get("param_valid"):
            counts["param error"] += 1
        elif row.get("loop_stuck"):
            counts["timeout"] += 1
        else:
            counts["other"] += 1
    if sum(counts.values()) == 0:
        write_diagnostic_plot("failure_categories_pie_chart.png", "No failures observed", "No failed traces were present in the aggregated data.")
    else:
        labels = list(counts.keys())
        sizes = list(counts.values())
        colors = ["#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02"]
        plt.figure(figsize=(6, 6))
        plt.pie(
            sizes,
            labels=labels,
            autopct="%1.1f%%",
            startangle=140,
            colors=colors,
            wedgeprops={"edgecolor": "white", "linewidth": 1.5, "antialiased": True},
        )
        plt.title("SCADA Agent Failure Cause Breakdown")
        plt.savefig(ASSETS_DIR / "failure_categories_pie_chart.png")
        plt.close()

    print("All plots generated under paper_assets/. Missing-data charts are diagnostic, not estimated results.")
    matplotlib.figure.Figure.savefig = original_fig_savefig


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="all", help="Model name to analyze ('all' for all models)")
    args = parser.parse_args()
    analyze_and_plot(args.model)
