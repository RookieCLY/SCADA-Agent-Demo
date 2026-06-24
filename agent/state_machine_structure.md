# State Machine Structure Diagram

Generated from `agent/state_machine.py` (277 lines)

## 以 State 为结点、Transition 为边的完整状态图

```mermaid
flowchart LR
    subgraph Entry["入口 / 出口"]
        AI["🔍 ANALYZE_INTENT<br/><small>解析用户意图</small>"]
        DONE["✅ DONE<br/><small>任务完成（终止态）</small>"]
    end

    subgraph Config["配置域"]
        CP["⚙️ CONFIG_POINT<br/><small>SCADA 点位</small>"]
        CA["⚠️ CONFIG_ALARM<br/><small>告警配置</small>"]
        CH["📊 CONFIG_HISTORY<br/><small>历史记录</small>"]
        CS["📜 CONFIG_SCRIPT<br/><small>脚本管理</small>"]
    end

    subgraph UI["UI 域"]
        MP["📄 MANAGE_PAGES<br/><small>页面管理</small>"]
        GL["🎨 GENERATE_LAYOUT<br/><small>布局绘制</small>"]
        BP["🔗 BIND_POINTS<br/><small>点位绑定</small>"]
    end

    subgraph Deploy["部署域"]
        VAL["✅ VALIDATE<br/><small>跨实体校验</small>"]
        DEP["🚀 DEPLOY<br/><small>部署 / 回滚</small>"]
    end

    subgraph Special["特殊"]
        ASK["❓ ASK_USER<br/><small>需用户澄清<br/>（无工具可用）</small>"]
    end

    %% Entry → Config
    AI --> CP
    AI --> CA
    AI --> CH
    AI --> CS

    %% Entry → UI
    AI --> MP
    AI --> GL
    AI --> BP

    %% Entry → Deploy
    AI --> VAL
    AI --> DEP

    %% Entry → Special
    AI --> ASK

    %% Entry → Done
    AI --> DONE

    %% Config → 互跳
    CP --> CA
    CP --> CH
    CP --> CS
    CA --> CH
    CA --> CS
    CH --> CA
    CH --> CS

    %% Config → UI
    CP --> MP
    CP --> GL
    CP --> BP
    CA --> MP
    CA --> BP
    CH --> MP

    %% Config → Deploy
    CP --> VAL
    CP --> DEP
    CA --> VAL
    CA --> DEP
    CH --> VAL
    CH --> DEP
    CS --> VAL
    CS --> DEP

    %% Config → Done
    CP --> DONE
    CA --> DONE
    CH --> DONE
    CS --> DONE

    %% UI → 互跳
    MP --> GL
    MP --> BP
    GL --> MP

    %% UI → Config
    MP --> CA
    MP --> CH
    MP --> CS
    GL --> BP
    BP --> CA
    BP --> CH
    BP --> CS

    %% UI → Deploy
    MP --> VAL
    MP --> DEP
    GL --> VAL
    GL --> DEP
    BP --> VAL
    BP --> DEP

    %% UI → Done
    MP --> DONE
    GL --> DONE
    BP --> DONE

    %% Deploy → 互跳
    VAL --> DEP
    DEP --> VAL

    %% Deploy → Entry
    VAL --> AI

    %% Deploy → Done
    VAL --> DONE
    DEP --> DONE

    %% Special → Entry / Done
    ASK --> AI
    ASK --> DONE

    %% 样式
    classDef entry fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef config fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef ui fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef deploy fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef special fill:#ffebee,stroke:#c62828,stroke-width:2px
    classDef terminal fill:#e0e0e0,stroke:#424242,stroke-width:2px,stroke-dasharray: 5 5

    class AI entry
    class CP,CA,CH,CS config
    class MP,GL,BP ui
    class VAL,DEP deploy
    class ASK special
    class DONE terminal
```

## 简化状态图（主路径）

```mermaid
stateDiagram-v2
    [*] --> ANALYZE_INTENT
    
    state "配置域" as Config {
        CONFIG_POINT
        CONFIG_ALARM
        CONFIG_HISTORY
        CONFIG_SCRIPT
    }
    
    state "UI 域" as UI {
        MANAGE_PAGES
        GENERATE_LAYOUT
        BIND_POINTS
    }
    
    state "部署域" as Deploy {
        VALIDATE
        DEPLOY
    }
    
    ANALYZE_INTENT --> Config
    ANALYZE_INTENT --> UI
    ANALYZE_INTENT --> Deploy
    ANALYZE_INTENT --> ASK_USER
    ANALYZE_INTENT --> DONE
    
    Config --> Config : 内部跳转
    Config --> UI
    Config --> Deploy
    Config --> DONE
    
    UI --> UI : 内部跳转
    UI --> Config
    UI --> Deploy
    UI --> DONE
    
    Deploy --> Deploy : VALIDATE ⟷ DEPLOY
    Deploy --> ANALYZE_INTENT : VALIDATE 回到分析
    Deploy --> DONE
    
    ASK_USER --> ANALYZE_INTENT
    ASK_USER --> DONE
    
    DONE --> [*]
```

## 状态定义

| 状态 | 描述 | 允许工具 | 可跳转至 | 终止态 |
|------|------|----------|----------|--------|
| **ANALYZE_INTENT** | 解析用户查询为高层意图 | 5 个 list/show 工具 | 10 个下游状态 | ❌ |
| **CONFIG_POINT** | 创建/更新/删除 SCADA 点位 | 4 个工具 | 9 个下游状态 | ❌ |
| **MANAGE_PAGES** | 创建/重命名/删除 HMI 页面 | 5 个工具 | 8 个下游状态 | ❌ |
| **GENERATE_LAYOUT** | 绘制图形、应用布局 | 9 个工具 | 5 个下游状态 | ❌ |
| **BIND_POINTS** | 将 SCADA 点位绑定到控件 | 3 个工具 | 6 个下游状态 | ❌ |
| **CONFIG_ALARM** | 创建/启用/禁用/删除告警 | 7 个工具 | 7 个下游状态 | ❌ |
| **CONFIG_HISTORY** | 配置历史采样/保留/查询 | 6 个工具 | 5 个下游状态 | ❌ |
| **CONFIG_SCRIPT** | 编写/启用/禁用脚本 | 7 个工具 | 3 个下游状态 | ❌ |
| **VALIDATE** | 部署前跨实体一致性检查 | 6 个工具 | 3 个下游状态 | ❌ |
| **DEPLOY** | 部署或回滚项目 | 4 个工具 | 2 个下游状态 | ❌ |
| **ASK_USER** | 需用户澄清（无工具可用） | 0 个工具 | 2 个下游状态 | ❌ |
| **DONE** | 任务完成 | 0 个工具 | 无 | ✅ |

## 全部状态转移表

| 当前状态 | 可跳转至 |
|----------|----------|
| **ANALYZE_INTENT** | CONFIG_ALARM, CONFIG_POINT, MANAGE_PAGES, GENERATE_LAYOUT, BIND_POINTS, CONFIG_HISTORY, CONFIG_SCRIPT, DEPLOY, VALIDATE, ASK_USER, **DONE** |
| **CONFIG_POINT** | MANAGE_PAGES, GENERATE_LAYOUT, CONFIG_ALARM, BIND_POINTS, CONFIG_HISTORY, CONFIG_SCRIPT, VALIDATE, DEPLOY, **DONE** |
| **MANAGE_PAGES** | GENERATE_LAYOUT, BIND_POINTS, CONFIG_ALARM, CONFIG_HISTORY, CONFIG_SCRIPT, VALIDATE, DEPLOY, **DONE** |
| **GENERATE_LAYOUT** | MANAGE_PAGES, BIND_POINTS, VALIDATE, DEPLOY, **DONE** |
| **BIND_POINTS** | CONFIG_ALARM, CONFIG_HISTORY, CONFIG_SCRIPT, VALIDATE, DEPLOY, **DONE** |
| **CONFIG_ALARM** | BIND_POINTS, MANAGE_PAGES, CONFIG_HISTORY, CONFIG_SCRIPT, VALIDATE, DEPLOY, **DONE** |
| **CONFIG_HISTORY** | CONFIG_SCRIPT, CONFIG_ALARM, VALIDATE, DEPLOY, **DONE** |
| **CONFIG_SCRIPT** | VALIDATE, DEPLOY, **DONE** |
| **VALIDATE** | DEPLOY, ANALYZE_INTENT, **DONE** |
| **DEPLOY** | VALIDATE, **DONE** |
| **ASK_USER** | ANALYZE_INTENT, **DONE** |
| **DONE** | （无 — 终止态） |

## 转移矩阵

```
               To→
               A  C  C  C  M  G  B  C  C  V  D  A  D
               N  O  O  O  A  E  I  O  O  A  E  S  O
               A  N  N  N  N  N  N  N  N  L  P  K  N
               L  F  F  F  A  E  D  F  F  I  L  _  E
               Y  I  I  I  G  R  _  I  I  D  O  U
                  G  G  G  E  A  P  G  G  A  Y  S
                  _  _  _  _  T  O  _  _  T  _  E
                  P  A  S     E  I  H  S     E  R
From↓             O  L  C     _  N  I  C
                  I  A     L  T  S  R
                  N  R     A  S  T  I
                  T  M     Y     O  P
                              O  R  T
                              U  Y  _
                              T
ANALYZE_INTENT    ·  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✓
CONFIG_POINT      ·  ·  ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ·  ✓
MANAGE_PAGES      ·  ·  ·  ·  ·  ✓  ✓  ✓  ✓  ✓  ✓  ·  ✓
GENERATE_LAYOUT   ·  ·  ·  ·  ✓  ·  ✓  ·  ·  ✓  ✓  ·  ✓
BIND_POINTS       ·  ·  ·  ·  ·  ·  ·  ✓  ✓  ✓  ✓  ·  ✓
CONFIG_ALARM      ·  ·  ·  ·  ✓  ·  ✓  ✓  ✓  ✓  ✓  ·  ✓
CONFIG_HISTORY    ·  ·  ·  ·  ·  ·  ·  ✓  ✓  ✓  ✓  ·  ✓
CONFIG_SCRIPT     ·  ·  ·  ·  ·  ·  ·  ·  ·  ✓  ✓  ·  ✓
VALIDATE          ✓  ·  ·  ·  ·  ·  ·  ·  ·  ·  ✓  ·  ✓
DEPLOY            ·  ·  ·  ·  ·  ·  ·  ·  ·  ✓  ·  ·  ✓
ASK_USER          ✓  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ✓
DONE              ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·
```

## 关键特征

1. **全连通性**：ANALYZE_INTENT 可达所有非终止态，DONE 是唯一终止态
2. **无环保障**：`transit()` 在运行时校验合法性，非法跳转会抛 `ValueError`
3. **回环路径**：VALIDATE → ANALYZE_INTENT 允许校验失败后重新分析
4. **ASK_USER 隔离态**：工具白名单为空，只能回到 ANALYZE_INTENT 或结束
5. **DEPLOY ↔ VALIDATE 互跳**：部署后可以重新校验，校验后可再部署
6. **典型主路径**：ANALYZE_INTENT → CONFIG_ALARM → DONE（最小 E2E 路径）

## 类结构

```
@dataclass(frozen=True)
StateSpec:
  name: str                          # 状态名
  description: str                   # 描述
  allowed_tools: frozenset[str]      # 该状态允许的工具白名单
  next_states: frozenset[str]        # 允许的下一状态集合
  terminal: bool = False             # 是否为终止态

STATES: dict[str, StateSpec]         # 全局状态目录（12 个条目）

INITIAL_STATE = "ANALYZE_INTENT"     # 初始状态

@dataclass
StateMachine:
  current: str = INITIAL_STATE       # 当前状态
  history: list[str]                 # 状态历史

  can_transit(target) -> bool        # 检查是否可跳转
  transit(target)                    # 执行跳转（校验合法性）
  is_terminal (property)             # 是否在终止态
  allowed_tools(state?) -> set       # 获取状态的白名单工具
  filter_tools(candidates) -> list   # 硬过滤工具列表
```

## 执行示例：最小 E2E 路径

```
ANALYZE_INTENT
  ├── LLM 解析用户查询 → "创建告警"
  ├── filter_tools([list_points, create_page, ...]) → [list_points]
  └── transit("CONFIG_ALARM")
      │
CONFIG_ALARM
  ├── filter_tools([create_analog_alarm, create_digital_alarm, ...])
  ├── LLM 调用 create_analog_alarm
  ├── LLM 调用 set_threshold
  └── transit("DONE")
      │
DONE (terminal)
  └── is_terminal → True → 结束
```
