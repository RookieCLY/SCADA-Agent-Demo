# 将 LLM 关进笼子里——工业 SCADA Agent 的约束架构与功能安全边界

Author：崔留洋

---

## 摘要

随着大语言模型（LLM）在工业自动化领域的渗透，将其引入SCADA（数据采集与监视控制系统）软件以构建SCADA-Agent正成为工控领域研究热点。然而，工业SCADA系统天然具有原子操作数量庞大（通常300~1000+）、状态依赖强、流程严格的特征，直接将全部能力暴露给LLM会引发注意力稀释、工具混淆、上下文爆炸、推理漂移等一系列问题，导致Agent在生产环境中不可用。本文阐述了**作者主张的**工业级SCADA Agent设计哲学：**通过"分层工具（Hierarchical Tools）+ 工具检索增强（Tool RAG）+ 工作流编排（Workflow）+ 状态机（State Machine）"的四位一体架构，将LLM约束在一个可控、有限、确定的空间内工作**，以"降低自由度"换取"提升确定性"。本文以"生成化工厂生产监控界面"为完整案例，详细演示了该架构下Agent的真实调用链与每一步的Tool裁剪过程，并最终升华到Agent工程化的核心哲学："把概率模型逐步约束成近似确定性系统"。从范式视角看，本文所提架构是 **LLM Agent Harness**（承载 LLM 的运行时框架）在工业控制领域的**垂类特化（Vertical Harness）**——通用 Harness（如 Claude Code、WorkBuddy）只需一层软件工程笼子，工业垂类 Harness 还须叠加功能安全层，以应对认证、不可逆性、强工艺约束等物理世界硬约束。把创造性留给LLM，把确定性留给系统。

**关键词**：SCADA、LLM Agent、Agent Harness、垂类 Harness（Vertical Harness）、Tool RAG、Workflow、状态机、MCP、工业自动化、功能安全、确定性

---

## 1. 引言与总览

### 1.1 研究背景

工业SCADA系统是流程工业、电力、水务、化工等领域的核心控制软件。一个典型的SCADA项目涉及：

- 页面/界面组态
- 图元绘制与动画
- 点位（Tag）创建与绑定
- 报警策略配置
- 历史曲线与趋势图
- 联锁与脚本逻辑
- 权限与部署

每一类操作下又包含数十个原子能力，整个系统的能力空间动辄达到300~1000+个原子Tool。

而工程师真正希望 AI Agent 承担的工作，并不是单个原子操作的调用，而是端到端的工程任务，例如：

- "根据工艺描述生成一个化工厂生产监控画面"
- "为锅炉系统配置完整的高低限报警策略"
- "搭建反应釜温度与压力的历史趋势分析"
- "把 PLC 标签清单批量映射到画面元素"
- "为新增的换热器一键生成图元、点位、报警与趋势"

这类任务每一个都涉及数十到数百个原子操作，且操作之间存在**严格的工艺顺序与状态依赖**：必须先创画面才能添加图元、必须先创图元才能配置动画、必须先创设备才能建点位、必须有图元与点位才能完成绑定、必须先校验通过才能部署。这种"任务结构性"远高于通用编程或文档工作场景，是 SCADA Agent 设计真正的核心难点。

### 1.2 核心问题

当工程师尝试将上述能力以 MCP（Model Context Protocol）的 Tool 形式暴露给 LLM 以构建 AI-Agent 时，会立刻遭遇一组**层层叠加**的失效问题——这些问题不止是"LLM 选错一个工具"，而是横跨从单步到多步、从工具调用到任务编排的完整谱系：

| 层级      | 症状                              | 后果                |
| ------- | ------------------------------- | ----------------- |
| **工具层** | Tool Selection Error（选错工具）      | 调用错误能力，单步失败       |
| **工具层** | 近义 Tool 混淆（如 `create_alarm` 系列） | 调用不稳定、回归不通过       |
| **工具层** | 上下文爆炸 / 注意力稀释                   | Token 成本失控、关键信息退权 |
| **工具层** | 推理路径变长                          | 响应延迟严重            |
| **任务层** | 工序错乱（先绑点位再建图元、先部署再校验）           | 任务整体失败、状态不一致      |
| **任务层** | 漏关键步骤（忘记校验、忘记设置死区、忘记测试触发）       | 上线后才发现，影响生产       |
| **任务层** | 参数漂移（同一对象在多步间命名/单位/ID 不一致）      | 隐藏 bug，难定位        |
| **任务层** | 长流程中状态丢失（Agent "忘记"前期决策）        | 风格不统一、设计不连贯       |
| **任务层** | 失败无法恢复（无事务边界，留下半成品组态）           | 需人工清理后才能重试        |
| **领域层** | 越权调用敏感工具（直接写运行态变量）              | 工业事故风险            |

> **严重程度随模型代次有所改善**——2023~2024 年的旗舰模型在数十 Tool 即明显劣化，2026 年的旗舰模型在 100~200 Tool 量级仍可保持 70%+ 的端到端准确率，但**症状本身**至今未被消除，只是阈值后移。任务层与领域层的问题更不是"换更大模型"能解决的——它们源自任务本身的结构性与工业控制的物理约束，必须靠**架构约束**来兜底。

### 1.3 Agent Harness 范式与本文定位

在讨论 SCADA Agent 具体架构之前，先简要交代一个上层范式——**Agent Harness**。

"Harness"在 LLM 应用工程中指**包裹大模型外的运行时框架**，承担工具执行、权限管控、上下文管理、流程编排、会话持久化等职责，把"会输出 tool_use 的语言模型"承载为"能在真实环境中完成任务的 Agent"。代表实现有 Claude Code、Cursor、LangGraph、Temporal、Prefect 等，协议层以 MCP（Model Context Protocol）形成跨 Harness 互通规范。其共同范式可一句话概括：

> **LLM 是推理协处理器，Runtime 才是执行主体。**

通用 Harness 提供的是开放机制（工具白名单、Hooks、上下文策略由用户临场组装），适用于代码编辑、文档问答等可试错场景。但当 Harness 进入**强约束垂直领域**（工业控制、金融、医疗、法律）时，失败不可逆、认证体系严苛、领域工艺复杂——通用机制不够用，必须演化为 **垂类 Harness（Vertical Harness）**：把领域工艺、合规标准、安全边界**预先内化为 Harness 自身**，而非留给 LLM 临场组装。

**本文定位**：后文阐述的"分层 Tool + Tool RAG + Workflow + 状态机 + 功能安全"五位一体架构，本质上就是**工业控制场景下的垂类 Agent Harness 设计规范**——§3~§5 给出其构造方法，§6~§7 把它放回更宽的 Harness 谱系讨论哲学定位。

### 1.4 研究目标

本文要回答的核心问题不止于"如何在大量 Tool 中选对一个"，而是更广义的：

> **当 SCADA Agent 面对"生成生产监控画面"、"配置报警策略"、"搭建趋势分析"等真实工业任务时，这些任务涉及数十到数百个原子操作，且操作之间存在严格的工艺顺序、状态依赖与参数关联——如何通过架构约束，让 LLM 能够稳定、准确、可恢复地完成它们？**

具体地，需要 LLM 在数百上千个原子能力的 Agent 中同时做到：

- **选对工具**：不在近义 Tool 中混淆，不被无关 Tool 稀释注意力
- **按对顺序**：尊重 SCADA 工艺依赖（先画面 → 后图元、先点位 → 后绑定、先校验 → 后部署）
- **守住状态**：长流程中前后一致，不丢失早期决策、不出现参数漂移
- **填对参数**：跨步之间的名称、单位、ID 联动一致
- **出错可回滚**：失败时不留下"半成品组态"，能恢复到一致状态
- **守住红线**：永不越界进入运行态写操作或安全联锁回路（详见 §4.7）

**值得强调的是**：LLM 并不天然知道"先创画面再创图元、先创设备再建点位、有图元有点位才能绑点"这套工艺顺序——这些来自 SCADA 项目实施的工程经验，而非语言模型的训练语料。本文的核心主张是：**与其期望 LLM"学会"这些约束，不如把它们硬编码到 Agent Runtime 中，让 LLM 在每个受控节点上只负责局部决策**。这正是后续章节"分层 Tool + Tool RAG + Workflow + 状态机 + 功能安全"五位一体架构（即 §1.3 所述"工业控制垂类 Harness"）的根本动机。

---

## 2. 问题分析：工业 SCADA Agent 的多层失效

SCADA Agent 在真实生产任务中的失效，绝不仅仅是"LLM 在大量 Tool 中选错一个"。当任务规模从"调一个 API"扩大到"端到端生成监控画面"时，失效会分布在三个相互叠加的层次：

- **工具层**：在大量 Tool 中选错、混淆相似 Tool、context 被 Tool 描述挤爆（§2.1~§2.4）
- **任务层**：工序错乱、漏关键步骤、参数漂移、长流程状态丢失、失败无法回滚（§2.5）
- **领域层**：SCADA 操作天然原子化且顺序敏感、失效不可逆（§2.6）

真正难的不是任一单层问题，而是三层叠加——SCADA Agent 既要在大量 Tool 中选对，又要按工艺顺序串成长流程，还要在物理后果不可逆的前提下完成。这正是工业 Agent 设计必须叠加多重约束（§3~§4）的根本原因。

### 2.1 LLM 工具选择（Tool Selection）的本质

即便是 2026 年的旗舰级推理型模型在显式思维链加持下已显著改善长链规划，LLM 的 Tool 选择**底层仍是**基于上下文条件分布的采样：

```
基于上下文的概率选择 (next-token sampling on tool-call distribution)
```

推理链改善的是"在脑中先列候选再筛"的过程，**但并未把它变成确定性的最优搜索**——当候选 Tool 列表长、名称相似、领域专有度高时，分布的尾部仍会污染输出。因此当 Prompt 中存在大量 Tool 时，会出现一组**工具层失效**（§2.2~§2.4）；而当 SCADA 任务跨越数十步、涉及强工艺依赖时，更会叠加一类**任务层失效**（§2.5）——后者无法靠"换更大模型"消除：

### 2.2 注意力稀释（Attention Dilution）

Transformer 的 Attention 是有限资源。当大量 Tool 描述同时存在时，模型会：

- 记不住
- 分不清
- 注意力退化

尤其当 Tool 名称相似时（如 `create_alarm`、`create_alarm_group`、`create_alarm_template`、`update_alarm`、`bind_alarm`），模型极易混淆。

**2026 年的实测情况**：根据 Berkeley Function Calling Leaderboard（BFCL v3/v4，2026-04 数据），即便是位居榜首的模型，整体准确率也只在 76% 左右；BFCL 团队总结的关键发现是"top AIs ace one-shot questions but still stumble when they must remember context, manage long conversations, or decide when not to act"。即"近义 Tool 混淆"与"多轮状态下的记忆/弃权决策失败"仍是开放挑战。**注意力稀释不再是"100 Tool 接近随机"式的断崖，而是一条缓慢但稳定下降的曲线，并在近义/多轮场景下被显著放大。**

### 2.3 工具描述冲突

SCADA Tool经常在语义、参数、动词上高度重复。例如：

```
create_point
add_point
insert_point
register_point
```

LLM很难稳定区分这些近义Tool，导致调用不稳定。

### 2.4 上下文窗口耗尽（Context Window Exhaustion）

一个典型Tool定义：

```json
{
  "name": "create_trend_chart",
  "description": "Create a trend chart...",
  "parameters": { ... }
}
```

约占200~1000 token。100个Tool即2w~10w token，在 2026 年 1M~10M 上下文窗口的旗舰模型下，**token 占用本身不再是硬瓶颈**，但**注意力稀释**仍是真问题——窗口大≠注意力有效，注入大量 Tool 同样会稀释关键信息的有效权重，并显著增加每轮推理的成本与延迟。

### 2.5 任务层失效：当 LLM 必须串起多步工艺

即便 LLM 在每一步都"选对了 Tool"，多步任务仍可能整体失败。SCADA 任务的特殊性在于：**单一原子操作的成功并不意味着整体任务的成功——任务结构本身就是失败源**。

**(1) 工序错乱（Step Order Violation）**

SCADA 任务有严格的工艺顺序：必须先创画面才能添加图元、先创图元才能配置动画、先创设备才能建点位、有图元有点位才能绑点、校验通过才能部署。LLM 训练语料中没有这些行业约束，常常自由调度——例如先调 `bind_tag` 再调 `create_tag`，整个任务在第一步就失败；或者跳过校验直接 `deploy_project`，把错误组态推到运行环境。

**(2) 漏关键步骤（Missing Prerequisite）**

即便顺序大致正确，LLM 也容易跳过"看起来不显眼"的步骤——生成画面后忘记校验未绑点位、配置完报警忘记测试触发条件、绑定完点位忘记设置死区与单位、批量创建后忘记重命名 ID。这些步骤往往不影响"接口调用是否成功"，但直接决定最终质量。

**(3) 参数漂移（Parameter Drift）**

多步之间的参数必须联动一致——画面中"反应釜 1"的名称、点位中 `TEMP_REACTOR1` 的标签、报警中 `TEMP_REACTOR1_HI` 的命名要严格匹配。LLM 在长流程中常出现命名漂移、单位前后不同（°C vs K）、ID 拼写差异等问题，导致后续步骤找不到目标。

**(4) 长流程中状态丢失**

一个"生成化工厂监控画面"任务可能涉及 30~50 步、对话历史几万 token。LLM 在后期常"忘记"前期决策——已经选定横向布局，却在添加新设备时切回竖向；已经为反应釜配过红色，新设备却用了不一致的色系；已经把所有点位归到 `Group_Reactor1`，新点位却散在根目录。

**(5) 失败无法恢复**

LLM 自由式 Agent 通常没有事务边界，第 17 步失败时前 16 步的副作用已经写入。没有补偿机制时，系统会留下"半成品组态"——画面建了一半、点位绑了一半、报警配了一半，需要人工清理才能重试。

这五类失效都不是"换更大模型"能解决的——它们源自 SCADA 任务本身的结构性，而非模型的能力上限。即便是 2026 年的旗舰模型，在 20~30 步的长链工业任务上仍会持续暴露这些问题。这正是 §4 中 **Workflow** 与 **状态机** 两大策略的存在理由：**把任务结构从 LLM 的"自由推理"中剥离出来，固化到 Agent Runtime 自身**。

### 2.6 SCADA 的领域特殊性：高结构 + 不可逆

上述工具层、任务层问题在通用 Agent 场景下都存在，但 SCADA 把它们推到极致：

1. **能力高度原子化**：创建矩形、设置颜色、绑定变量、配置动画、添加脚本、创建报警、设置死区、设置上下限……每一项都是独立操作。若全部暴露成 Tool，Agent 很快变成"Tool Zoo"——几乎所有 AI-组态项目最先踩的坑。
2. **操作高度顺序敏感**：与通用编程"代码顺序可由编译器/运行时容错"不同，SCADA 操作的顺序就是工艺逻辑本身，错位即失败、且失败往往不可恢复。
3. **状态强依赖**：每一步的合法性都依赖前序步骤产生的状态——点位的存在、图元的位置、画面的尺寸都是后续操作的前提，LLM 无法从训练语料中推导这些依赖。
4. **失效成本远高于通用场景**：通用 Agent 写错代码可以 revert；SCADA Agent 一旦把错误组态部署到运行系统，可能直接干扰生产——这部分边界进一步在 §4.7 功能安全章详述。

这四点叠加，使得 SCADA Agent 必须比通用 Agent 多承担一层"任务结构性约束 + 物理边界约束"——这就是后续章节"分层 Tool + Tool RAG + Workflow + 状态机 + 功能安全"五位一体架构存在的根本原因。

---

## 3. 方案描述：五大核心策略

针对上述问题，本文基于业界经验和已有主流解决方案，总结了五大核心策略，提出了四位一体架构解决方案。其中**前四者构成"决策骨架"**（即摘要中所述的"四位一体"核心架构），**第五者 MCP Resources 分离作为关键的"支撑技术"**，两者共同形成完整方案：

| 类别   | 策略                       | 核心思想                         | 防护的主要失效（对应 §2 失效层）             |
| ---- | ------------------------ | ---------------------------- | ------------------------------ |
| 决策骨架 | 分层工具（Hierarchical Tools） | 顶层暴露领域Tool，内部分发到原子操作         | 收缩选择面 → 防工具层选错与近义混淆            |
| 决策骨架 | 工具RAG（Tool RAG）          | 将Tool向量化，按Query检索Top-K相关Tool | 动态裁剪 + 多轮上下文融合 → 防工具层稀释、跨步参数漂移 |
| 决策骨架 | 工作流（Workflow）            | 预定义常见任务的执行序列                 | 强制工序 + 事务补偿 → 防工序错乱、漏步骤、失败无法回滚 |
| 决策骨架 | 状态机（State Machine）       | 按当前阶段动态裁剪可用Tool集             | 阶段化裁剪 + 状态持久化 → 防长流程状态丢失、越权调用  |
| 支撑技术 | MCP Resources分离          | 只读查询用Resources，避免污染Tools列表   | 隔离读写 → 减少 Tool 列表污染，降低注意力稀释    |

**与 §2 三层失效的对应**：这五大策略并非独立堆砌，而是分别压制不同失效层——**工具层失效**由分层 Tool + Tool RAG + MCP Resources 分离三者共同抑制；**任务层失效**由 Workflow + 状态机两者兜底（这是与通用 Agent 框架最大的差异）；**领域层失效**则在 §4.7 由功能安全层物理隔离。这正是 §1.3 所述"垂类 Harness 把领域知识与安全边界预先内化为 Harness 自身"的具体落实。

**生产实践的典型组合是**：以四位一体（Tool RAG + 分层Tool + Workflow + 状态机）构成决策主干，并辅以 MCP Resources 分离作为协议层的辅助手段，整体形成 2~3 层分发结构。

各策略之间不是替代关系，而是**正交分工**：

- **分层工具**：在"Tool命名空间"维度收缩选择面
- **Tool RAG**：在"语义相关性"维度动态裁剪
- **状态机**：在"时间/阶段"维度限制可见集合
- **Workflow**：在"流程依赖"维度强制顺序
- **MCP Resources**：在"读/写副作用"维度分离Tool列表

只有同时启用，才能从根本上让Agent在数百Tool规模下保持稳定。

这一方案范式并非业内"已收敛"的事实标准，目前主流SCADA厂商的AI方案大多仍停留在"组态推荐+局部增强"层级，本文主张的Workflow主导、四位一体架构应视为**目标态**而非现状。

---

## 4. 核心策略实现细节

### 4.1 分层工具（Hierarchical Tools）

#### 4.1.1 核心思想

不要直接暴露300个原子Tool，而是抽象出10个左右的**领域Tool（Domain Tool）**。每个领域Tool在LLM端表现为一个"门面（Façade）"，内部通过Dispatcher机制将LLM传入的 `action` 字段分发到具体的原子实现。

典型的二层结构如下表所示：

| 领域Tool            | 内部分发的原子操作（节选）                                                                               |
| ----------------- | ------------------------------------------------------------------------------------------- |
| `manage_pages`    | `create_page`、`delete_page`、`rename_page`、`clone_page`、`set_resolution`、`set_background`…   |
| `manage_points`   | `create_point`、`update_point`、`delete_point`、`bind_point`、`batch_import_points`、`set_unit`… |
| `manage_alarms`   | `create_analog_alarm`、`create_digital_alarm`、`bind_alarm`、`set_threshold`、`enable_alarm`…   |
| `manage_graphics` | `create_rect`、`create_circle`、`create_pipe`、`create_tank`、`set_color`、`set_animation`…      |
| `manage_history`  | `enable_history`、`set_sampling_rate`、`set_storage_policy`、`create_trend_chart`…             |
| `manage_scripts`  | `attach_script`、`detach_script`、`set_trigger`、`debug_script`…                               |

#### 4.1.2 接口设计：Action 参数 vs 嵌套 Tool

分层Tool的接口通常采用 **"action 枚举 + 联合参数"** 模式：

```json
{
  "tool": "manage_alarms",
  "arguments": {
    "action": "create_analog_alarm",
    "tag": "TEMP_101",
    "high_limit": 80.0,
    "deadband": 1.0,
    "priority": "high"
  }
}
```

LLM端只需要做两次小型选择：

1. **领域选择**：从约10个领域Tool中挑选 → `manage_alarms`
2. **动作选择**：在该领域的 `action` 枚举（约10~30个）中挑选 → `create_analog_alarm`
3. **参数填充**：按该 action 的Schema填字段

相比"在300个独立Tool里找一个"，这种结构把**组合爆炸的离散决策**降为**两次小型选择 + 一次参数生成**。

##### 4.1.2.1 取舍，不是唯一最佳实践

需要明确：**Action Dispatcher 模式并非业内公认的唯一最佳实践**，它与"扁平命名"各有适用场景：

| 维度          | 扁平命名（如 `create_analog_alarm`） | Action Dispatcher（如 `manage_alarms{action}`） |
| ----------- | ----------------------------- | -------------------------------------------- |
| 单 Tool 描述粒度 | 细，每个 Tool 独立                  | 粗，schema 被压扁为 union                          |
| RAG 召回粒度    | 细，可单独命中                       | 粗，召回到领域后还需 action 选择                         |
| Tool 总数     | 多                             | 少                                            |
| LLM 选择步数    | 1 步（多选一）                      | 2 步（领域 + action）                             |
| 业界经验        | 社区主流选择                        | 早期 MCP 案例与高 Tool 数系统更常见                      |

**经验法则**：

- **Tool 总数 ≤ 50**：用扁平命名，让 RAG 与 LLM 都能精确定位
- **Tool 总数 > 200**：用 Action Dispatcher 压缩选择面，否则注意力稀释严重
- **50~200 之间**：按领域内聚度判断——同领域内 action 语义高度相关用 Dispatcher，否则保持扁平

SCADA 系统因原子操作 300~1000+ 通常落在第二档，因此本文倾向 Dispatcher；但工具量更小的 Agent 不应盲目套用。

#### 4.1.3 Dispatcher 实现

后端Dispatcher是一个简单的 switch/match 结构，工业系统中通常用 C++、Rust 或 Go 实现以获得高性能与类型安全，伪代码如下：

```cpp
Result manage_alarms(const Args& args) {
    if (args.action == "create_analog_alarm") {
        return createAnalogAlarm(args);
    } else if (args.action == "create_digital_alarm") {
        return createDigitalAlarm(args);
    } else if (args.action == "bind_alarm") {
        return bindAlarm(args);
    }
    // ...
    return Error("unknown action: " + args.action);
}
```

Dispatcher层还负责：

- **参数Schema校验**：拒绝LLM生成的非法参数组合
- **权限检查**：基于RBAC的细粒度控制
- **审计日志**：记录每次action调用
- **指标上报**：统计哪些action被高频调用

#### 4.1.4 为什么有效：认知科学视角

LLM **更擅长语义领域选择，而不是底层 API 选择**。其原因可从两个角度理解：

1. **语义压缩**：领域Tool的名字（如 `manage_alarms`）天然对应人类思维中的"功能模块"概念，与用户Query的语义距离更近。
2. **参数生成是LLM强项**：LLM经过大量代码训练，对生成结构化JSON极为擅长，但对在长列表中做精确名称匹配并不擅长。

例如用户说"帮我创建一个报警"，LLM容易识别出 `alarm domain`，但难以从 `create_alarm_template_v2`、`create_alarm_group_v3`、`init_alarm_handler`、`add_alarm_rule` 等近义名中精确挑选。

#### 4.1.5 本质：扁平大空间 → 层级小空间

> **把"在 N 个 Tool 里挑 1 个"分解为"在 M 个领域里挑 1 个 + 在该领域的 K 个 action 里挑 1 个"。**

这是分层Tool最深刻的优化（其中 N ≈ M × K，且 M、K 都远小于 N）：

| 维度    | 扁平大空间选择     | 层级小空间选择         |
| ----- | ----------- | --------------- |
| 决策性质  | 一次性 N 选 1   | 两次小型 M/K 选 1    |
| LLM能力 | 随 N 增长准确率下降 | 受益于分层注意力        |
| 错误率   | 随Tool数线性增长  | 与每层规模弱相关        |
| 可控性   | 难（黑盒选择）     | 易（每层 Schema 约束） |

**这并不是完全把"选择问题"转化为"生成问题"**——LLM 依旧要做两次离散选择，只是每次的候选集都被显著压缩；更准确的说法是"扁平大空间 → 层级小空间，最后一步配以 Schema 受约束的参数生成"。

#### 4.1.6 分层粒度的权衡

分层不是越粗越好，存在一个最优区间：

| 粒度                                  | 优点         | 缺点                 |
| ----------------------------------- | ---------- | ------------------ |
| **过粗**（只有一个 `do_anything`）          | LLM选择极简单   | 参数空间爆炸，LLM无法生成有效参数 |
| **适中**（10~20个领域Tool，每个10~30个action） | 选择简单且参数可控  | 需良好的领域划分           |
| **过细**（300+原子Tool）                  | 每个Tool职责明确 | LLM选择困难，注意力稀释      |

**经验法则**：单个领域Tool的 action 数量控制在 10~30 个之间，超过 30 个应进一步拆分子领域。

#### 4.1.7 失败模式

分层Tool并非银弹，常见失败模式包括：

- **action 命名不一致**：`create_xxx` vs `add_xxx` vs `new_xxx` 混用，让LLM无所适从
- **参数Schema过于灵活**：允许 `extra: {...}` 之类的逃逸字段，让LLM"创造性生成"无效字段
- **action 之间存在隐式依赖**：例如必须先 `init_alarm` 才能 `bind_alarm`，但接口没体现 → 应交给Workflow或状态机处理
- **领域边界模糊**：例如"点位"和"报警"高度耦合时，应该合并还是分离？需要业务建模决策

---

### 4.2 工具RAG（Tool Retrieval-Augmented Generation）

#### 4.2.1 整体架构

```
            ┌─────────────────────┐
用户Query →  │   Query Embedding   │
            └──────────┬──────────┘
                       ↓
            ┌─────────────────────┐
            │  Vector Database    │  ← 离线构建：所有Tool的Embedding
            │  (Tool Embeddings)  │
            └──────────┬──────────┘
                       ↓
            ┌─────────────────────┐
            │   Top-N Retrieval   │  ← 召回：N ≈ 50
            └──────────┬──────────┘
                       ↓
            ┌─────────────────────┐
            │  Re-ranking (可选)   │  ← Cross-Encoder 精排
            └──────────┬──────────┘
                       ↓
            ┌─────────────────────┐
            │  Top-K Inject       │  ← 注入：K ≈ 10~20
            └──────────┬──────────┘
                       ↓
                      LLM
```

#### 4.2.2 为什么至关重要

**早期（2023~2024 年模型代次）经验数据**曾给出一组广为流传的硬阈值——"超过 50 准确率明显下降、超过 100 接近随机、超过 200 模型直接编造 Tool 名"。这些数字反映的是 GPT-3.5、Claude 2 一代模型的能力上限，**2026 年视角下需要修正**：

| 数据来源 / 基准                                   | 时间        | 主要发现                                                                                     |
| ------------------------------------------- | --------- | ---------------------------------------------------------------------------------------- |
| Berkeley Function Calling Leaderboard v3/v4 | 2026-04   | 旗舰模型（GLM-4.5、Qwen3-32B 等）整体准确率 ~76%；single-turn 已接近饱和；**multi-turn / 多步 / 状态记忆**仍是公认开放问题 |
| τ-bench（多轮工具调用 / Agentic）                   | 2025~2026 | 准确率随 Tool 总数增加呈**缓慢下降而非断崖**；近义工具间的相互混淆率上升更显著                                             |
| MCP-Bench 与各家厂商内部基准                         | 2026      | 上下文窗口达 1M+ 后，瓶颈从"token 占用"转移到"注意力有效利用"                                                   |

**2026 年修订后的经验法则**：

- LLM 在 **10~30 个 Tool** 范围内仍最稳定，错误率最低
- **30~100 个 Tool**：旗舰模型可用，但近义 Tool 的混淆率显著上升
- **100~500 个 Tool**：必须配合 Tool RAG + 状态机做硬过滤，否则误调率与延迟都不可接受
- **超过 500 个 Tool**：扁平暴露已无意义，必须用分层 + RAG

因此工业实践的铁律未变：**永远不要把全部 Tool 给 LLM**。Tool RAG 的本质是用"检索的确定性"过滤掉无关 Tool，把 LLM 的注意力集中在最相关的小集合上——这条结论在模型能力提升后依然成立，只是阈值整体后移了一个数量级。

#### 4.2.3 检索对象的设计

向量化的内容直接决定召回质量。一个高质量的 Tool Embedding 应当编码以下信息：

**(1) Tool Name**

```
create_analog_alarm
```

仅靠名字召回率有限，因为名字往往是技术术语，与用户自然语言距离远。

**(2) Tool Description**

```
Create an analog (continuous-value) alarm for a SCADA point,
supporting high/low limits, deadband, and priority.
```

描述应当包含：功能、应用场景、关键参数概念、典型行业用语。

**(3) 参数描述**

```
- tag:        the SCADA point to monitor
- high_limit: upper threshold (e.g., 80°C for temperature)
- deadband:   hysteresis to prevent alarm chattering
- priority:   high / medium / low
```

参数描述帮助检索匹配"含参用户Query"，例如"创建带死区的报警"会匹配到 `deadband` 字段。

**(4) 示例（最关键）**

详见 4.2.4。

#### 4.2.4 示例（Examples）：召回率的命脉

**示例是 Tool Embedding 中最重要的部分。** 用户Query往往不是API语言而是自然语言：

| 用户说法（自然语言）    | 对应Tool（技术语言）           |
| ------------- | ---------------------- |
| "给锅炉温度加个超限报警" | `create_analog_alarm`  |
| "压力高了报警一下"    | `create_analog_alarm`  |
| "液位太低提醒一下"    | `create_analog_alarm`  |
| "电机停机时弹窗"     | `create_digital_alarm` |
| "限位开关动作触发提示"  | `create_digital_alarm` |

如果没有这些示例，纯靠 Tool Name 与 Description 的 Embedding 相似度往往很差——"锅炉温度超限"和 `create_analog_alarm` 在向量空间中可能相距很远。

**最佳实践**：为每个Tool准备 **3~10 个不同表达的自然语言示例**，作为合成训练数据扩展 Embedding 索引。示例可以由领域专家撰写，也可以用 LLM 离线批量生成后人工筛选。

#### 4.2.5 检索策略

**(1) 稠密检索（Dense Retrieval）**

使用通用 Embedding 模型（如 `bge-m3、gte-Qwen2、Conan-embedding、OpenAI-text-embedding-4`）计算余弦相似度。

- 优点：语义泛化好，能匹配同义不同字的Query。
- 缺点：对专有名词（如 `TEMP_101` 这类Tag命名）召回率差。

**(2) 稀疏检索（Sparse Retrieval / BM25）**

基于关键词匹配（TF-IDF 改进版）。

- 优点：对专有名词、Tool名、参数名敏感。
- 缺点：完全不理解语义。

**(3) 混合检索（Hybrid Retrieval）**

工业系统几乎总是使用混合检索：

```
final_score = α · dense_score + (1 - α) · bm25_score
```

通常 α 取 0.6~0.7 **是一个经验起点，跨域、跨语种、跨语料波动很大，须配合 A/B 测试调参**。中文工业语料（含大量英文 Tool 名、Tag 命名、单位符号）下经验值往往偏向 0.5~0.65——稀疏检索权重高于通用语料。这种组合既保留语义泛化能力，又对工业专有名词敏感。

**(4) Re-ranking（精排）**

对召回的 Top-50 用更精细的 Cross-Encoder 进一步排序，取最终 Top-K。这是经典"召回-精排"两阶段架构。Cross-Encoder 准确率高但延迟大，因此只用于精排小集合。

#### 4.2.6 Top-K 的经验值

| Tool总数 | 召回 Top-N | 注入 Top-K |
| ------ | -------- | -------- |
| 50     | 全部       | 8~10     |
| 100    | 30       | 10~12    |
| 500    | 50       | 12~15    |
| 1000   | 80       | 15~20    |
| 5000+  | 100      | 20（再多无益） |

通常注入数不要超过 20。**K 过大会让 LLM 重新陷入"Tool过多"问题；K 过小会漏掉关键Tool**。生产系统应当结合 A/B 测试调优 K 值。**上表为粗略经验值，具体阈值与模型代次、检索质量、领域密度强相关，建议以基准回归测试为准而非把表中数字当作硬性约束**。

#### 4.2.7 失败模式与对策

| 失败模式       | 表现                  | 对策                         |
| ---------- | ------------------- | -------------------------- |
| **低召回**    | 关键Tool没出现在 Top-K 中  | 扩充示例、提高K、调整α、增加Re-rank     |
| **查询模糊**   | 用户表达过于抽象（"搞一下那个东西"） | 先用LLM做Query重写/扩展           |
| **冷启动**    | 新Tool没有真实用户Query作示例 | 用LLM合成示例数据，再人工校对           |
| **域偏差**    | 通用Embedding对工业术语不敏感 | Fine-tune 领域专用Embedding    |
| **同义词不召回** | 用户说"温度"而Tool用"temp" | 维护同义词表，离线扩展索引文本            |
| **多语言混杂**  | 中英混用的Query匹配差       | 使用多语种Embedding（如 `bge-m3`） |

#### 4.2.8 多轮上下文融合

Tool RAG 不应只看当前Query，还应融入：

- **历史对话**：用户之前提到过的设备、点位、上下文
- **当前 Workflow 状态**：处于哪个阶段，已完成什么
- **当前 State Machine 状态**：作为**硬过滤**（不在该状态白名单中的Tool直接淘汰）
- **用户角色**：工程师 vs 操作员，权限不同则Tool集合不同

最终公式：

```
Final Top-K = StateMachine_AllowedSet  ∩  RAG_Ranked_TopN
```

即：**状态机做硬过滤，RAG 做软排序**，两者交集形成最终注入的Tool列表。

#### 4.2.9 Tool RAG 的局限

需要清醒地认识到 Tool RAG 也有自身局限：

- **检索质量决定上限**：如果检索没召回，LLM 根本看不到正确Tool
- **示例需要持续维护**：业务变化时示例会过时
- **不解决长链推理**：RAG 只能给单步选Tool，长流程依赖仍需 Workflow
- **冷启动成本高**：每个新Tool需要写示例 + 重建索引

---

### 4.3 工作流（Workflow）

#### 4.3.1 核心思想

把复杂的多Tool操作封装成高层任务。例如用户说"创建一个泵站监控界面"，实际需要：

```
创建页面 → 创建图元（泵、管道、阀门） → 绑定点位 → 添加动画 →
配置报警 → 生成趋势图 → 设置权限 → 保存项目
```

如果全靠LLM自由规划，非常容易：漏步骤、顺序错、状态不一致、参数不匹配。Workflow 把这些"已知正确的执行序列"沉淀为代码资产，让Agent按既定剧本演出。

**Workflow 的本质：系统侧的"确定性可执行图"**

需要说明的是：在工业级 Agent 中，Workflow 既不是 Prompt，也不是 LLM 临时规划出来的步骤，而是系统侧由工程师预先定义、验证、可执行、可恢复、可审计的任务流程图（Executable Graph）。其物理载体可能是 YAML、JSON DAG、状态机、BPMN、DSL 或编排代码（详见§4.3.2），但本质都是"确定性可执行图"。Workflow 主导是 LangGraph、Temporal、Prefect 等通用编排框架在 LLM 应用领域兴起后**本文主张推广到 SCADA Agent 的设计范式**，而不是已被工业界普遍采用的既定事实。

**LLM 在 Workflow 中的角色：入口决策器**

LLM 并不"拥有"或"生成" Workflow，它只是 Workflow 的入口决策器（Entry Selector）：

- LLM 看到的只是 Workflow 的入口 Tool 描述（如 `generate_scada_screen`）
- LLM 并不知道该 Tool 内部包含多少步骤、走哪条状态路径
- 一旦 LLM 调用了该 Tool，控制权立即移交给 Workflow Engine
- Workflow 推进过程中，会在特定节点反向回调 LLM 做局部决策（如"选横向布局还是纵向布局"、"哪些变量需要报警"）

由此澄清一个易混淆的问题：LLM 不感知 Workflow 的内部结构，**绝不承担"走哪条路、按什么顺序"这类控制流决策**——这些是 Workflow Engine 的职责。LLM 只在 Workflow Engine 在指定节点反向调度时做受限的**局部决策**（如布局风格、报警阈值选择、参数补全）。Agent 作为整体系统才是 Workflow 的拥有者与执行主体；Workflow 的"接管"对 LLM 透明，但对 Agent Runtime 而言是显式的、受控的、可观测的。

**Workflow Tool 与原子 Tool 的根本区别**

理解 Workflow 必须先理解：在 LLM 面前暴露的 Workflow 入口 Tool 与普通原子 Tool 虽然形式相同，本质完全不同：

| 维度       | 原子 Tool（如 `create_rect`） | Workflow Tool（如 `generate_scada_screen`） |
| -------- | ------------------------ | ---------------------------------------- |
| 调用语义     | 立即执行单一动作                 | 触发一个长期运行的任务流                             |
| 执行时长     | 毫秒~秒                     | 秒~小时                                     |
| 状态管理     | 无状态                      | 强状态、可断点续跑                                |
| 失败处理     | 抛错即结束                    | Retry / Rollback / Checkpoint            |
| LLM 介入次数 | 一次性参数生成                  | 多次回调 LLM 做节点决策                           |
| 本质身份     | API 调用                   | Workflow Trigger（流程触发点）                  |

这一区分让 §4.6.1 调用链中"Domain Tool Dispatcher → Workflow Engine"这一跳不再是黑魔法：Dispatcher 识别该 Tool 属于 L2 Workflow 层，于是把控制权移交给 Workflow Engine，由它接管后续所有阶段推进、Tool 可见性裁剪、上下文生命周期、错误恢复等责任。

> Tool Call 本质上就是 Workflow Trigger。

#### 4.3.2 Workflow 定义形式

主流的 Workflow 定义方式有三种：

**(1) 声明式 YAML/JSON**

```yaml
name: ChemicalProductionScreenWorkflow
version: 1.2.0
steps:
  - id: analyze_process
    type: llm_step
    allowed_tools: [query_chemical_template, query_device_library]
    timeout: 30s
  - id: generate_layout
    type: llm_step
    allowed_tools: [create_canvas, create_flow_layout]
    depends_on: [analyze_process]
  - id: validate_screen
    type: deterministic_step
    handler: validateScreen
    on_failure: rollback_to(generate_layout)
```

- **优点**：可视化、可热加载、非程序员可维护、易做权限管控
- **缺点**：复杂逻辑（循环、嵌套条件）表达力受限

**(2) DSL / SDK（如 Temporal、Prefect、Airflow）**

```python
@workflow.defn
class ChemicalScreenWorkflow:
    @workflow.run
    async def run(self, input: ScreenInput) -> Screen:
        process = await workflow.execute_activity(analyze_process, input)
        layout  = await workflow.execute_activity(generate_layout, process)
        devices = await workflow.execute_activity(create_devices, layout)
        await workflow.execute_activity(bind_points, devices)
        ...
```

- **优点**：表达力强、可调试、可单元测试
- **缺点**：需程序员维护，门槛较高

**(3) 代码即流程（Code-as-Workflow）**

直接用 Python/Go 代码组装。最灵活但最不结构化，不推荐在工业系统中作为主形态。

**(4) 三种形态对比**

| 形态              | 优点                       | 致命缺点                   | SCADA 适用性    |
| --------------- | ------------------------ | ---------------------- | ------------ |
| **纯 YAML/JSON** | 可热加载、版本化友好、领域专家可读、易做权限管控 | 循环/嵌套条件/复杂错误恢复表达力差     | 单独使用不够       |
| **DSL / SDK**   | 表达力强、可调试、可单元测试           | 工艺工程师看不懂、改动需重新部署、与代码耦合 | 适合复杂流程       |
| **纯代码**         | 最灵活                      | 无结构、无审计、无热加载、配置与逻辑混杂   | **不推荐作为主形态** |

**(5) 推荐形态：声明式骨架 + 代码化 Step Handler**

工业 SCADA Agent 的最佳实践是 **混合形态**，而不是非此即彼：

```
Workflow 定义（YAML）        ← 工艺/应用工程师维护，可热加载
    ↓ 引用
Step Handler（代码）         ← 平台工程师维护，编译为二进制
    ↓ 调用
L0/L1 原子 Tool & 领域 Tool
```

**YAML 层**只描述"做什么、什么顺序、什么条件、出错怎么办"：

```yaml
name: ChemicalProductionScreenWorkflow
version: 1.2.0
steps:
  - id: analyze_process
    handler: handlers.analyze_process       # 指向代码 handler
    allowed_tools: [query_chemical_template]
    timeout: 30s
    retry: { max: 3, backoff: exponential }
  - id: generate_layout
    handler: handlers.generate_layout
    depends_on: [analyze_process]
    on_failure: rollback_to(analyze_process)
```

**代码层**实现具体 Step 逻辑（包括 LLM 调用、Tool 执行、补偿动作、数据变换）。

混合形态的优势：

1. **审计与合规**：YAML 是可签名、可 diff、可回放的工艺资产，符合工业控制审计要求
2. **热加载**：新增/修改 Workflow 无需重启 Agent 服务
3. **角色分工**：工艺工程师改流程（YAML），平台工程师改能力（代码 handler），互不干扰
4. **版本化与灰度**：YAML 可挂版本号做 A/B 切换与回滚
5. **可测试性**：每个 handler 可独立单测，整个 YAML 可端到端回归

**一条经验法则**：

> **流程编排（顺序、条件、重试、补偿）放 YAML；业务逻辑（状态判断、Tool 组合、数据变换）放代码。**

纯 YAML 适合 demo 与简单流程，纯代码适合极度定制化场景。**生产级 SCADA Agent 本文推荐混合路线**——这也是 Temporal、Prefect 等编排引擎的设计哲学。

#### 4.3.3 Step 类型

一个成熟的 Workflow Engine 至少支持以下 Step 类型：

| Step类型                 | 说明               | 示例                  |
| ---------------------- | ---------------- | ------------------- |
| **LLM Step**           | 由LLM在限定Tool集中决策  | "选择合适的布局风格"         |
| **Deterministic Step** | 纯代码执行，不调用LLM     | "校验所有点位已绑定"         |
| **Tool Call Step**     | 直接调用指定Tool，不经LLM | `deploy_project()`  |
| **Parallel Step**      | 多分支并发执行          | 同时创建多个设备            |
| **Conditional Step**   | 基于条件分支           | 根据 `industry` 选择子流程 |
| **Loop Step**          | 循环执行             | 为每个点位逐一绑定           |
| **Human-in-the-loop**  | 等待人工确认           | 部署前的Review          |
| **Sub-workflow Step**  | 嵌套调用另一个Workflow  | "创建子设备流程"           |

不同Step类型混合编排，构成完整业务流程。

#### 4.3.4 错误处理与回滚

工业系统必须支持完善的错误处理：

- **重试（Retry）**：网络抖动、临时失败 → 指数退避重试
- **补偿（Compensation / Saga）**：每步定义反向操作，失败时按反顺序补偿
- **检查点（Checkpoint）**：失败后从最近成功点恢复，而非从头开始
- **死信（Dead Letter）**：无法恢复时进入人工介入队列
- **超时（Timeout）**：避免某一步永久挂起
- **幂等（Idempotent）**：同一Step重放结果一致

**Saga 补偿示例**：

```
[创建设备 ✓] → [绑定点位 ✗] → 触发补偿链：
    1. 撤销已部分绑定的点位
    2. 删除已创建的设备
    3. 回滚到 "创建设备前" 状态
    4. 上报失败原因供人工或LLM重新规划
```

#### 4.3.5 本质：长链推理 → 确定性编排

Workflow 的本质是：

> **把"长链推理"转移到"确定性编排"**，让LLM只在单步内做局部决策。

| 角色           | 负责                            |
| ------------ | ----------------------------- |
| **LLM**      | 高层意图理解、单步内的局部决策、参数生成、异常情境下的应变 |
| **Workflow** | 步骤顺序、状态一致性、错误恢复、事务边界、可观测性     |

这种分工的根本意义是：**把不确定性从"全链路"压缩到"单步内"**，每一步只允许LLM在极小范围内自由发挥。

虽然在 2026 年的旗舰级推理型模型加持下，LLM 自身在 20~50 步的规划任务上的表现已显著改善，但是本文认为当前工业Agent系统仍然需要坚持 Workflow 编排，因为Workflow具有：**可恢复性、可审计性、可重放性、可灰度发布、可单元测试**——这些是工程化属性，而不仅是 LLM 能力的补丁。LLM 能力越强，反而越值得用编排把它的能力**可靠地复用**，而不是每次依赖临场推理。

#### 4.3.6 为什么在 SCADA 特别重要

SCADA 操作具有强状态依赖：

- 必须先创建点位才能绑定
- 必须先创建图元才能配置动画
- 必须先配置报警才能测试报警
- 必须先校验项目才能部署
- 部署后才能切换运行模式

这些依赖如果交给LLM自己推理，在 10+ 步的长链中极其容易出错。Workflow 把这些依赖"硬编码"为正确顺序，从根本上消除该类错误。

#### 4.3.7 Workflow vs 单纯 Prompt 链

一个常见误区是把 Workflow 等同于"多次 LLM 调用"。两者本质不同：

| 维度    | 多次LLM调用（Chain） | Workflow |
| ----- | -------------- | -------- |
| 顺序控制  | LLM自己推断        | 引擎强制     |
| 状态持久化 | 依赖上下文窗口        | 持久化存储    |
| 失败恢复  | 重新对话           | 检查点恢复    |
| 可观测性  | 日志难追踪          | 每步可追溯    |
| 可测试性  | 难单元测试          | 每步可独立测试  |
| 长任务   | 上下文爆炸          | 跨天跨周仍可继续 |

#### 4.3.8 Workflow 的设计原则

- **单一职责**：每个 Workflow 解决一个明确业务问题
- **可组合**：Workflow 可嵌套调用其他 Workflow
- **幂等性**：同一输入多次执行结果一致
- **版本化**：每个 Workflow 有版本号，便于灰度发布与回滚
- **可观测**：每步记录输入、输出、耗时、状态
- **可中断**：随时支持暂停、恢复、终止

---

### 4.4 状态机（State Machine）

#### 4.4.1 核心思想

状态机定义"系统当前处于哪个阶段"，并据此**裁剪当前阶段合法的Tool集合**。不同阶段暴露不同Tool。

例如当前正在"创建页面"阶段，则只暴露：

```
create_widget
bind_point
set_layout
```

而 `create_alarm`、`deploy_project` 等其他阶段的Tool**对LLM完全不可见**——不是靠Prompt提示"请不要使用"，而是物理上不出现在Tool列表中。

#### 4.4.2 状态定义

一个 SCADA Agent 的典型状态集如下：

```
IDLE
  ↓
ANALYZE_INTENT          ← 理解用户意图
  ↓
ANALYZE_PROCESS         ← 工艺分析
  ↓
GENERATE_LAYOUT         ← 布局生成
  ↓
CREATE_DEVICES ←─┐
  ↓              │
BIND_POINTS ─────┘      ← 失败回退到 CREATE_DEVICES 重建
  ↓
CONFIG_ANIMATION
  ↓
CONFIG_ALARM
  ↓
GENERATE_TREND
  ↓
VALIDATE                ← 全局校验
  ↓
DEPLOY
  ↓
DONE
```

每个状态在代码中关联以下属性：

```python
class State:
    name: str
    allowed_tools: List[str]             # 当前可见Tool（白名单）
    allowed_transitions: List[str]       # 可转移到的下一状态
    entry_action: Optional[Callable]     # 进入该状态时执行
    exit_action:  Optional[Callable]     # 退出该状态时执行
    timeout_seconds: Optional[int]       # 超时回退
    invariants: List[Callable]           # 不变量校验（如"所有点位必须有Tag"）
```

#### 4.4.3 状态转移规则

转移触发条件：

- **显式触发**：Workflow Engine 调用 `transit_to(next_state)`
- **隐式触发**：当前状态的目标完成（例如所有点位已绑定）
- **失败触发**：错误时回退到安全状态
- **超时触发**：长时间无进展，进入人工介入

转移必须**严格受控**：

- 不允许跨级跳跃（例如从 `CREATE_DEVICES` 直接跳到 `DEPLOY`）
- 每次转移必须通过 `allowed_transitions` 白名单校验
- 转移前后必须通过该状态的不变量校验

```python
def transit_to(self, next_state: str):
    if next_state not in self.current.allowed_transitions:
        raise IllegalTransition(self.current.name, next_state)
    self._check_invariants(self.current)        # 离开校验
    self.current.exit_action()
    self.current = self.states[next_state]
    self.current.entry_action()
    self._check_invariants(self.current)        # 进入校验
```

#### 4.4.4 状态持久化与恢复

工业 Agent 的会话可能持续数十分钟到数小时，必须支持：

- **持久化**：状态序列化到数据库（PostgreSQL / Redis / etcd）
- **恢复**：服务重启后从持久化层加载，无缝继续
- **快照（Snapshot）**：关键状态的完整快照，支持回滚到任意检查点
- **版本演进**：状态机定义升级时，对在途会话做兼容迁移

例如 `CONFIG_ALARM` 阶段崩溃后，重启时应从 `BIND_POINTS` 完成态恢复，而不是从头开始。

#### 4.4.5 为什么有效：三重保障

状态机的有效性来自三重保障：

1. **目标明确**：Agent 当前上下文目标单一，LLM 无需"理解全局"，只需聚焦"当前应该做什么"
2. **决策空间收缩**：可选Tool 从 500 个变为 5~10 个，错误率断崖式下降
3. **物理隔离**：从根本上杜绝越权调用——不可见的Tool 无法被调用

**这是减少越权 Tool 调用最强的方法之一**，因为它不是靠 Prompt 提示 LLM"不要做X"（LLM 经常忽视），而是让 X **在物理上不存在**。

> 注：本文刻意避免把这一效果称为"消除 hallucination"。"hallucination"在 LLM 语境下专指**模型凭空捏造不存在的事实/Tool/字段**，而状态机消除的是**模型在所有真实 Tool 中越权选择**——两者机制不同，状态机对前者只有间接抑制作用（让模型看不到错误的 Tool 也就无从模仿/编造其变体）。

#### 4.4.6 层级状态机（Hierarchical State Machine, HSM）

复杂系统可使用层级状态机（受 UML Statechart 启发）：

```
SCREEN_GENERATION (父状态)
  │  ├─ 全局可用Tool: save_progress, abort
  │
  ├── LAYOUT (子状态)
  │     └─ 局部Tool: create_canvas, set_grid
  │
  ├── DEVICES (子状态)
  │     ├── CREATE
  │     │    └─ Tool: create_reactor, create_pump
  │     └── CONFIGURE
  │          └─ Tool: set_size, set_position
  │
  └── BINDING (子状态)
        └─ Tool: bind_tag, batch_bind
```

子状态继承父状态的全局Tool（如 `save_progress`、`abort`），并叠加自己的局部Tool。这种设计避免了状态爆炸，同时保留了细粒度控制能力。

#### 4.4.7 与 Workflow 的关系

状态机与 Workflow 是**互补正交**的：

- **Workflow**：定义状态转移的**路径**（"先做A再做B"）
- **状态机**：定义每个状态的**约束**（"在状态A时只能用X、Y、Z工具"）

两者结合构成完整的"导航 + 围栏"系统：

```
Workflow（导航）：告诉Agent要走哪条路
State Machine（围栏）：限制Agent在每一段只能做什么
```

没有 Workflow，状态机会"原地踏步"；没有状态机，Workflow 的步骤会被 LLM "钻空子"调用不该调用的Tool。

#### 4.4.8 状态机的失败模式

- **状态爆炸**：状态数失控，建议用层级状态机收纳
- **死锁**：缺少超时或回退路径，会卡在某状态
- **不变量过严**：合法操作被错杀，影响可用性
- **持久化竞争**：多个 Agent 实例并发修改同一会话状态，需要乐观锁/版本号

---

### 4.5 资源-工具分离（MCP Resources vs Tools）

#### 4.5.1 MCP 的主要原语

MCP（Model Context Protocol）规范（2025-11-25 最新稳定版）实际定义了**六个**核心原语，分布在服务端与客户端两侧。完整列表如下：

**服务端原语（Server → Host/LLM）**

| 原语            | 用途            | 副作用 | 是否参与 Tool Selection | 触发方              |
| ------------- | ------------- | --- | ------------------- | ---------------- |
| **Tools**     | 执行动作          | 有   | 是                   | LLM 主动调用         |
| **Resources** | 提供只读上下文       | 无   | 否                   | Host 按 URI 拉取或订阅 |
| **Prompts**   | 预定义 Prompt 模板 | 无   | 否                   | 用户/Host 选择       |

**客户端原语（Client → Server，反向能力）**

| 原语              | 用途                                                          | 关键意义                                                         |
| --------------- | ----------------------------------------------------------- | ------------------------------------------------------------ |
| **Sampling**    | 服务端反向请求客户端代为发起 LLM 推理（`createMessage`），客户端转发给宿主模型并返回结果      | Agent 内嵌 LLM 推理而无需服务端持有 API key，是构建"嵌套 Agent / 子 Agent"的核心机制 |
| **Roots**       | 客户端告诉服务端"我希望你聚焦在这些工作区/资源根"，是作用域信息（信息性、非强制）                  | 类似 IDE 的 workspace 概念，定义可见范围而非权限边界                           |
| **Elicitation** | 服务端运行中发现需要更多上下文（如 `{{current_user}}`），通过结构化 schema 向客户端发起询问 | 把"运行中补全缺失上下文"标准化，2026 年新增 URL-mode 适配复杂交互                    |

很多团队的常见错误是**把所有能力都做成 Tool**，导致 Tool 列表急剧膨胀。

**Sampling 对 Agent 设计的关键意义**

在 SCADA Agent 场景下，Sampling 直接支持以下两种关键模式：

1. **子 Agent / 工具内推理**：某个 MCP Tool（如"自动诊断报警风暴"）内部需要 LLM 做语义分析，但服务端不想各自维护 LLM 凭证。它通过 Sampling 向 Host 借用模型推理能力，Host 统一管控成本、模型选择与审计。
2. **Workflow 节点回调 LLM**：Workflow Engine 在节点上需要 LLM 做局部决策时，可通过 Sampling 在 MCP 协议层完成回调，而不必让 Workflow 直接持有模型客户端——这与 §4.3.1 中"Workflow 在节点上回调 LLM 做局部决策"的范式天然契合。

**Elicitation 的安全意义**

Elicitation 让服务端能在运行中合法地"暂停并要求补充信息"，而不必让 LLM 凭空编造缺失字段。对工业 Agent 而言，这是把"参数补全"从概率事件转变为协议事件的关键机制——尤其在写操作前的"二次确认"场景。

#### 4.5.2 错误做法（Tool 列表污染）

```
Tools:
  - query_page              ← 只读
  - query_point             ← 只读
  - query_alarm             ← 只读
  - read_history            ← 只读
  - list_devices            ← 只读
  - get_user_permission     ← 只读
  - get_project_metadata    ← 只读
  ...
  - create_page             ← 真正的"动作"
  - bind_point              ← 真正的"动作"
  - deploy_project          ← 真正的"动作"
```

在这种设计中，**只读查询占据了 Tool 列表的大部分席位**，严重稀释 LLM 对真正动作 Tool 的注意力。在 SCADA 这种"读多写少"的场景下，比例往往是 7:3 甚至 8:2，读Tool 数量是写Tool 的数倍。

#### 4.5.3 正确做法（读写分离）

```
Resources (只读):
  - scada://pages                          列出所有页面
  - scada://pages/{page_id}                单个页面详情
  - scada://pages/{page_id}/widgets        页面下的所有图元
  - scada://points                         列出所有点位
  - scada://points?filter=temperature      筛选点位
  - scada://alarms                         列出所有报警
  - scada://history/{tag}?from=...&to=...  时序数据
  - scada://devices                        设备清单
  - scada://permissions/{user}             权限查询

Tools (有副作用):
  - manage_pages       (含 create/delete/rename 等 action)
  - manage_points      (含 create/bind/import 等 action)
  - manage_alarms      (含 create/bind/test 等 action)
  - manage_graphics    (含 create_rect/set_animation 等 action)
  - deploy_project
```

#### 4.5.4 为什么这种分离重要

| 维度                     | 影响                                               |
| ---------------------- | ------------------------------------------------ |
| **Tool Selection 准确率** | Resource 不出现在Tool列表，LLM 只在真正"动作Tool"中选择          |
| **Token 成本**           | Resource 定义不占用 Tool Schema 的 token               |
| **缓存友好**               | Resource 结果可按 URI 强缓存，避免重复查询                     |
| **订阅推送**               | MCP 支持 Resource Subscribe（可选能力），状态变化时主动推送给 Agent |
| **权限分离**               | 只读权限与写权限可独立管控（读 RW，写 RBAC）                       |
| **审计简化**               | 写操作=Tool调用=审计点；读操作不需审计                           |

#### 4.5.5 Resource URI 设计原则

资源 URI 应当**可枚举、可参数化、可订阅、自描述**：

```
scada://pages                       列表（可分页）
scada://pages/{page_id}             单个页面详情
scada://pages/{page_id}/widgets     嵌套子资源
scada://points?filter=temperature   查询参数
scada://history/{tag}?from=...      时序参数化
scada://search?q=锅炉                全文搜索
```

LLM 读取 Resource 时不需要"决策"，只需要"声明需要什么"——这是声明式 vs 命令式的关键差异。

#### 4.5.6 Resource 与 Tool 的协同模式

典型的协同模式：

```
1. LLM 通过 Resource 读取当前状态：
     GET scada://pages/page_001
   → { widgets: [...], bindings: [...] }

2. LLM 基于状态推理出动作：
     "需要为 widget_X 绑定 TEMP_101 点位"

3. LLM 调用 Tool 执行：
     manage_points { action: "bind", widget: "widget_X", tag: "TEMP_101" }

4. 系统通过 Resource Subscribe 推送变更：
     SUB scada://pages/page_001
   → 状态更新事件
```

这种"先读 Resource、再写 Tool"的模式，把 LLM 的认知任务划分得非常清晰：

- **理解**：通过 Resource 获取信息
- **决策**：基于信息做规划
- **执行**：调用 Tool 改变状态

#### 4.5.7 实际收益（量化）

把只读能力从 Tool 迁移到 Resource 后：

- **Tool 数量减少 30%~60%**（与具体业务相关）
- **Tool Selection 准确率提升明显**（可补充实测数据）
- **Token 成本下降**（Tool 定义不再占用 system prompt）
- **响应延迟降低**（Resource 可缓存命中）

#### 4.5.8 何时该用 Tool 而非 Resource？

边界并非总是清晰，判断原则：

| 特征            | 选择       |
| ------------- | -------- |
| 改变系统状态        | Tool     |
| 仅返回数据，幂等      | Resource |
| 有副作用但小（如打点日志） | Tool     |
| 计算密集但只读       | Resource |
| 需要用户输入授权      | Tool     |
| 需要订阅推送        | Resource |

---

### 4.6 工业级整合架构（Integrated Architecture）

#### 4.6.1 完整调用链

将前述五大策略整合，工业级 SCADA Agent 的完整调用链如下：

```
                  ┌─────────────────────────┐
                  │     User Query          │
                  └──────────┬──────────────┘
                             ↓
                  ┌─────────────────────────┐
                  │  Planner / Intent       │
                  │   Classifier            │
                  └──────────┬──────────────┘
                             ↓
                  ┌─────────────────────────┐
                  │  Session Context        │  ← 历史对话、用户角色
                  └──────────┬──────────────┘
                             ↓
              ┌──────────────────────────────┐
              │   State Machine              │  ← 硬过滤：当前状态白名单
              └──────────┬───────────────────┘
                         ↓
              ┌──────────────────────────────┐
              │   Tool RAG                   │  ← 软排序：Hybrid 检索 + Re-rank
              │   (Hybrid + Re-rank)         │
              └──────────┬───────────────────┘
                         ↓
              ┌──────────────────────────────┐
              │   Prompt Assembly            │  ← 拼装上下文
              │   (Tools + Resources URIs +  │
              │    History + State + Hints)  │
              └──────────┬───────────────────┘
                         ↓
                        LLM
                         ↓
              ┌──────────────────────────────┐
              │   Domain Tool Dispatcher     │  ← Façade 二级分发
              └──────────┬───────────────────┘
                         ↓
              ┌──────────────────────────────┐
              │   Workflow Engine            │  ← 顺序、事务、补偿
              └──────────┬───────────────────┘
                         ↓
              ┌──────────────────────────────┐
              │   Atomic Executor            │  ← 真正的原子操作
              └──────────┬───────────────────┘
                         ↓
              ┌──────────────────────────────┐
              │   SCADA Runtime              │
              └──────────────────────────────┘
                         ↑
                         │
              ┌──────────────────────────────┐
              │   Observability / Audit      │  ← 全链路埋点
              └──────────────────────────────┘
```

每一层都承担明确职责，且**层与层之间通过接口契约通信**，可独立演进。

#### 4.6.2 四层分层模型

| 层级                | 内容                                                                               | LLM 可见性 | 维护者    | 变更频率       |
| ----------------- | -------------------------------------------------------------------------------- | ------- | ------ | ---------- |
| **L0：原子层**        | `create_point`、`bind_point`、`set_color`、`set_animation`、`create_rect`            | 不可见     | 平台工程师  | 低（API级别）   |
| **L1：领域层**        | `manage_alarm`、`manage_page`、`manage_graphics`、`manage_points`                   | 通常可见    | 平台工程师  | 中（业务模块级）   |
| **L2：Workflow 层** | `create_pump_station_screen`、`migrate_legacy_project`、`generate_chemical_screen` | 优先可见    | 应用工程师  | 高（按业务需求迭代） |
| **L3：Planner 层**  | 多Agent协作、跨Workflow编排、长任务调度                                                       | 顶层入口    | AI 工程师 | 中（架构级）     |

**层间调用规则**：

- 上层可调用下层（L3 → L2 → L1 → L0）
- **下层不得反向调用上层**，避免环依赖
- 同层之间尽量解耦，必要时通过事件总线异步通信

**与垂类 Harness 的对应**：这四层分布正是 §1.3 所述"垂类 Harness 把领域知识预先内化"的具体形态——通用 Harness（如 Claude Code、LangGraph）通常只暴露 L0/L1 两层（原子能力 + 编排接口），把 L2 Workflow 与 L3 Planner 完全留给用户在 Prompt/配置中临场组装；工业垂类 Harness 则把 L2 中的工艺流程（如 `generate_chemical_screen`）与 L3 中的跨流程调度作为**产品的一等公民固化下来**，让 LLM 优先从 L2/L3 入口进入，再由系统决定是否下沉到 L1/L0。这正是"把任务结构从 LLM 的自由推理中剥离到 Runtime"的物理实现。

#### 4.6.3 横切关注点（Cross-cutting Concerns）

成熟工业 Agent 还必须处理以下横切关注点：

**(1) 可观测性（Observability）**

- 每次 Tool 调用、状态转移、LLM 调用都需记录
- 三大支柱：Logs（日志）、Metrics（指标）、Traces（链路追踪）
- 工业系统应能回放任意一次 Agent 会话以排查问题

**(2) 版本化（Versioning）**

- Tool 接口、Workflow 定义、状态机、Prompt 模板都需版本号
- 灰度发布机制：新版本先在少量会话上验证，再全量推广
- 兼容策略：旧版本Agent 会话应能继续运行直到完成

**(3) 热加载（Hot Reload）**

- 新增 Tool / Workflow 不应需要重启服务
- 通过中心化的 Tool Registry 实现动态注册

**(4) 审计（Audit）**

- 所有"有副作用"的 Tool 调用必须可审计
- 审计日志包括：操作者（人/Agent）、时间、参数、结果、影响范围
- 工业控制场景下，审计是合规性硬性要求

**(5) 权限（Authorization）**

- 基于 RBAC 的 Tool 访问控制
- LLM 的"身份"绑定到当前用户，越权调用直接拒绝
- 敏感操作（如部署）需要二次确认

**(6) 限流与熔断（Rate Limiting & Circuit Breaker）**

- 避免 LLM 误调用造成系统过载（如循环创建10万个图元）
- 单会话 Tool 调用频率上限
- 失败率超阈值自动熔断

**(7) 回归测试（Regression Testing）**

- 每次 Tool / Workflow 变更必须通过回归套件
- 用真实历史会话作为测试用例
- 用 LLM-as-Judge 做自动化评估

**(8) 多租户隔离（Multi-tenancy）**

- 不同企业/项目的 Agent 会话相互隔离
- Tool / Workflow / 状态机的实例化按租户隔离

#### 4.6.4 部署形态

工业 SCADA Agent 通常以以下形态部署：

| 部署形态                 | 适用场景               | 关键考虑                  |
| -------------------- | ------------------ | --------------------- |
| **嵌入式（与 SCADA 同进程）** | 单机组态工具、离线场景        | 资源受限，需轻量 LLM          |
| **本地服务（同局域网）**       | 工厂内网，数据不出厂         | 私有化 LLM、与 OPC UA 网关集成 |
| **云端服务（SaaS）**       | 多租户、低运维成本          | 数据合规、跨境传输限制           |
| **混合云（边-云协同）**       | 大型企业，边缘做实时控制、云端做规划 | 边-云一致性、断网容灾           |

#### 4.6.5 端到端示例：完整请求生命周期

以"为反应釜配置高温报警"为例（**下列时间是 2026 年云端旗舰模型 + 工业部署的现实基线，不是理论最优**）：

```
T+0ms      User: "给反应釜1加个高温报警，超过80度告警"
T+5ms      Intent Classifier → "alarm configuration"
T+10ms     State Machine → 当前 STATE_CONFIG_ALARM, 允许Tools: [manage_alarms]
T+50ms     Tool RAG → 召回[manage_alarms 内 create_analog_alarm 等action示例]
T+80ms     Prompt Assembly → 拼装上下文 + Resource: scada://devices/reactor_1
T+80ms~T+1500ms  LLM 推理（含网络往返 + 首 token 延迟 + 流式输出）→ 输出 manage_alarms 调用
T+1505ms   Dispatcher → 路由到 create_analog_alarm
T+1510ms   Workflow Engine → 验证依赖（点位TEMP_REACTOR1已存在）
T+1520ms   Atomic Executor → 写入报警配置
T+1530ms   SCADA Runtime → 持久化、订阅推送
T+1535ms   Audit Log → 记录此次配置
T+1550ms   Response → "已为反应釜1配置高温报警，阈值80°C"
```

整个流程中 LLM 只看到 1~2 个相关 Tool，没有出错空间，**端到端响应时间通常落在 1~2 秒**。若使用本地部署的小模型（7B~32B 量级）+ KV-cache 命中 + 本地工业网，理论上可压缩到 200~500ms 量级，但这不是默认基线——把"工业 Agent 必须毫秒级"作为前提进行架构论证是不现实的。

---

### 4.7 功能安全与 LLM 边界

前述五大策略构成的是**软件工程层**的"笼子"——防止 LLM 在能力空间中选错 Tool、走错路径。但工业 SCADA 系统的特殊性在于：它最终会通过 PLC、SIS、执行机构作用到**物理世界**——反应釜会爆、储罐会漏、电网会跳。因此，工业语境下还存在另一层完全独立、且优先级更高的"笼子"：**功能安全（Functional Safety）标准**所定义的物理与认证边界。任何严肃的 SCADA Agent 设计都必须在动笔写第一行代码前明确这一层。

#### 4.7.1 工业安全标准全景

| 标准                     | 范畴            | 关键概念                                                                     |
| ---------------------- | ------------- | ------------------------------------------------------------------------ |
| **IEC 61508**          | 通用功能安全总论      | 电气/电子/可编程电子安全相关系统的设计准则；SIL 等级定义、系统能力（Systematic Capability）、随机失效与系统失效的分离 |
| **IEC 61511**          | 流程工业 SIS 专用   | 基于 IEC 61508 框架的过程工业实现；覆盖石化、化工、医药、纸浆、电力等                                 |
| **IEC 61513**          | 核工业           | 核电控制系统专用                                                                 |
| **IEC 62061**          | 机械与制造业        | 工厂自动化与机械安全                                                               |
| **IEC 62443**          | 工业网络安全        | OT 网络的纵深防御、区域与管道模型                                                       |
| **ISA-95**             | 企业-控制系统集成参考模型 | Level 0~4 五层结构，定义不同层级的职责边界（详见 §4.7.3）                                    |
| **FDA 21 CFR Part 11** | 生命科学行业电子记录与签名 | GMP/GxP 验证要求                                                             |

**Safety Integrity Level（SIL）** 是这套标准的核心度量。SIL 分为 1~4 级，4 级最严苛，对应每小时危险失效概率（PFH）从 10⁻⁵ 到 10⁻⁹ 的范围。SIL 由三方面共同决定：

1. **Systematic Capability（系统能力）**：开发过程的质量保证、验证与确认手段
2. **Architecture Constraints（架构约束）**：硬件冗余度、故障检测覆盖率
3. **Probability of Dangerous Failure（危险失效概率）**：可量化的可靠性指标

#### 4.7.2 LLM 不可触碰的红线

IEC 61508/61511 并未明文"禁止 AI"，但其**四项硬性要求**使 LLM 在当前形态下**几乎不可能**通过 SIL 认证：

1. **确定性行为**：同一输入必须产生同一输出。LLM 的采样本质与之根本冲突。
2. **可穷尽的验证**：高 SIL 等级（SIL 3/4）要求 MC/DC（Modified Condition/Decision Coverage）级别的代码覆盖；神经网络没有传统意义上的"分支"，无法满足。
3. **可解释性**：每一次决策必须可被人类追溯。LLM 内部权重不具备此性质。
4. **系统能力可证明**：开发过程、训练数据、模型迭代必须有完整的可审计记录。LLM 的训练语料几乎不可能满足。

由此推出**绝对红线**——LLM 永远不得直接进入以下回路：

- **SIS（Safety Instrumented System）安全仪表系统**：紧急停车、压力释放、火灾报警联锁
- **联锁逻辑（Interlock）**：必须由通过认证的 PLC 安全功能块实现
- **SIL 认证回路**：任何被 HAZOP/LOPA 分析认定为"风险消减层"的功能
- **硬实时控制环**：毫秒级 PID、电网保护、运动控制——LLM 推理延迟（即便本地 7B 模型也在 100ms 量级）与之物理上不兼容
- **OPC UA Real-time / 时间敏感网络（TSN）下行通道**：LLM 不能作为下行控制指令的来源

这条红线的本质不是"LLM 还不够好"，而是**功能安全的认证体系建立在不同的本体论假设上**——可证明的确定性 vs 经验性的概率正确。两者无法在同一回路中互替。

#### 4.7.3 LLM 在 ISA-95 层级中的合法介入域

ISA-95 把企业-控制系统划分为五层：

| Level | 名称                 | 时间尺度 | LLM 可否介入               |
| ----- | ------------------ | ---- | ---------------------- |
| **0** | 物理过程               | 连续   | **绝对不可**               |
| **1** | 现场设备 / 传感执行        | 毫秒~秒 | **绝对不可**               |
| **2** | 监控与控制（PLC/DCS/SIS） | 毫秒~秒 | **绝对不可**（控制回路）         |
| **3** | 制造执行（MES）          | 秒~小时 | 部分可（生产调度建议、配方推荐，需人工确认） |
| **4** | 企业资源（ERP）          | 小时~天 | 可（报表、KPI 解读、计划草案）      |

**SCADA 软件本身横跨 Level 2~3**：其"运行监视"部分属于 Level 2（不可介入控制回路），"组态/配置/历史趋势/报警管理"部分属于 Level 2.5~3（可介入）。

**本文为现阶段 SCADA Agent 划定的核心准则（按重要性递减）**：

1. **主要活跃于组态期（Design-time）**——界面生成、点位规划、报警策略草案、趋势图配置、设备模板搭建。这是 LLM 当前能力与认证体系下**唯一真正"主战场"**，也是本文 §5 案例的合法范围。组态期产物在交付运行前必须经过人工评审、组态校验、模拟运行三道关，因此 LLM 即便偶尔出错也不会直接作用于物理过程。
2. **极少工作于运行态（Operations-time）**——只在确有需要时介入：异常解释、报警风暴诊断、历史数据问答、操作建议草拟、事后复盘报告。**频率应被刻意压低**，避免 Agent 演化为运行态的"驾驶员"。
3. **运行态下 LLM 绝对只读，永远不写**——这是不可妥协的红线。即便加上"人工二次确认"也不允许；二次确认是工程实现层的最后兜底，不是放宽红线的理由。运行态需要写入的任何动作（开关阀、改设定值、复位报警、切换运行模式、下发参数）都必须由**人类操作员通过 SCADA 原生 HMI 直接发起**，Agent 不得作为中介、不得"建议+自动执行"、不得"代填表单等待点击"。
4. **绝对不进入控制期（Control-time）/ SIS / 联锁 / 实时回路**——同 §4.7.2 红线。

这套准则的逻辑是：组态期错了可以撤、可以重做、可以不部署；**运行态写错了，物理世界不可逆**。LLM 当前形态下的概率性输出与运行态写权限是**根本不相容**的两件事——不是"加一层确认就能用"，而是"在认证体系演进出针对 LLM 的 SIL 等价机制之前，这两者必须物理隔离"。

在 §5 的化工厂监控界面案例中，所有 Tool（`create_alarm`、`bind_point`、`create_trend_chart`）都是"组态期"操作——它们改变的是**组态库与显示**，而**不直接改变正在运行的控制逻辑**。这条边界是 SCADA Agent 安全设计的第一原则。

#### 4.7.4 OPC UA / Modbus / IEC 61850 通信边界

工业通信协议本身就**强制**定义了 Agent 的物理边界，必须按职能严格切分通道：

- **北向只读通道（North-bound Read-only）**：Agent ← SCADA Runtime，通过 OPC UA Client（订阅 + 按需读）、Historian REST API、Modbus 只读功能码读取实时与历史数据。**这是 Agent 在运行态唯一允许的接入方式**。
- **北向写通道（North-bound Write）**：**Agent 在运行态完全不接入**。运行态下任何写操作（变量赋值、设定值变更、命令下发）必须由人类操作员通过 SCADA 原生 HMI 发起；Agent 既不作为"代理写入者"，也不作为"自动审批者"，更不作为"自动重试者"。运行态写权限**在系统级（RBAC + 网络分段）就应被剥夺**，而非依赖应用层的"审计 + 二次确认"做软兜底。
- **组态写通道（Design-time Write）**：Agent 仅在组态期作用于**组态数据库 / 工程文件 / 离线项目**——它写的是图纸、点位定义、报警阈值草案、Tag 字典，而非运行中的活变量。组态成果必须经"组态校验 → 人工评审 → 部署网关"三道闸口才能进入运行环境，**这条路径上 Agent 永远不接触正在运行的 Runtime**。
- **南向通道（South-bound）**：PLC ↔ 现场设备，由 IEC 61131-3 程序、IEC 61850 GOOSE/SMV 消息承担硬实时；**Agent 完全不参与南向通道**——无论组态期还是运行态。
- **Pub/Sub 通道（OPC UA PubSub、MQTT Sparkplug B）**：Agent 可作为 Subscriber 订阅遥测数据用于分析，但**不得作为 Publisher 发出任何控制 topic**，无论是直接控制指令还是"建议指令"。

| 通道                 | 组态期 | 运行态          |
| ------------------ | --- | ------------ |
| 北向读                | ✅   | ✅（且仅此一项）     |
| 北向写到组态库 / 工程文件     | ✅   | —（运行态不存在该动作） |
| 北向写到 Runtime 活变量   | ❌   | ❌（不可妥协的红线）   |
| 南向 PLC / IEC 61850 | ❌   | ❌            |
| Pub/Sub 订阅         | ✅   | ✅            |
| Pub/Sub 发布控制 topic | ❌   | ❌            |

简而言之：**Agent 在组态期是"读 + 写组态"，在运行态退化为"纯只读观察者"**。这与 §4.5 中 Resource/Tool 读写分离的设计哲学在物理层得到了对应，但更进一步——**运行态下，连"受控 Tool 的北向写"也被彻底取消**，只保留 Resource 式只读。把"读多写少"升级为"运行态零写"，是本文相对早期描述更严格的工程准则。

#### 4.7.5 合规、审计与可追溯性

在生命科学、电力、油气等强监管行业，Agent 还须满足：

- **FDA 21 CFR Part 11**（生命科学电子记录）：所有"由 Agent 提出的变更"必须有完整的"who / what / when / why / before / after"五元组审计链
- **GMP / GxP 验证**：Agent 的每一次升级（包括模型版本、Tool 集合、Workflow 定义、Prompt 模板）必须经过 IQ/OQ/PQ 验证
- **不变量审计**：所有有副作用的 Tool 调用必须可重放——这与 §4.6.3 横切关注点中的"审计"要求在合规层得到再次确认
- **模型版本绑定**：审计记录中必须固化"当时使用的模型版本与 Prompt 模板版本"，因为同一 Prompt 在不同模型代次下的行为可能截然不同

合规层与 §4.4 状态机、§4.3 Workflow 在工程实现上有天然契合——状态机的转移、Workflow 的步骤天然就是审计点。把这套合规需求**作为状态机/Workflow 设计的一等公民**而非"事后补丁"，是工业 Agent 落地最关键的工程决策之一。

#### 4.7.6 两层笼子：纵深防御

至此可以清楚地看到，工业 SCADA Agent 的"笼子"实际有两层：

| 笼子层           | 防御目标                      | 实现机制                                                        | 失效后果             |
| ------------- | ------------------------- | ----------------------------------------------------------- | ---------------- |
| **软件工程层（内层）** | 防止 LLM **选错** Tool / 走错路径 | §4.1~§4.6：分层 Tool、Tool RAG、Workflow、状态机、MCP                 | 用户体验差、产生废组态、配置出错 |
| **工业安全层（外层）** | 防止 LLM **做坏**——伤人、毁设备、违法  | §4.7：ISA-95 分层、SIS 物理隔离、SIL 认证边界、**运行态零写、北向写权限在系统级剥夺**、合规审计 | 安全事故、设备损坏、法律责任   |

两层笼子**互为冗余、不可相互替代**：

- 仅有内层笼子：LLM 选对了 Tool，但 Tool 本身被错误地暴露给了控制回路 → 仍可能酿成事故
- 仅有外层笼子：物理上无害，但 LLM 频繁选错 Tool，产品不可用

这也呼应了工业控制工程几十年来的核心智慧——**纵深防御（Defense in Depth）**：BPCS（基本过程控制系统）失效有 SIS 兜底，SIS 失效有机械保护层兜底，机械层失效有应急响应兜底。LLM Agent 不是这条链中的新增防御层，而是叠加在 BPCS 之上的"组态与监视增强层"，其失效绝不应造成对下层防御能力的削弱。

> **把"AI 关进笼子里"在工业语境下的完整含义是：用软件工程笼子约束 LLM 选什么 Tool，用功能安全笼子隔离 LLM 永远碰不到什么——前者管"对不对"，后者管"敢不敢"。而现阶段最具操作性的一条边界，可以浓缩为一句话：**
> 
> **"LLM 主要活跃于组态期，极少介入运行态；即便介入运行态，也牢牢守住只读不写的红线——运行态写权限不是用审计与确认去管，而是从系统层就不给。"**

---

## 5. 案例分析：生成化工厂生产监控界面

### 5.1 场景设定

- **用户输入**："生成一个化工厂生产监控界面"
- **系统能力**：500+原子Tool
- **采用架构**：Tool RAG + 分层Tool + Workflow + 状态机 + MCP

### 5.2 整体调用链

```
User
  ↓
Planner Agent
  ↓
Tool RAG
  ↓
高层Workflow Tool
  ↓
Workflow Engine
  ↓
State Machine
  ↓
Domain Tool
  ↓
Atomic Tool Executor
  ↓
SCADA Runtime
```

### 5.3 详细执行过程

#### 5.3.1 第一轮：高层意图识别

**LLM初始可见Tool（仅3~10个）：**

```json
[
  { "name": "generate_scada_screen", "description": "Generate industrial SCADA screen" },
  { "name": "manage_alarm",          "description": "Manage alarm strategy" },
  { "name": "deploy_project",        "description": "Deploy SCADA project" }
]
```

**LLM调用：**

```json
{
  "tool": "generate_scada_screen",
  "arguments": { "industry": "chemical", "screen_type": "production_monitor" }
}
```

此时LLM根本看不到`create_rect`、`create_pipe`、`create_tag`等原子Tool，因此不会陷入"低层API选择困境"。

#### 5.3.2 进入Workflow层

系统启动预定义的 `ChemicalProductionScreenWorkflow`：

```yaml
steps:
  - analyze_process
  - generate_layout
  - create_devices
  - bind_points
  - configure_animation
  - configure_alarm
  - generate_trend
  - validate_screen
```

**Workflow保证顺序正确**——避免LLM"先绑定后创建"等错误。

#### 5.3.3 阶段1：工艺分析（STATE_ANALYZE_PROCESS）

状态机裁剪后，LLM只能看到：

```
query_template
query_device_library
query_industry_knowledge
```

Tool RAG进一步根据当前阶段与Query检索Top-K：

```
query_chemical_template
query_reactor_symbols
query_pump_symbols
query_pipe_templates
```

LLM调用 `query_chemical_template`，系统返回：

```json
{ "devices": ["reactor", "pump", "tank", "heat_exchanger"] }
```

#### 5.3.4 阶段2：布局生成（STATE_GENERATE_LAYOUT）

状态机再次收缩Tool集合至：

```
create_canvas
create_grid_layout
create_flow_layout
```

LLM调用：

```json
{
  "tool": "create_flow_layout",
  "arguments": { "style": "chemical_horizontal" }
}
```

#### 5.3.5 阶段3：设备创建（STATE_CREATE_DEVICES）

此时只暴露领域Tool `manage_graphics`。LLM调用：

```json
{
  "tool": "manage_graphics",
  "arguments": { "action": "create_reactor", "position": [100, 200] }
}
```

**Domain Tool内部Dispatcher（C++）**：

```cpp
if (action == "create_reactor") {
    return createReactor();   // 内部再调用 create_rect / create_circle / bind_style 等
}
```

LLM永远不知道这些原子Tool的存在。

#### 5.3.6 阶段4：点位绑定（STATE_BIND_POINTS）

只暴露：

```
query_tag
bind_tag
batch_bind_tags
```

LLM调用：

```json
{
  "tool": "batch_bind_tags",
  "arguments": {
    "device": "reactor_1",
    "tags": ["TEMP_101", "PRESS_101", "LEVEL_101"]
  }
}
```

#### 5.3.7 阶段5：报警配置（STATE_CONFIG_ALARM）

只暴露：

```
create_analog_alarm
create_digital_alarm
bind_alarm
```

此时LLM**根本不可能误调用** `create_layout`——因为该Tool不可见。

#### 5.3.8 阶段6 & 7：验证与部署

- **STATE_VALIDATE**：系统自动检查未绑定点位、动画、重叠、报警
- **STATE_DEPLOY**：只暴露 `deploy_project`

### 5.4 关键观察

> **整个流程中，系统内部有500+ Tool，但任意时刻LLM真正看到的通常只有5~15个。**

这正是工业Agent设计的核心：

- LLM负责语义
- 系统负责确定性

---

## 6. 理论升华：从概率到确定性

### 6.1 核心哲学

> **工业Agent的本质不是"让LLM自由发挥"，而是"把LLM约束在一个可控、有限、确定的空间里工作"。**

状态机、Workflow、Tool RAG、分层Tool，本质上都在做同一件事：

```
降低自由度（Reduce Degrees of Freedom）
        ↓
提升确定性（Determinism）
```

---

### 6.2 各组件的统一视角：四个正交的"约束层"

四位一体架构的核心洞察是：分层 Tool、Tool RAG、Workflow、状态机本质上都属于同一种东西——"LLM 约束系统（Constraint System）"。它们共同的目标是降低 LLM 的自由度，把开放世界问题逐步收缩成有限确定性问题。它们看起来相似，但约束的维度完全不同——这正是为什么必须四者并存、不能相互替代。

**四个正交的约束维度**

| 组件       | 约束维度 | 回答的问题     | 类比          |
| -------- | ---- | --------- | ----------- |
| 分层 Tool  | 能力空间 | "能做什么"    | API 抽象 / 封装 |
| Tool RAG | 可见空间 | "当前看见什么"  | 动态链接 / 检索   |
| Workflow | 路径空间 | "按什么顺序做"  | 编排 Pipeline |
| 状态机      | 状态空间 | "此刻允许做什么" | Runtime 不变量 |

四者各自管一个维度，像四道阀门，分别从能力、可见性、路径、状态四个方向收缩 LLM 的决策空间。

**从高熵系统到低熵系统的逐步收缩**

```
原始 LLM 面对的问题：              工业 Agent 面对的问题：
┌────────────────────┐           ┌────────────────────┐
│ 500 个 Tool        │           │ 5 个候选 Tool       │
│ 无限步骤组合         │           │ 1 条合法路径         │
│ 任意顺序            │     →     │ 1 个合法状态         │
│ 无限上下文           │          │ 受限上下文窗口        │
│ 【高熵系统】         │           │ 【低熵系统】         │
└────────────────────┘           └────────────────────┘
```

每加入一层约束，系统熵就下降一截。信息熵的减少，正是工程化所追求的"确定性"的代数表达。

**层与层之间的嵌套关系**

四者并非平铺并列，而是相互嵌套、相互组合：

- Workflow 是宏观状态机，状态机是局部 Workflow——两者尺度不同但同构，常常嵌套使用。例如 Workflow 的 `Bind` 阶段内部，又是一个 `QueryTag → MatchTag → Validate → Commit` 的子状态机
- 分层 Tool 嵌入在 Workflow 节点和状态机状态中——每个节点/状态暴露的不是原子 Tool，而是该上下文相关的领域 Tool 子集
- Tool RAG 跨所有维度运作——依据当前 Workflow 位置与状态机状态做硬过滤，再在剩余集合中做语义软排序

可以用一句话形象概括：

> Workflow 决定走哪条路，状态机决定每一段路的围栏，分层 Tool 决定围栏内的能力树形状，Tool RAG 决定能力树上当前发亮的那几片叶子。

**最终形态：近似形式化系统**

四个约束层叠加之后，Agent 整体已经非常接近一个形式化系统——有限 Tool、有限状态、有限路径、有限上下文。这正是工业系统所需"可预测、可复现、可验证、可恢复"四大属性的代数基础。

正因如此，成熟的工业 Agent 与其说是"自由智能体"，不如说是"LLM 驱动的类型化状态工作流系统（Typed State Workflow System driven by LLM）"——LLM 提供语义灵活性，约束系统提供形式化可靠性，二者缺一不可。

---

### 6.3 搜索空间裁剪与编译器架构的类比

LLM原本面临500叉树搜索；状态机裁剪后变成5叉树。**类比的选择需要审慎**：早期版本曾把这种裁剪与"AlphaGo 搜索剪枝""SAT Solver Constraint"并列，但严格来说两者机制不同——AlphaGo 用 MCTS + 价值网络做**采样**而非剪枝，SAT Solver 用 CDCL/单元传播做**逻辑推断**而非可见性裁剪。更贴切的类比有三个：

- **Beam Search 剪枝**：每一步只保留 Top-K 候选，其余路径被物理丢弃——这与 Tool RAG 的语义最接近
- **编译器作用域规则（Lexical Scoping）**：每个作用域内只有特定符号可见，越界访问编译期即报错——这与状态机白名单机制几乎一一对应
- **类型系统（Type System）**：通过类型约束在编译期把非法状态变为不可表达——这与四层约束系统的整体哲学最契合

这与以下经典工程思想高度相似：

- 编译器优化
- 静态类型系统
- Beam Search 剪枝
- 有限自动机（DFA / NFA）

**Agent 与编译器的架构同构**

如果继续追问"为什么这种约束架构如此自然"，会发现成熟 Agent 与现代编译器/运行时的架构几乎一一对应：

| Agent 组件   | 编译器/运行时对应          |
| ---------- | ------------------ |
| LLM        | 高级语言解释器 / 前端语义分析   |
| 分层 Tool    | API 抽象层 / 中间表示 IR  |
| Tool RAG   | 动态链接器 / 符号解析       |
| Workflow   | 编译 Pipeline / 优化阶段 |
| 状态机        | 运行时类型与状态约束         |
| Dispatcher | 函数调用约定 / 调度入口      |

Agent 工程化的演进，与编译器从"自由汇编"走向"严格类型化优化管线"的演进路径惊人相似——一个领域只要追求确定性与可验证性，就必然演化出类似的分层抽象。

**四个约束层都在"做类型系统"**

最深一层看，四者都在为 Agent 引入某种"类型约束"：

- 分层 Tool：约束能力类型（这个上下文只允许"图形管理"类操作）
- Tool RAG：约束可见类型（这个上下文只可见与"温度"相关的工具）
- Workflow：约束步骤类型（这一步必须是"绑定点位"而非"部署项目"）
- 状态机：约束状态类型（当前必须处于 `BindTags` 状态）

这就是为什么工业 Agent 越来越像一个被严格类型化的运行时系统——它在用类型系统驯服概率模型的不确定性。

> 类型系统是确定性工程几十年来最成熟的"约束语言"，Agent 工程化只是把它移植到了 LLM 这个新底座上。

---

### 6.4 自由 Agent 与 Workflow Agent 的对比

| 维度    | 自由Agent       | Workflow Agent |
| ----- | ------------- | -------------- |
| LLM职责 | 自己规划、排序、推理、试错 | 仅负责当前步骤的局部决策   |
| 稳定性   | 不稳定           | 稳定             |
| 可复现性  | 难             | 易              |
| 适用场景  | Demo、聊天       | 工业生产           |

---

### 6.5 LLM 的能力边界与职责定位

| 任务     | 是否适合LLM |
| ------ | ------- |
| 语义理解   | 是       |
| 模糊意图解析 | 是       |
| 参数补全   | 是       |
| 人机交互   | 是       |
| 创意生成   | 是       |
| 精确流程控制 | 否       |
| 强状态管理  | 否       |
| 事务一致性  | 否       |
| 原子执行   | 否       |

**工业系统会"把创造性留给LLM，把确定性留给系统"。**

**进一步的结论：Agent ≠ LLM**

业界一个常见误区是把 Agent 等同于 LLM 本身。但在成熟的工业系统中：

- Agent 本体是 Runtime——它包含 Workflow Engine、状态机、Tool RAG、Dispatcher、调度器、上下文生命周期管理器等多个组件
- LLM 更像"推理协处理器（Reasoning Co-processor）"——只在 Runtime 调用它的时候提供语义判断、意图解析与参数生成
- 真正的"大脑"是 Workflow Engine，LLM 是被 Runtime 编排的一个组件，而不是反过来 LLM 主导 Runtime

这与早期 Demo 型 Agent（`while(true){ LLM(); Tool(); }` 式的 ReAct Loop）形成根本对比。现代生产级 Agent 正在从"LLM + Tools"范式迁移到"Workflow Runtime + LLM Nodes"范式：

```
旧范式： LLM 主导 → 自由调度 Tools
新范式： Workflow Runtime 主导 → 在节点上回调 LLM 做局部决策
```

即 LLM 成为 Workflow 图中的一个节点，而不是 Workflow 由 LLM 自由生成。LangGraph、Temporal、Prefect 等编排框架的兴起正是这一迁移的注脚。

---

### 6.6 与工业自动化哲学的同构性

非常有意思的是，Agent工程化与PLC/SCADA哲学高度一致：

**PLC为什么稳定？**

- 有限状态
- 固定扫描周期
- 明确状态转移
- 强约束

工业Agent也是一样：本文主张"把LLM关进笼子里"。这里"笼子"不是贬义，而是**安全边界 + 决策边界**。

工业控制几十年前就已经在实践中证明："确定性系统" 远比 "自由智能系统" 更可靠。Agent 工程化某种意义上正在重演 PLC/SCADA 的演化路径——从"自由控制逻辑"走向"严格的扫描周期 + 状态机模型"。这不是历史的倒退，而是工程化必然的收敛。

**两层笼子的纵深防御映射**

§4.7 已论证：工业 SCADA Agent 的"笼子"有两层。这两层与 PLC/SCADA 经典的纵深防御层级形成精确对应：

| 防御层级（工业控制经典模型）         | Agent 工程化对应                          |
| ---------------------- | ------------------------------------ |
| BPCS（基本过程控制系统）的"软件健壮性" | 软件工程层笼子（§4.1~§4.6 五大策略）              |
| SIS（安全仪表系统）的"物理隔离与认证"  | 工业安全层笼子（§4.7 ISA-95 分层、SIL 认证、运行态零写） |
| 机械保护层（释放阀、防爆膜）         | 物理层 fail-safe（与 Agent 无关）            |
| 应急响应（应急预案、人员疏散）        | 组织流程层（与 Agent 无关）                    |

PLC 的"笼子"在物理层（IEC 61508/SIL 认证的电路与算法），Agent 的"笼子"在认知层（约束系统裁剪决策空间）——**两层在不同抽象层次上共同构成纵深防御**，缺一不可。这是为什么本文从摘要开始就强调"两层笼子"：不是两个独立话题，而是同一个工程化谱系在不同抽象层级上的延续。

**另一层同构：Agent Runtime ≈ AI 操作系统**

如果继续抽象，会发现成熟 Agent Runtime 与传统操作系统的核心抽象高度对应：

| 操作系统概念          | Agent Runtime 对应        |
| --------------- | ----------------------- |
| 进程（Process）     | Workflow Instance       |
| 调度器（Scheduler）  | DAG / 状态机 Scheduler     |
| 内存管理            | Context Window 生命周期管理   |
| IPC             | Tool Call               |
| 驱动（Driver）      | MCP Tool / Resource     |
| 权限控制            | Tool Visibility / RBAC  |
| 中断（Interrupt）   | Event / Subscribe       |
| 检查点（Checkpoint） | Workflow Snapshot       |
| 异常恢复            | Retry / Rollback / Saga |
| 系统调用            | LLM 推理回调                |

这种同构并非巧合：一旦系统需要承担"长任务、强状态、多组件协作、可恢复、可审计"等工程属性，它就必然演化出与传统 OS 类似的抽象层次。LangGraph、Temporal、Prefect 等编排框架的崛起，本质上是 AI 行业正在为自己补建"OS 层"——而工业 SCADA Agent 是这股趋势中要求最严苛的客户：既要 OS 级的可靠性，又要 LLM 级的语义灵活性。

---

### 6.7 本文架构的范式定位：工业控制垂类 Harness

把前述哲学讨论收敛成一句精确的工程定位——本文阐述的架构本质上是 **工业控制领域的垂类 Agent Harness**。它与通用 Harness（Claude Code、LangGraph、Temporal 等）共享"LLM 协处理器 + Runtime 主导"的同一核心范式，但有两个根本差异：

1. **领域内化**：工艺依赖、点位关系、组态/运行隔离等行业知识直接固化为 Workflow 与状态机定义，不依赖 LLM 临场推理或 Prompt 软约束
2. **安全冗余**：通用 Harness 只有一层"软件工程笼子"，垂类 Harness 必须再叠加一层"功能安全笼子"（§4.7）——前者管 LLM 选错，后者管 LLM 做坏

同样的方法论可推演到其他强约束垂类：金融交易（风控 + 监管）、医疗辅助（HIPAA + FDA）、法律检索（管辖权 + 引用准确）等——它们都符合"通用范式 + 垂类特化"的同一形态。

> **一句话**：本文不是"又一篇 SCADA Agent 架构论文"，而是 **"工业控制垂类 Harness 的构造规范"**。

---

### 6.8 互联网 Demo 难以生产化的根因

很多Demo直接给LLM 200个Tool，然后AutoGPT式自由规划。短Demo看起来很惊艳，但生产环境：不稳定、成本高、难复现、易漂移、无法回归测试。

真正生产系统越来越像：

> **"LLM驱动的有限状态工作流引擎"**

而不是：

> "无限自由AI"

---

## 7. 总结

本文围绕"SCADA Agent 如何稳定、准确、可恢复地完成涉及数十到数百个原子操作的真实工业任务"这一核心问题，系统性地阐述了工业级 Agent 的设计哲学与具体架构。

**主要结论如下：**

1. **问题根源**：LLM 本质是概率模型，在 SCADA 这种"数百原子能力 + 强工艺顺序 + 状态强依赖"的场景下会出现三层失效——工具层（选错、混淆、注意力稀释）、任务层（工序错乱、漏步骤、参数漂移、状态丢失、失败无回滚）、领域层（越权调用敏感工具）。其中**任务层与领域层失效不是"换更大模型"能解决的**——它们源自任务本身的结构性与工业控制的物理约束，必须靠架构约束兜底。

2. **核心策略**：本文提出Workflow为核心、"分层Tool + Tool RAG + Workflow + 状态机"的四位一体架构。

3. **关键技术与对应防护**：五大策略各自压制 §2 中识别的不同失效——
   
   - **分层 Tool**：把"扁平大空间选择"变为"分层小空间选择" → 防工具层选错与近义混淆
   - **Tool RAG**：动态裁剪 + 多轮上下文融合 → 防工具层稀释、跨步参数漂移
   - **Workflow**：预定义执行序列 + Saga 事务补偿 → 防任务层工序错乱、漏步骤、失败无法回滚
   - **状态机**：按阶段动态收缩 + 状态持久化与快照 → 防长流程状态丢失、越权调用
   - **MCP Resources 分离**：把只读查询从 Tool 列表剥离 → 减少污染、降低注意力稀释

4. **设计哲学**：工业Agent工程化的本质，是"把概率模型逐步约束成近似确定性系统"。这与传统PLC/SCADA的稳定性哲学高度同构。

5. **LLM定位**：在工业系统中，LLM应当承担"创造性、语义理解、意图解析、参数补全"等任务，而"精确流程控制、强状态管理、事务一致性、原子执行"应交由确定性系统完成。

6. **功能安全边界**：软件工程层的五大策略只是"内层笼子"，仅能防止 LLM **选错** Tool；工业语境下还必须叠加由 **IEC 61508/61511、SIL、SIS、ISA-95** 等功能安全标准定义的"外层笼子"，以防止 LLM **做坏**——伤人、毁设备、违法。两层互为冗余、不可相互替代。落到现阶段最具操作性的工程准则上，可以浓缩为一句话：**LLM 主要活跃于组态期，极少介入运行态；即便介入运行态，也牢牢守住只读不写的红线——运行态写权限不是用审计与确认去管，而是从系统层就不给**（详见 §4.7）。

7. **范式定位**：本文所提架构是**工业控制场景的垂类 LLM Agent Harness**——以通用 Harness（Claude Code、LangGraph、Temporal 等）"LLM 协处理器 + Runtime 主导"的范式为底座，把领域工艺与功能安全约束预先内化为 Harness 自身。该方法论可作为其他强约束垂类（金融、医疗、法律）Harness 设计的参考模板（详见 §1.3、§6.7）。

**一句话总结：**

> 真正工业级SCADA Agent的核心，不是"让LLM拥有所有能力"，而是——
> 
> **"在正确的阶段，只让LLM看到当前最相关、最安全、最有限的能力。"**
> 
> 而工业语境下，"笼子"实际有两层：**软件工程层的约束系统让 LLM 不会选错；工业安全层的标准与隔离让 LLM 不会做坏。** 前者管"对不对"，后者管"敢不敢"——两层互为冗余，缺一不可。

这就是"将AI关进笼子里"的真正含义：不是限制智能，而是用工程化的边界与安全标准的双重约束，把LLM的概率性创造力安全地引导到工业系统所需的确定性轨道之上。

换言之，本文阐述的不是"新 Agent 算法"，而是**工业控制领域的垂类 Agent Harness 范式**——在通用 Harness 范式之上叠加领域工艺与功能安全约束，把 LLM 安放在它应在的位置：**工业系统中一个受控、可审计、可恢复的语义节点，而非自主决策者**。

---

*本文系统性地总结了SCADA软件Agent设计中Tool管理、Workflow编排与状态控制的核心理论，并将其定位为"工业控制垂类 Agent Harness"范式，为工业AI-组态系统的工程化实践提供了完整的方法论框架。*
