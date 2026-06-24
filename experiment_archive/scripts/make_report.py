#!/usr/bin/env python3
"""
scripts/make_report.py

Reads results/aggregated.parquet and generates EXPERIMENT_REPORT.md.
The report uses observed data only; incomplete hypotheses are marked as such.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import numpy as np
import pandas as pd
import polars as pl
import scipy.stats as stats


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def fmt_pct(value: float | None, digits: int = 2) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{value:.{digits}%}"


def fmt_num(value: float | None, digits: int = 4) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{value:.{digits}f}"


def fmt_seconds(ms_value: float | None) -> str:
    return "N/A" if ms_value is None or pd.isna(ms_value) else f"{ms_value / 1000:.2f}s"


def compute_cliffs_delta(x: pd.Series, y: pd.Series) -> float | None:
    x_arr = np.asarray(x.dropna())
    y_arr = np.asarray(y.dropna())
    if len(x_arr) == 0 or len(y_arr) == 0:
        return None
    diffs = 0
    for val_x in x_arr:
        diffs += np.sum(val_x > y_arr) - np.sum(val_x < y_arr)
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
        "A": {"F": f_a, "p": stats.f.sf(f_a, df_a, df_error)},
        "B": {"F": f_b, "p": stats.f.sf(f_b, df_b, df_error)},
        "AB": {"F": f_ab, "p": stats.f.sf(f_ab, df_ab, df_error)},
    }


def locate_trace(trace_id: str, results_root: Path) -> tuple[Path | None, int | None]:
    if not trace_id:
        return None, None
    for path in results_root.rglob("traces.jsonl"):
        try:
            with path.open("r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    if trace_id in line:
                        return path.resolve(), line_num
        except OSError:
            pass
    return None, None


def load_dataframe(parquet_path: Path) -> pd.DataFrame:
    df = pd.DataFrame(pl.read_parquet(parquet_path).to_dicts())
    features = df["config_name"].apply(map_config_features)
    df["hierarchical"] = [item[0] for item in features]
    df["rag"] = [item[1] for item in features]
    df["workflow"] = [item[2] for item in features]
    df["state_machine"] = [item[3] for item in features]
    df["resources"] = [item[4] for item in features]
    return df


def main(model_name: str | None = None) -> None:
    parquet_path = PROJECT_ROOT / "results" / "aggregated.parquet"
    if not parquet_path.exists():
        print("aggregated.parquet not found. Please run scripts/aggregate.py first.")
        sys.exit(1)

    df = load_dataframe(parquet_path)
    if model_name:
        df = df[df["model"] == model_name]
    df_main = df[df["experiment"] == "phase4_batch"]
    config_groups = {name: group for name, group in df_main.groupby("config_name")}
    summary_stats = {
        name: {
            "count": len(group),
            "success_rate": group["task_success"].mean(),
            "task_success_rate": group["task_success"].mean(),
            "strict_success_rate": group["strict_success"].mean(),
            "functional_success_rate": group["functional_success"].mean(),
            "weighted_success_rate": group["weighted_success"].mean(),
            "f1": group["tool_selection_f1"].mean(),
            "latency": group["e2e_latency_ms"].mean(),
            "cost_usd": group["cost_usd"].mean(),
        }
        for name, group in config_groups.items()
    }

    def get_stat(config: str, metric: str) -> float | None:
        return summary_stats.get(config, {}).get(metric)

    flat_grp = df_main[df_main["config_name"] == "A_flat_baseline"]
    hier_grp = df_main[df_main["config_name"] == "B_hierarchical_only"]
    c_grp = df_main[df_main["config_name"].isin(["C_hier_rag", "C_hier_rag_workflow"])]
    grp_d = df_main[df_main["config_name"].isin(["D_hier_rag_workflow", "D_minimal"])]
    grp_e = df_main[df_main["config_name"] == "E_with_state_machine"]
    grp_f = df_main[df_main["config_name"] == "F_full_four_in_one"]

    h1_p = h1_t = h1_cliffs = None
    if len(flat_grp) > 0 and len(hier_grp) > 0:
        h1_t, h1_p = stats.ttest_ind(
            hier_grp["tool_selection_f1"],
            flat_grp["tool_selection_f1"],
            alternative="greater",
            nan_policy="omit",
        )
        h1_cliffs = compute_cliffs_delta(hier_grp["tool_selection_f1"], flat_grp["tool_selection_f1"])

    h2_available = len(hier_grp) > 0 and len(c_grp) > 0
    success_b = get_stat("B_hierarchical_only", "strict_success_rate")
    func_success_b = get_stat("B_hierarchical_only", "functional_success_rate")
    latency_b = get_stat("B_hierarchical_only", "latency")
    success_c = c_grp["strict_success"].mean() if len(c_grp) > 0 else None
    func_success_c = c_grp["functional_success"].mean() if len(c_grp) > 0 else None
    latency_c = c_grp["e2e_latency_ms"].mean() if len(c_grp) > 0 else None
    latency_delta = None
    if latency_b and latency_c:
        latency_delta = (latency_c - latency_b) / latency_b

    h3_p = h3_u = oos_d_mean = oos_e_mean = None
    if len(grp_d) > 0 and len(grp_e) > 0:
        oos_d = grp_d["out_of_scope"].astype(float)
        oos_e = grp_e["out_of_scope"].astype(float)
        h3_u, h3_p = stats.mannwhitneyu(oos_d, oos_e, alternative="greater")
        oos_d_mean = oos_d.mean()
        oos_e_mean = oos_e.mean()

    no_workflow_grp = c_grp if len(c_grp) > 0 else hier_grp
    no_workflow_label = "C_hier_rag" if len(c_grp) > 0 else "B_hierarchical_only (C missing)"
    h4_bartlett = h4_p = steps_no_wf_std = steps_wf_std = None
    if len(no_workflow_grp) > 0 and len(grp_d) > 0:
        steps_no_wf = no_workflow_grp["step_count"].dropna().astype(float)
        steps_wf = grp_d["step_count"].dropna().astype(float)
        steps_no_wf_std = steps_no_wf.std()
        steps_wf_std = steps_wf.std()
        if len(steps_no_wf) >= 2 and len(steps_wf) >= 2 and steps_no_wf.var() != 0.0 and steps_wf.var() != 0.0:
            h4_bartlett, h4_p = stats.bartlett(steps_no_wf, steps_wf)

    success_d = success_f = func_success_d = func_success_f = tools_d = tools_f = tool_reduction = None
    if len(grp_d) > 0 and len(grp_f) > 0:
        success_d = grp_d["strict_success"].mean()
        success_f = grp_f["strict_success"].mean()
        func_success_d = grp_d["functional_success"].mean()
        func_success_f = grp_f["functional_success"].mean()
        tools_d = grp_d["visible_count_mean"].mean()
        tools_f = grp_f["visible_count_mean"].mean()
        tool_reduction = (tools_d - tools_f) / tools_d if tools_d > 0 else None

    anova_res = two_way_anova(df_main, "hierarchical", "workflow", "strict_success")
    anova_func_res = two_way_anova(df_main, "hierarchical", "workflow", "functional_success")

    failed_df = df_main[df_main["task_success"] == False].copy()

    def get_failure_reason(row: pd.Series) -> str:
        if row.get("hallucinated"):
            return "幻觉工具 (Hallucinated Tool)"
        if row.get("out_of_scope"):
            return "越权/超出范围调用 (Out of Scope Invocation)"
        if not row.get("param_valid"):
            return "参数验证错误 (Parameter Validation Error)"
        if row.get("loop_stuck"):
            return "死循环/超时卡住 (Loop/Stuck Timeout)"
        return f"错误码: {row.get('error_code') or 'UNKNOWN'}"

    failed_df["failure_reason"] = failed_df.apply(get_failure_reason, axis=1)
    top_failures = failed_df.head(10)

    discoveries = []
    f1_flat_val = get_stat("A_flat_baseline", "f1")
    f1_hier_val = get_stat("B_hierarchical_only", "f1")
    if f1_flat_val is not None and f1_hier_val is not None and f1_flat_val > f1_hier_val:
        discoveries.append({
            "title": "在当前规模下，Flat 基线表现优于分层架构",
            "observation": f"Flat 基线的 F1 值 ({fmt_pct(f1_flat_val)}) 高于分层架构的 F1 值 ({fmt_pct(f1_hier_val)})。",
            "explanation": "当前观测的工具空间规模可能足够小，以至于扁平选择尚未发生预期的注意力衰退。",
        })
    latency_d_val = get_stat("D_minimal", "latency")
    if latency_d_val is not None and latency_b is not None and latency_d_val > latency_b * 1.3:
        discoveries.append({
            "title": "工作流引擎增加了明显的延迟",
            "observation": f"D_minimal 延迟 ({fmt_seconds(latency_d_val)}) 高于 B_hierarchical_only ({fmt_seconds(latency_b)})。",
            "explanation": "工作流检查和中间步骤验证可能会引入额外的循环和累积的端到端延迟。",
        })

    paper_assets_url = (PROJECT_ROOT / "paper_assets").as_uri()
    notebooks_url = (PROJECT_ROOT / "notebooks").as_uri()
    scripts_url = (PROJECT_ROOT / "scripts").as_uri()

    def get_img(name: str) -> str:
        if model_name:
            safe_model = model_name.replace("/", "_").replace(" ", "_")
            stem = name[:-4]
            return f"{paper_assets_url}/{stem}_{safe_model}.png"
        return f"{paper_assets_url}/{name}"

    report_path = PROJECT_ROOT / "EXPERIMENT_REPORT.md"
    with report_path.open("w", encoding="utf-8") as f:
        f.write(f"""# SCADA Agent 评估报告

自动生成的科学评估报告，汇总了可用配置运行的观测结果。

## 执行摘要

- **评估的 Trace 总数**: {len(df)}
- **基线成功率 (Flat)**: 主要功能: {fmt_pct(get_stat("A_flat_baseline", "task_success_rate"))} | 严格轨迹: {fmt_pct(get_stat("A_flat_baseline", "strict_success_rate"))}
- **分层架构成功率**: 主要功能: {fmt_pct(get_stat("B_hierarchical_only", "task_success_rate"))} | 严格轨迹: {fmt_pct(get_stat("B_hierarchical_only", "strict_success_rate"))}
- **完整四合一成功率**: 主要功能: {fmt_pct(get_stat("F_full_four_in_one", "task_success_rate"))} | 严格轨迹: {fmt_pct(get_stat("F_full_four_in_one", "strict_success_rate"))}
- **观测到的配置**: {", ".join(sorted(config_groups))}

### 各配置成功指标对比

| 配置名称 | Trace 数量 | 主要功能成功率 (Functional) | 严格轨迹成功率 (Trajectory-Aware) | 加权成功率 (Multi-factor Soft Metric) | 工具选择 F1 值 | 端到端延迟 | 成本 (USD) |
|---|---|---|---|---|---|---|---|
| **A_flat_baseline** | {get_stat("A_flat_baseline", "count") or 0} | {fmt_pct(get_stat("A_flat_baseline", "task_success_rate"))} | {fmt_pct(get_stat("A_flat_baseline", "strict_success_rate"))} | {fmt_pct(get_stat("A_flat_baseline", "weighted_success_rate"))} | {fmt_num(get_stat("A_flat_baseline", "f1"))} | {fmt_seconds(get_stat("A_flat_baseline", "latency"))} | ${fmt_num(get_stat("A_flat_baseline", "cost_usd"))} |
| **B_hierarchical_only** | {get_stat("B_hierarchical_only", "count") or 0} | {fmt_pct(get_stat("B_hierarchical_only", "task_success_rate"))} | {fmt_pct(get_stat("B_hierarchical_only", "strict_success_rate"))} | {fmt_pct(get_stat("B_hierarchical_only", "weighted_success_rate"))} | {fmt_num(get_stat("B_hierarchical_only", "f1"))} | {fmt_seconds(get_stat("B_hierarchical_only", "latency"))} | ${fmt_num(get_stat("B_hierarchical_only", "cost_usd"))} |
| **C_hier_rag** | {get_stat("C_hier_rag", "count") or 0} | {fmt_pct(get_stat("C_hier_rag", "task_success_rate"))} | {fmt_pct(get_stat("C_hier_rag", "strict_success_rate"))} | {fmt_pct(get_stat("C_hier_rag", "weighted_success_rate"))} | {fmt_num(get_stat("C_hier_rag", "f1"))} | {fmt_seconds(get_stat("C_hier_rag", "latency"))} | ${fmt_num(get_stat("C_hier_rag", "cost_usd"))} |
| **D_hier_rag_workflow** | {get_stat("D_hier_rag_workflow", "count") or 0} | {fmt_pct(get_stat("D_hier_rag_workflow", "task_success_rate"))} | {fmt_pct(get_stat("D_hier_rag_workflow", "strict_success_rate"))} | {fmt_pct(get_stat("D_hier_rag_workflow", "weighted_success_rate"))} | {fmt_num(get_stat("D_hier_rag_workflow", "f1"))} | {fmt_seconds(get_stat("D_hier_rag_workflow", "latency"))} | ${fmt_num(get_stat("D_hier_rag_workflow", "cost_usd"))} |
| **E_with_state_machine** | {get_stat("E_with_state_machine", "count") or 0} | {fmt_pct(get_stat("E_with_state_machine", "task_success_rate"))} | {fmt_pct(get_stat("E_with_state_machine", "strict_success_rate"))} | {fmt_pct(get_stat("E_with_state_machine", "weighted_success_rate"))} | {fmt_num(get_stat("E_with_state_machine", "f1"))} | {fmt_seconds(get_stat("E_with_state_machine", "latency"))} | ${fmt_num(get_stat("E_with_state_machine", "cost_usd"))} |
| **F_full_four_in_one** | {get_stat("F_full_four_in_one", "count") or 0} | {fmt_pct(get_stat("F_full_four_in_one", "task_success_rate"))} | {fmt_pct(get_stat("F_full_four_in_one", "strict_success_rate"))} | {fmt_pct(get_stat("F_full_four_in_one", "weighted_success_rate"))} | {fmt_num(get_stat("F_full_four_in_one", "f1"))} | {fmt_seconds(get_stat("F_full_four_in_one", "latency"))} | ${fmt_num(get_stat("F_full_four_in_one", "cost_usd"))} |

---

## 假设检验与分析

### H1: 分层与扁平(Flat)架构
- **状态**: {"已接受" if h1_p is not None and h1_p < 0.05 and h1_cliffs and h1_cliffs > 0 else "当前数据不支持"}
- **结果**: T 检验 (Hier > Flat) p 值 = **{fmt_num(h1_p)}** (t = **{fmt_num(h1_t)}**), Cliff's delta = **{fmt_num(h1_cliffs)}**。
- **图表**: ![H1 工具扩展图]({get_img("h1_tool_count_vs_f1.png")})

### H2: 工具 RAG 性能
- **状态**: {"已评估" if h2_available else "未评估: 缺少 Config C_hier_rag 数据"}
- **结果**:
  - **严格轨迹成功率**: B = **{fmt_pct(success_b)}** vs C = **{fmt_pct(success_c)}** (变化量: **{fmt_pct(success_c - success_b) if success_c is not None and success_b is not None else "N/A"}**)
  - **主要功能成功率**: B = **{fmt_pct(func_success_b)}** vs C = **{fmt_pct(func_success_c)}** (变化量: **{fmt_pct(func_success_c - func_success_b) if func_success_c is not None and func_success_b is not None else "N/A"}**)
  - **端到端延迟**: B = **{fmt_seconds(latency_b)}** vs C = **{fmt_seconds(latency_c)}** (变化比例: **{fmt_pct(latency_delta) if latency_delta is not None else "N/A"}**)。
- **图表**: ![H2 成功率与延迟图]({get_img("h2_success_vs_latency.png")})

### H3: 状态机约束验证
- **状态**: {"已接受" if h3_p is not None and h3_p < 0.05 else "当前数据不支持"}
- **结果**: Mann-Whitney U 检验 p 值 = **{fmt_num(h3_p)}** (U = **{fmt_num(h3_u, 1)}**)。越权/超出范围调用率 (Out-of-Scope Rate): D = **{fmt_pct(oos_d_mean)}** vs E = **{fmt_pct(oos_e_mean)}**。
- **图表**: ![H3 越权调用率柱状图]({get_img("h3_out_of_scope_rate.png")})

### H4: 工作流方差降低
- **状态**: {"已接受" if h4_p is not None and h4_p < 0.05 else "差异不显著或方差不足"}
- **对比组**: {no_workflow_label} 对比 D_hier_rag_workflow/D_minimal。
- **结果**: Bartlett 检验 p 值 = **{fmt_num(h4_p)}** (统计量 = **{fmt_num(h4_bartlett)}**)。步骤数标准差: 无工作流 = **{fmt_num(steps_no_wf_std, 2)}** vs 有工作流 = **{fmt_num(steps_wf_std, 2)}**。
- **图表**: ![H4 步骤数箱线图]({get_img("h4_step_count_boxplot.png")})

### H5: 资源隔离
- **状态**: {"已接受" if success_f is not None and success_d is not None and tool_reduction is not None and abs(success_f - success_d) < 0.05 and tool_reduction > 0.3 else "当前数据不支持"}
- **结果**:
  - **严格轨迹成功率**: D = **{fmt_pct(success_d)}** vs F = **{fmt_pct(success_f)}**
  - **主要功能成功率**: D = **{fmt_pct(func_success_d)}** vs F = **{fmt_pct(func_success_f)}**
  - **可见工具数**: D = **{fmt_num(tools_d, 1)}** vs F = **{fmt_num(tools_f, 1)}** (变化比例: **{fmt_pct(-tool_reduction) if tool_reduction is not None else "N/A"}**)。
- **图表**: ![H5 工具减少与成功率散点图]({get_img("h5_tool_reduction_vs_success.png")})

### H6: 交互作用效应 (双因素方差分析 / Two-way ANOVA)
- **状态**: {"已评估" if anova_res else "未评估: 缺乏平衡的双因素数据"}
- **结果 (严格轨迹成功率)**:
  - 分层架构主效应: F = **{fmt_num(anova_res["A"]["F"]) if anova_res else "N/A"}** (p = **{fmt_num(anova_res["A"]["p"]) if anova_res else "N/A"}**)
  - 工作流主效应: F = **{fmt_num(anova_res["B"]["F"]) if anova_res else "N/A"}** (p = **{fmt_num(anova_res["B"]["p"]) if anova_res else "N/A"}**)
  - 交互效应: F = **{fmt_num(anova_res["AB"]["F"]) if anova_res else "N/A"}** (p = **{fmt_num(anova_res["AB"]["p"]) if anova_res else "N/A"}**)
- **结果 (主要功能成功率)**:
  - 分层架构主效应: F = **{fmt_num(anova_func_res["A"]["F"]) if anova_func_res else "N/A"}** (p = **{fmt_num(anova_func_res["A"]["p"]) if anova_func_res else "N/A"}**)
  - 工作流主效应: F = **{fmt_num(anova_func_res["B"]["F"]) if anova_func_res else "N/A"}** (p = **{fmt_num(anova_func_res["B"]["p"]) if anova_func_res else "N/A"}**)
  - 交互效应: F = **{fmt_num(anova_func_res["AB"]["F"]) if anova_func_res else "N/A"}** (p = **{fmt_num(anova_func_res["AB"]["p"]) if anova_func_res else "N/A"}**)
- **图表**: ![H6 交互效应热力图]({get_img("h6_interaction_heatmap.png")})

---

## 失败模式分析

失败原因的分布图见下：

![失败原因饼图]({get_img("failure_categories_pie_chart.png")})

### Top-10 失败案例

| 排名 | 配置名称 | 黄金用例 ID (Golden ID) | 失败类别 | Trace 链接 |
| --- | --- | --- | --- | --- |
""")

        for idx, (_, row) in enumerate(top_failures.iterrows(), 1):
            trace_id = row.get("trace_id") or ""
            trace_path, line_num = locate_trace(trace_id, PROJECT_ROOT / "results")
            if trace_path:
                trace_url = trace_path.as_uri() + f"#L{line_num}"
                link_text = f"[Trace {trace_id[:8]}]({trace_url})"
            else:
                link_text = f"`{trace_id[:8]}` (文件未链接)"
            f.write(f"| {idx} | {row.get('config_name')} | {row.get('golden_id')} | {row['failure_reason']} | {link_text} |\n")

        f.write("\n---\n\n## 反直觉发现\n\n")
        if not discoveries:
            f.write("规则引擎未标记任何反直觉发现。\n")
        else:
            for discovery in discoveries:
                f.write(f"### {discovery['title']}\n\n")
                f.write(f"- **观测结果**: {discovery['observation']}\n")
                f.write(f"- **原因解释**: {discovery['explanation']}\n\n")

        f.write(f"""---

## 可复现性与代码引用

- **主结果 Notebook**: [01_main_results.ipynb]({notebooks_url}/01_main_results.ipynb)
- **消融实验与方差分析 Notebook**: [02_ablation.ipynb]({notebooks_url}/02_ablation.ipynb)
- **数据聚合 CLI 脚本**: [aggregate.py]({scripts_url}/aggregate.py)
- **分析 CLI 脚本**: [analyze.py]({scripts_url}/analyze.py)
- **报告生成 CLI 脚本**: [make_report.py]({scripts_url}/make_report.py)
""")

    print(f"Successfully generated EXPERIMENT_REPORT.md at: {report_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="deepseek-v4-flash", help="Model name to generate report for")
    args = parser.parse_args()
    main(args.model)
