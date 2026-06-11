# Phase 4/5 数据覆盖 Gap 分析

> 生成日期: 2026-06-11
>
> 对比基线: 《开发计划》§2.6 (Phase 4) / §2.7 (Phase 5) / §3 (实验计划) / §4 (数据处理) / §5 (交付物)
>
> 现有工具: `eval/metrics.py` + `eval/judges.py` + `eval/runner.py` + `agent/tracer.py`

---

## 0. 结论速览

**原始 per-trace 数据基本够用，但聚合/统计/可视化完全空白。**

- ✅ Phase 4 **数据采集** (runner + traces.jsonl): 可直接用，扩展到 14,850 trace 无结构性障碍
- ✅ Phase 4 **确定性评分** (metrics.py): 30+ 指标覆盖了 H1-H6 所需的绝大多数原始字段
- ✅ Phase 4 **语义评分** (judges.py + rubrics): 7 维度 + failure category 覆盖 LLM 主观评测需求
- ⚠️ Phase 4 **成本追踪**: `cost_usd` 恒为 0.0, Judge `usage` 恒为 `{}` (无法统计实验实际花费)
- ❌ Phase 5 **数据聚合**: `scripts/aggregate.py` 不存在
- ❌ Phase 5 **统计检验**: 无 t-test / ANOVA / bootstrap / CI 计算能力
- ❌ Phase 5 **可视化**: `notebooks/` 为空，无可画图 notebook
- ❌ Phase 5 **报告生成**: `scripts/make_report.py` 不存在
- ❌ Phase 4/5 **Trace-Join**: metrics.jsonl 与 judges.jsonl 未自动合并，judge 字段恒为 None

---

## 1. Phase 4 (实验执行) 需求 vs 现状

### 1.1 数据采集 — ✅ 基本就绪

| 需求 | 现状 | 差距 |
|------|------|------|
| 6 配置 × 3 模型 × 5 reps × 100 query = 9,000 trace | runner 支持 `--config --model --reps --all` | 无结构性障碍 |
| Tool 总数扫描 3,600 trace | 需要配置文件支持 Tool 数量参数，`configs/` 下暂无 sweep 专用配置 | 需新建 sweep 配置 |
| Top-K 扫描 2,250 trace | 同上，需 `tool_rag.top_k` 参数化 | 需新建 sweep 配置 |
| 失败率 < 2% | `--max-reruns` 支持重试，`_failures.jsonl` 记录失败 | 已就绪 |
| Langfuse 分类筛选 | tracer 集成了 Langfuse (但实际是否部署未确认) | 需确认 Langfuse 可用性 |

### 1.2 确定性指标 — ✅ 就绪

`eval/metrics.py` 的 `evaluate_trace()` 输出 30+ 字段/trace，覆盖:

| H 假设 | 需要的指标 | metrics.py 是否产出 | 字段名 |
|--------|-----------|-------------------|--------|
| H1 | Tool 选择 F1 | ✅ | `tool_selection_f1` + `precision` + `recall` |
| H1 | Hallucinated Tool Rate | ✅ | `hallucinated_tool_rate` |
| H1 | Out-of-Scope Rate | ✅ | `out_of_scope_tool_rate` |
| H1 | Domain/Action 分层诊断 | ✅ | `domain_match_accuracy`, `action_match_accuracy` |
| H1 | visible tool 数量 | ✅ | `visible_count_mean` |
| H2 | 参数有效性 | ✅ | `parameter_validity`, `parameter_match`, `schema_violation_rate` |
| H2 | 终态匹配 | ✅ | `final_state_match`, `match_mode`, `final_state_report` |
| H3 | 越权调用计数 | ✅ | `out_of_scope`, `out_of_scope_tool_rate` |
| H3 | forbidden_tools 违反 | ✅ | `forbidden_tools_violated` |
| H4 | 任务成功率 | ✅ | `task_success`, `task_success_deterministic` |
| H4 | 步数/效率 | ✅ | `step_count`, `step_efficiency` |
| H4 | 轨迹匹配 | ✅ | `trajectory_match`, `order_correctness`, `order_distance` |
| H4 | Workflow 命中 | ✅ | `workflow_hit`, `expected_workflow_id`, `selected_workflow` |
| H5 | Tool 列表长度 | ✅ | `visible_count_mean` |
| H5 | Resource 先读后写 | ✅ | `resource_query_before_action` |
| H6 | 级联失败 | ✅ | `cascade_failure_count`, `cascade_failure_rate` |
| 通用 | Loop/Stuck | ✅ | `loop_stuck` |
| 通用 | Token 消耗 | ✅ | `input_tokens`, `output_tokens` |
| 通用 | 延迟 | ✅ | `e2e_latency_ms`, `latency_per_turn_ms` |
| 通用 | 成本 | ⚠️ | `cost_usd` 存在但**恒为 0.0** |

### 1.3 语义评分 — ✅ 就绪

`eval/judges.py` 的 Judge 输出 7 维度评分:

| Judge 维度 | 用途 |
|-----------|------|
| `task_completion` | 端到端任务完成度 |
| `tool_correctness` | Tool 选择正确性 |
| `parameter_correctness` | 参数正确性 |
| `step_efficiency` | 步骤效率 |
| `clarification_or_rejection_quality` | 负例/追问质量 |
| `communication_quality` | 沟通质量 |
| `overall` | 综合评分 |

加 `passed` (bool) 和 `failure_category` (13 类枚举)。

### 1.4 关键缺失项

| 缺失 | 影响 | 严重度 |
|------|------|--------|
| `cost_usd` 恒为 0.0 | 无法统计实验实际花费，无法做成本-效益分析 | ⚠️ 中 |
| Judge `usage` 恒为 `{}` | 无法统计 Judge 阶段 token 消耗 | ⚠️ 中 |
| Trace 与 Judge 未自动合并 | metrics 中 `judge_*` 字段恒为 None，分析时需手动 join | 🔴 高 |
| 无 sweep 配置文件 | 主实验 2 (Tool 数量扫描) 和主实验 3 (Top-K 扫描) 无法直接启动 | ⚠️ 中 |

---

## 2. Phase 5 (分析与报告) 需求 vs 现状

### 2.1 数据聚合 — ❌ 缺失

**开发计划要求**: `scripts/aggregate.py` → 输出 `results/aggregated.parquet`

**现状**: 不存在。`eval/metrics.py` 的 `aggregate_summary()` 只做简单 mean，不分组。

需要实现:
- 遍历 `results/` 下所有 config/model/run_id
- 合并 traces.jsonl + metrics.jsonl + judges.jsonl
- 输出扁平 DataFrame → Parquet
- 列结构: `config_name, model, golden_id, rep, complexity, domain, visible_count_mean, tool_selection_f1, hallucinated, out_of_scope, param_valid, task_success, step_count, order_distance, input_tokens, output_tokens, cost_usd, e2e_latency_ms, judge_completion, judge_tool, judge_param, judge_efficiency, ...`

### 2.2 统计检验 — ❌ 缺失

**开发计划要求 (§3.6)**:

| 统计需求 | 用于 | 现状 |
|---------|------|------|
| 单侧 t-test | H1~H4 成功率差值 | ❌ 无 |
| Mann-Whitney U | 延迟分布比较 | ❌ 无 |
| Bonferroni 校正 | 多重比较 | ❌ 无 |
| 95% CI (bootstrap) | 所有点估计 | ❌ 无 |
| Cohen's d / Cliff's δ | 效应量 | ❌ 无 |
| 双因素 ANOVA | H6 交互效应 | ❌ 无 |
| 标准差 | 行为方差 / 可复现性 | ❌ `aggregate_summary` 只算 mean |

### 2.3 可视化 — ❌ 缺失

**开发计划要求 (§4.5)**: 6+ 张关键图

| 假设 | 需要的图表 | 现状 |
|------|----------|------|
| H1 | x=Tool总数, y=F1; 两条线(扁平 vs 分层), 误差带 | ❌ |
| H2 | 双 y 轴: 成功率 vs 延迟, 柱状对比 | ❌ |
| H3 | 越权调用率箱线图 (配置 D vs E) | ❌ |
| H4 | 成功率柱状 + 方差箱线 | ❌ |
| H5 | Tool 数减少 vs 成功率变化散点 | ❌ |
| H6 | 交互效应热图 (2×2 或 4×4) | ❌ |
| 通用 | 每配置的成本/延迟分布直方 | ❌ |
| 通用 | 复杂度 × 配置的成功率热图 | ❌ |
| 通用 | 失败案例分类饼图 | ❌ |

### 2.4 报告生成 — ❌ 缺失

**开发计划要求**: `scripts/make_report.py` → 自动生成 `EXPERIMENT_REPORT.md`

- 自动填充假设的数值、CI、p 值
- 自动嵌入图表
- 自动列出 top-10 失败案例
- 自动生成"反直觉发现"候选清单

---

## 3. Per-Hypothesis 数据支持度矩阵

| 假设 | 原始指标 | 统计方法 | 聚合/分组 | 可视化 | 报告 |
|------|---------|---------|----------|--------|------|
| **H1** (分层Tool优于扁平) | ✅ tool_selection_f1 | ❌ 无 t-test | ❌ 无 config 分组 | ❌ 无图 | ❌ |
| **H2** (RAG提升成功率) | ✅ task_success + e2e_latency_ms | ❌ 无 Mann-Whitney | ❌ | ❌ | ❌ |
| **H3** (状态机消除越权) | ✅ out_of_scope_tool_rate | ❌ 无 检验 | ❌ | ❌ | ❌ |
| **H4** (Workflow提升完成率) | ✅ task_success + workflow_hit | ❌ 无 σ / 方差比较 | ❌ | ❌ | ❌ |
| **H5** (Resources不损成功率) | ✅ visible_count + task_success | ❌ | ❌ | ❌ | ❌ |
| **H6** (四位一体交互效应) | ✅ 同上 | ❌ 无 ANOVA | ❌ | ❌ | ❌ |
| **稳定性** | ✅ rep 数据存在 | ❌ 无 Jaccard/编辑距离方差 | ❌ | ❌ | ❌ |
| **成本** | ⚠️ cost_usd=0 | — | — | — | — |

**结论: 现有工具提供了足够的原始 per-trace 数据来支持 H1-H6 的证据采集，但从原始数据到可引用的实验报告之间的整条"后处理链"是空白的。**

---

## 4. 建议实施优先级

### P0 (Phase 5 的前置依赖, 必须先做)

1. **`scripts/aggregate.py`** — 合并 traces + metrics + judges → aggregated.parquet
   - 自动 walk `results/` 目录结构
   - Join traces.jsonl ↔ judges.jsonl (by trace_id/golden_id)
   - 填充 metrics 中的 `judge_*` 占位字段
   - 输出扁平 DataFrame

2. **Trace-Join 补全** — 在 metrics 或 aggregate 层自动 merge judge 结果
   - 可选方案 A: `eval/metrics.py` 的 CLI 加 `--judges` 参数，自动 join
   - 可选方案 B: 在 `aggregate.py` 中统一处理 (推荐)

3. **`cost_usd` 计算** — `agent/tracer.py` 根据 model 名查询定价表计算
   - 可用 `litellm.completion_cost()` 或硬编码已知模型定价

### P1 (Phase 5 分析核心)

4. **`notebooks/01_main_results.ipynb`** — 主结果分析 notebook
   - 读 aggregated.parquet
   - Per-config 分组汇总 (mean, std, CI)
   - H1-H6 对应的图表
   - 统计检验 (scipy.stats / statsmodels)

5. **`notebooks/02_ablation.ipynb`** — 消融与交互效应
   - 双因素 ANOVA (statsmodels)
   - 效应量计算
   - 交互热图

### P2 (Phase 4 配置就绪)

6. **Sweep 配置文件** — Tool 数量扫描和 Top-K 扫描的 YAML 配置
7. **`scripts/make_report.py`** — 自动生成实验报告

### P3 (nice-to-have)

8. **`aggregate_summary()` 增强** — 支持 std / CI / per-config 分组
9. **Langfuse 确认** — 确保真实模型跑批时 trace 可分类查看
10. **Judge cost 追踪** — 从 OpenAI response 中提取 actual usage tokens

---

## 5. 需要决策的事项

| 项 | 问题 | 建议 |
|---|------|------|
| `cost_usd` 计算 | 用 litellm 动态查询还是硬编码定价? | litellm 已在依赖中，用它; 离线兜底硬编码 |
| 统计检验库 | scipy 还是 statsmodels? | statsmodels 更适合 ANOVA; scipy 做 t-test/Mann-Whitney |
| Notebook 语言 | Python only 还是 Jupyter? | Jupyter, 方便交互探索 + 图表嵌入报告 |
| Trace-Join 时机 | metrics 层还是 aggregate 层? | aggregate 层更干净, metrics 保持只做确定性评分 |
| 可视化库 | plotly 还是 matplotlib? | 开发计划提到两者都用; 建议 plotly 交互图 + matplotlib 静态论文图 |
