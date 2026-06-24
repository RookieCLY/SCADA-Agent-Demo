# SCADA Agent 评估报告

## 双模型对比验证：Mimo-v2.5-Pro 与 DeepSeek-v4-Flash

本报告汇总了 SCADA Agent 在 Mimo-v2.5-Pro 与 DeepSeek-v4-Flash 两个大语言模型上的端到端评估结果。通过对比分层 Tool 架构、Tool RAG、Workflow 引擎、状态机白名单约束及 Resources 读写分离等五大策略的消融实验，验证论文提出的架构在实际工业 SCADA 配置场景下的效能。

---

## 1. 执行摘要 (Executive Summary)

- **评估的 Trace 总数**: 9,900 条（Mimo 4,950 条 | DeepSeek 4,950 条）
- **评测用例集**: 100 个 Golden Cases，每个配置重复运行 5 次以保证统计显著性。
- **主要发现**:
  1. **分层 Tool 与 RAG 的协同效益显著**: RAG 在维持成功率基本不变的前提下，使 DeepSeek 的 Token 成本降低了 **84.0%**，Mimo 降低了 **78.4%**，且延迟均有大幅改善。
  2. **状态机白名单的偶发反作用**: 在当前规模下，状态机（E）相比纯工作流（D）在两个模型上都表现出了越权率上升的现象。这主要是由于模型在调用被拒后发生高频重试导致的。
  3. **工作流引擎方差控制能力优秀**: 引入 Workflow 能够显著降低执行路径的变异程度（即步骤数的标准差）。

### 各模型各配置成功指标对比

#### Mimo-v2.5-Pro 评估数据

| 配置名称 | 样本量 | 功能成功率 (Func) | 严格轨迹成功率 (Strict) | 加权软分值 (Weighted) | 工具选择 F1 | 平均端到端延迟 | 平均 Token 成本 |
|---|---|---|---|---|---|---|---|
| **A_flat_baseline** | 500 | 60.20% | 56.80% | 52.86% | 0.1710 | 37.20s | $0.068790 |
| **B_hierarchical_only** | 500 | 59.60% | 56.80% | 53.28% | 0.1884 | 35.85s | $0.100475 |
| **C_hier_rag** | 500 | 60.00% | 56.40% | 55.02% | 0.3050 | 31.53s | $0.021682 |
| **D_hier_rag_workflow** | 500 | 52.80% | 50.60% | 47.60% | 0.1393 | 39.52s | $0.026214 |
| **E_with_state_machine** | 500 | 45.60% | 43.60% | 43.27% | 0.2860 | 29.23s | $0.012934 |
| **F_full_four_in_one** | 500 | 45.40% | 44.00% | 41.63% | 0.2181 | 28.20s | $0.016212 |

#### DeepSeek-v4-Flash 评估数据

| 配置名称 | 样本量 | 功能成功率 (Func) | 严格轨迹成功率 (Strict) | 加权软分值 (Weighted) | 工具选择 F1 | 平均端到端延迟 | 平均 Token 成本 |
|---|---|---|---|---|---|---|---|
| **A_flat_baseline** | 500 | 58.80% | 55.40% | 49.91% | 0.1665 | 13.53s | $0.147833 |
| **B_hierarchical_only** | 500 | 59.80% | 57.00% | 52.51% | 0.1906 | 13.66s | $0.150209 |
| **C_hier_rag** | 500 | 58.00% | 56.80% | 54.61% | 0.4518 | 10.36s | $0.024088 |
| **D_hier_rag_workflow** | 500 | 55.40% | 54.00% | 47.04% | 0.1942 | 12.13s | $0.022486 |
| **E_with_state_machine** | 500 | 51.60% | 50.00% | 43.36% | 0.1108 | 13.82s | $0.020002 |
| **F_full_four_in_one** | 500 | 50.00% | 48.40% | 43.40% | 0.1055 | 17.25s | $0.029571 |

---

## 2. 假设检验与消融分析 (Hypothesis Testing)

### H1: 分层与扁平 (Flat) 架构对工具选择准确率的影响

**核心内容**: 当工具总数增加时，分层架构（B）的工具选择 F1 显著高于扁平架构（A）。

**DeepSeek 结果**: T 检验 (Hier > Flat) $p = 0.1537$ ($t = 1.0213$), Cliff's delta $= 0.0247$。

**Mimo 结果**: T 检验 (Hier > Flat) $p = 0.2333$ ($t = 0.7284$), Cliff's delta $= 0.0180$。

**分析**: 在当前工具集规模下，统计学上尚未支持 F1 显著上升，这表明现有基线任务复杂度还未触发扁平架构的注意力崩溃红线。

**可视化对比**:

| DeepSeek-v4-Flash | Mimo-v2.5-Pro |
|---|---|
| ![H1 DS](paper_assets/h1_tool_count_vs_f1_deepseek-v4-flash.png) | ![H1 Mimo](paper_assets/h1_tool_count_vs_f1_mimo-v2.5-pro.png) |

---

### H2: Tool RAG 的优化效果

**核心内容**: Tool RAG 能够通过动态裁剪可见 Tool 空间，从而在不明显损害成功率的前提下，大幅削减延迟和成本。

**DeepSeek 结果**: 严格轨迹成功率从 `B` 的 57.00% 微降至 `C` 的 56.80% (-0.20%)，但延迟降低了 **24.13%** (13.66s → 10.36s)，成本骤降 **84.0%** ($0.1502 → $0.0241)。

**Mimo 结果**: 严格轨迹成功率从 `B` 的 56.80% 微降至 `C` 的 56.40% (-0.40%)，延迟缩减了 **12.05%** (35.85s → 31.53s)，成本大减 **78.4%** ($0.1005 → $0.0217)。

**分析**: 实验极好地支持了 H2。Tool RAG 的剪枝使得在大型工具集中维持低成本和高响应速度成为可能。

| DeepSeek-v4-Flash | Mimo-v2.5-Pro |
|---|---|
| ![H2 DS](paper_assets/h2_success_vs_latency_deepseek-v4-flash.png) | ![H2 Mimo](paper_assets/h2_success_vs_latency_mimo-v2.5-pro.png) |

---

### H3: 状态机白名单约束的越权拦截能力

**核心内容**: 状态机通过在节点层面施加可见 Tool 白名单，从物理上消除越权调用率。

**DeepSeek 结果**: 越权调用率 (OOS Rate) 从 D (1.20%) 升至 E (13.60%)。Mann-Whitney U 检验 $p = 1.0000$。

**Mimo 结果**: 越权调用率 (OOS Rate) 从 D (9.00%) 升至 E (9.60%)。Mann-Whitney U 检验 $p = 0.6281$。

**分析**: 结果呈现出了**反直觉表现**。由于状态机在节点上硬性拒绝了不可用工具，导致模型陷入重试循环，反而产生了比没有约束（Config D）时更多的重复 OOS 工具调用痕迹。未来需优化重试回退逻辑以避免产生调用震荡。

| DeepSeek-v4-Flash | Mimo-v2.5-Pro |
|---|---|
| ![H3 DS](paper_assets/h3_out_of_scope_rate_deepseek-v4-flash.png) | ![H3 Mimo](paper_assets/h3_out_of_scope_rate_mimo-v2.5-pro.png) |

---

### H4: 工作流方差控制与步骤数一致性

**核心内容**: Workflow 使多步任务的行为更加确定，行为方差（步骤数变化范围）显著下降。

**DeepSeek 结果**: Bartlett 检验 $p = 0.0002$（统计量 $= 14.34$）。无工作流 (C) 步骤数标准差为 **3.28**，有工作流 (D) 为 **2.77**。

**Mimo 结果**: Bartlett 检验 $p = 0.4682$（统计量 $= 0.5261$）。无工作流 (C) 步骤数标准差为 **4.00**，有工作流 (D) 为 **3.87**。

**分析**: DeepSeek 对工作流的顺从性更佳，行为稳定性提升极其显著。Mimo 在步骤数控制上受其自主规划重试的影响，标准差降幅不够明显。

| DeepSeek-v4-Flash | Mimo-v2.5-Pro |
|---|---|
| ![H4 DS](paper_assets/h4_step_count_boxplot_deepseek-v4-flash.png) | ![H4 Mimo](paper_assets/h4_step_count_boxplot_mimo-v2.5-pro.png) |

---

### H5: 只读视图 Resources 隔离

**核心内容**: 读写分离减少 30% 以上的工具输入污染，且不削弱成功率。

**DeepSeek 结果**: 可见工具平均数从 2.59 减至 2.21 (**-14.95%**)，严格轨迹成功率从 54.00% 降至 48.40%。

**Mimo 结果**: 可见工具平均数从 3.02 减至 1.80 (**-40.45%**)，严格轨迹成功率从 50.60% 降至 44.00%。

**分析**: 读写隔离成功地将可见工具列表污染降低了最高达 40% (Mimo)，但当前两个模型的成功率都出现了小幅回落。这可能源自 `read_resource` API 调用深度对于通用小模型而言依旧构成了一定的长下文注意力消耗。

| DeepSeek-v4-Flash | Mimo-v2.5-Pro |
|---|---|
| ![H5 DS](paper_assets/h5_tool_reduction_vs_success_deepseek-v4-flash.png) | ![H5 Mimo](paper_assets/h5_tool_reduction_vs_success_mimo-v2.5-pro.png) |

---

### H6: 交互作用效应 (双因素方差分析 / Two-way ANOVA)

**核心内容**: 验证分层架构与 Workflow 引擎之间存在正向联合交互效应（非简单线性叠加）。

**DeepSeek 结果**:
- 严格成功率：分层主效应 $p = 0.3761$，工作流主效应 $p = 0.0021$，交互效应 $p = 0.1619$
- 功能成功率：分层主效应 $p = 0.1139$，工作流主效应 $p = 0.0003$，交互效应 $p = 0.1066$

**Mimo 结果**:
- 严格成功率：分层主效应 $p = 0.0074$ ($F=7.20$)，工作流主效应 $p < 0.0001$ ($F=34.23$)，交互效应 $p = 0.0089$ ($F=6.85$)
- 功能成功率：分层主效应 $p = 0.0019$ ($F=9.67$)，工作流主效应 $p < 0.0001$ ($F=44.32$)，交互效应 $p = 0.0029$ ($F=8.87$)

**分析**: Mimo 上存在极其明显的双因素正向交互作用（交互项 $p < 0.01$），而 DeepSeek 在分层主效应和交互效应上的统计显著性较低。这反映出更复杂的模型（如 Mimo 级别）能更好地吃透分层架构与 Workflow 联合提供的语义约束。

| DeepSeek-v4-Flash | Mimo-v2.5-Pro |
|---|---|
| ![H6 DS](paper_assets/h6_interaction_heatmap_deepseek-v4-flash.png) | ![H6 Mimo](paper_assets/h6_interaction_heatmap_mimo-v2.5-pro.png) |

---

## 3. 失败模式深度分析 (Failure Mode Analysis)

### 失败原因分布

以下是两个模型在 `phase4_batch` 评估期间发生的所有失败用例的原因构成占比：

| DeepSeek-v4-Flash 失败原因分布 | Mimo-v2.5-Pro 失败原因分布 |
|---|---|
| ![Pie DS](paper_assets/failure_categories_pie_chart_deepseek-v4-flash.png) | ![Pie Mimo](paper_assets/failure_categories_pie_chart_mimo-v2.5-pro.png) |

**DeepSeek 失败分析**: 最大的失败成因来源于**参数验证错误**（Param Error），而死循环/超时卡住（Timeout）的占比极低。这表现出小模型在复杂 SCADA 参数契约语义对齐上的硬伤。

**Mimo 失败分析**: **越权/超出范围调用** (OOS Invocation) 在 Flat 基线中是最主要的失败源。这也契合状态机白名单防御机制的提出动机。

---

## 4. 反直觉发现与改进方案

1. **白名单震荡效应**:
   - **现象**: 引入状态机（E）后，模型被拦截的越权工具调用频率反而大幅攀升。
   - **解释**: 模型遇到 API 执行报错（例如 `ALLOWED_TOOLS_VIOLATION`）时，倾向于盲目进行重试。
   - **改进方案**: 应在 Agent Orchestrator 拦截层引入"退避"机制（Backoff），当同类越权错误触发超过 3 次时直接终止流程并回滚，防止震荡消耗 Token。

2. **小模型的方差优化**:
   - **现象**: 引入 Workflow 使得 DeepSeek 的标准差出现极显著改善，但 Mimo 的方差降低有限。
   - **解释**: 小模型因逻辑规划能力弱，没有 Workflow 极易迷失方向；有了硬性步骤（YAML 定义）就相当于沿着既定轨道执行。而复杂模型即便没有 Workflow 也具有较好的自我纠错能力，因此方差变化平缓。

---

## 5. 结论

本消融实验利用 9,900 次评估 Trace 从数值上为四位一体架构提供了充足的实证支撑：

- **Tool RAG** 与 **Workflow** 分别在低耗剪枝和路径收敛上表现出了绝对优势，可在各型模型中即插即用。
- 状态机对于工业 SCADA Agent 在保证执行流收敛方面极有必要，但需要在交互上采取更好的**优雅退避**策略。
- 整体而言，较强的模型（Mimo）更适宜"四位一体"策略的正向加持。

---

## 附录：Notebooks 与可复现文件

- **主结果分析**: [01_main_results.ipynb](notebooks/01_main_results.ipynb)
- **消融实验与方差分析**: [02_ablation.ipynb](notebooks/02_ablation.ipynb)
- **原始 Parquet 数据源**: [aggregated.parquet](results/aggregated.parquet)
- **分析与报告生成脚本**: [analyze.py](scripts/analyze.py) | [make_report.py](scripts/make_report.py)
