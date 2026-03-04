# Structra — 中文文档排版Agent

中文 Word 文档排版工具，支持接入大模型 API，实现智能排版分析。

---

## 功能概述

用户上传 Word 文档后，系统按照既定规则进行排版并输出新文档。引入大模型模块后，支持三种排版模式，兼容纯规则与 AI 辅助分析两种工作流程。

---

## 智能 Agent 模式

本项目支持三种排版模式，通过环境变量 `LLM_MODE` 控制：

| 模式 | 说明 |
|---|---|
| `rule` | 纯规则模式（默认兜底，无需 API Key） |
| `llm` | 纯 LLM 模式（完全由大模型分析文档结构 + 产出可执行建议） |
| `hybrid` | 混合模式（规则优先，仅在触发条件命中时调用 LLM，**推荐**） |

### 模式职责边界与差异对比

| 维度 | `rule` | `llm` | `hybrid` |
|---|---|---|---|
| **执行顺序** | 仅规则 | 仅 LLM（全量） | 规则 → 触发判断 → 可选 LLM |
| **LLM 调用时机** | 从不 | 始终（所有段落） | 仅当触发条件命中 |
| **触发条件** | — | — | ① `unknown` 标签 ② 标题文本过长 ③ 连续短正文（潜在列表） |
| **标签来源** | 规则 | LLM（规则兜底未覆盖段落） | 规则（未触发）+ LLM（触发段落，低置信度回退规则） |
| **语义建议输出** | 无 | 有（`llm_review.suggestions`） | 有（触发时，`llm_review.suggestions`） |
| **报告额外字段** | — | `llm_review`（建议列表） | `hybrid_triggers`（触发原因/指标）+ `llm_review`（若触发） |
| **API Key 要求** | 否 | 是 | 否（未触发时）/ 是（触发时） |

### 为什么之前 llm 与 hybrid 看不出区别

**根本原因**：旧实现中 `_llm` 和 `_hybrid` 均无差别地全量调用 LLM，且只返回结构标签（无语义建议），
导致两种模式的输出几乎相同。

**修复后的差异**：
1. **hybrid 增加了"门控"机制**：先运行规则层，评估三类触发条件；若无触发则完全不调用 LLM，
   输出的 `hybrid_triggers.triggered=false` 且 `llm_called=false`，可在报告中直观验证。
2. **llm 模式使用语义审阅 Prompt**：调用 `call_review` 返回 `DocumentReview`，包含完整的
   `suggestions` 建议列表（`category/severity/confidence/evidence/suggestion/rationale/apply_mode`）。
3. **hybrid 模式仅审阅触发段落**：`call_review` 传入 `triggered_indices`，LLM 只处理高价值
   的问题段落（通常 ≤ 20%），而非全量调用。

### LLM 建议输出格式（`llm_review.suggestions`）

当 `label_mode=llm` 或 `label_mode=hybrid`（且触发）时，报告中会包含 `llm_review` 字段：

```json
{
  "llm_review": {
    "suggestions": [
      {
        "category": "hierarchy",
        "severity": "high",
        "confidence": 0.92,
        "evidence": "段落3: 一、研究背景与现状分析...",
        "suggestion": "建议将「一、」格式改为二级标题样式（14pt 黑体）",
        "rationale": "当前使用了一级标题字号，但编号层级为二级",
        "apply_mode": "manual",
        "paragraph_index": 3
      }
    ],
    "auto_applied": [],
    "manual_pending": [ ... ]
  },
  "hybrid_triggers": {
    "triggered": true,
    "reasons": ["标题层级疑似错误: 2 个标题段落文本超过 30 字符"],
    "triggered_paragraph_count": 2,
    "total_paragraph_count": 15,
    "llm_called": true,
    "metrics": { "unknown_count": 0, "ambiguous_heading_count": 2 }
  }
}
```

### 触发条件说明（hybrid 模式）

| 触发条件 | 触发规则 | 原因分类 |
|---|---|---|
| `unknown_labels` | 规则标为 `unknown` 的段落 ≥ 1 | 术语/类型不明 |
| `heading_ambiguity` | `h2`/`h3` 标签但文本 > 30 字符 | 标题层级疑似错误 |
| `potential_list` | 连续 ≥3 个短正文段落（≤60 字符） | 结构化改写机会 |

**无触发时**（所有条件均未命中）：hybrid 模式完全等同于 rule 模式，不调用 LLM，
`report.hybrid_triggers.triggered=false`。

### 环境变量配置

```bash
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="https://your-llm-endpoint/v1"
export LLM_MODEL="gpt-4o"         # 或任意兼容模型名
export LLM_TIMEOUT_S="60"         # 读取超时基础值（秒）
export LLM_CONNECT_TIMEOUT_S="10" # TCP 连接超时（秒）
export LLM_MAX_TIMEOUT_S="120"    # 动态超时上限（秒）
export LLM_RETRY_ATTEMPTS="3"     # 超时/网络错误最大重试次数
export LLM_RETRY_BACKOFF_S="1"    # 重试指数退避基础等待时间（秒）
export LLM_MODE="hybrid"          # rule | llm | hybrid
```

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `LLM_API_KEY` | 大模型 API 密钥 | `""` |
| `LLM_BASE_URL` | API 基础 URL | `"https://api.openai.com/v1"` |
| `LLM_MODEL` | 使用的模型名称 | `"gpt-4o"` |
| `LLM_TIMEOUT_S` | 读取超时基础值（秒），动态超时以此为下限 | `60` |
| `LLM_CONNECT_TIMEOUT_S` | TCP 连接超时（秒），建立连接失败时更快失效 | `10` |
| `LLM_MAX_TIMEOUT_S` | 动态超时上限（秒），防止段落数过多时超时过长 | `120` |
| `LLM_RETRY_ATTEMPTS` | 超时/网络错误最大重试次数（含首次，≥1） | `3` |
| `LLM_RETRY_BACKOFF_S` | 重试指数退避基础等待秒数（实际等待 = base × 2ⁿ⁻¹） | `1` |
| `LLM_MODE` | 排版模式 `rule/llm/hybrid` | `"hybrid"` |

> **动态超时说明**：实际读取超时 = `LLM_TIMEOUT_S + 段落数 × 0.5`（秒），上限为 `LLM_MAX_TIMEOUT_S`。
> 文档越大，允许的读取时间越长，有效避免大文档超时。
> 若调用失败为超时或网络错误，系统会自动重试（最多 `LLM_RETRY_ATTEMPTS` 次，指数退避），
> 全部重试失败后仍会回退到规则排版，不影响输出结果。

### 架构说明

```
用户上传 Word 文档
        │
        ▼
  mode_router.py（根据 LLM_MODE 路由）
   ├── rule 模式  ──────────────────────► 规则排版引擎 → report（无 llm_review）
   │
   ├── llm 模式   ── DocAnalyzer.call_review ──► DocumentReview（标签 + 建议）
   │                                           ──► 排版引擎 → report（含 llm_review.suggestions）
   │
   └── hybrid 模式
         ├── 规则层运行 + 触发条件评估
         │     ├── 无触发（triggered=false）──► 规则结果 → report（hybrid_triggers.llm_called=false）
         │     └── 有触发（triggered=true）
         │           ├── call_review（仅触发段落）──► DocumentReview（标签 + 建议）
         │           ├── 合并：触发段落用 LLM，其余保留规则
         │           └── report（含 hybrid_triggers + llm_review.suggestions）
```

---

## 目录结构

```
MyAgent/
├── agent/
│   ├── __init__.py
│   ├── doc_analyzer.py      # 文档结构分析 Agent（调用 LLM）
│   ├── llm_client.py        # LLM 接入与调用封装
│   ├── mode_router.py       # 三种模式路由逻辑
│   ├── prompt_templates.py  # Prompt 模板管理
│   ├── schema.py            # JSON 输出 Schema 定义（pydantic）
│   └── Structura_agent.py   # 文档 Agent 主入口
├── core/                    # 规则排版核心模块
├── service/                 # 服务层（format_service）
├── specs/                   # 排版规范 YAML 配置
│   ├── default.yaml         # 通用默认模板
│   ├── academic.yaml        # 中文学术论文模板
│   ├── gov.yaml             # 政府公文模板（GB/T 9704）
│   └── contract.yaml        # 合同/协议模板
├── config.py                # 环境变量配置读取
├── format_docx.py           # CLI 入口
└── requirements.txt
```

---

## 专项模板库

通过 `--spec` 参数选择文档类型对应的排版规范，满足差异化语义样式需求：

| 模板文件 | 适用场景 | 特点 |
|---|---|---|
| `specs/default.yaml` | 通用文档 | 宋体/TNR，小四正文，摘要斜体 |
| `specs/academic.yaml` | 期刊/学位论文 | 五号正文，GB/T 7714 参考文献悬挂缩进 |
| `specs/gov.yaml` | 党政机关公文 | 仿宋_GB2312 四号，黑体标题，符合 GB/T 9704 |
| `specs/contract.yaml` | 合同/协议 | 宋体小四，条款标题黑体，签字落款专项样式 |

```bash
# 使用政府公文模板
python format_docx.py input.docx output.docx --spec specs/gov.yaml

# 使用学术论文模板 + hybrid 标注
python format_docx.py input.docx output.docx --spec specs/academic.yaml --label-mode hybrid
```

所有模板均支持完整的语义角色专项样式，包括 `abstract`、`keyword`、`reference`、`footer`、`list_item`，不再统一压扁为 `body`。

---

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

> **推荐**：将项目安装为可编辑包后，可直接使用 `python -m ui.app` 启动 Streamlit UI，无需手动修改 `sys.path`：
> ```bash
> pip install -e .
> python -m streamlit run ui/app.py
> ```

### 纯规则模式（无需 API Key）

```bash
python format_docx.py input.docx output.docx --label-mode rule
```

### 纯 LLM 模式

```bash
export LLM_API_KEY="your-api-key"
export LLM_MODE="llm"
python format_docx.py input.docx output.docx --label-mode llm
```

### 混合模式（推荐）

```bash
export LLM_API_KEY="your-api-key"
export LLM_MODE="hybrid"
python format_docx.py input.docx output.docx --label-mode hybrid
```

---

## 模块说明

### `config.py`

从环境变量读取所有大模型相关配置，集中管理，便于部署和切换。

### `agent/schema.py`

使用 `pydantic` 定义结构化输出 Schema：
- `ParagraphTag`：单段落结构标签（段落类型、置信度、推理说明等）
- `DocumentStructure`：整文档结构分析结果
- `LLMSuggestion`：单条语义建议（含 category/severity/confidence/evidence/suggestion/rationale/apply_mode）
- `DocumentReview`：文档语义审阅结果（结构标签 + 建议列表）

### `agent/llm_client.py`

封装大模型 API 调用：
- 基于 `openai` SDK，兼容所有 OpenAI 接口规范的模型（含国产模型）
- `call_structured(paragraphs)`：结构标注（返回 `DocumentStructure`）
- `call_review(paragraphs, triggered_indices, rule_labels)`：语义审阅（返回 `DocumentReview`，含建议）
- 支持超时控制：独立连接超时（`LLM_CONNECT_TIMEOUT_S`）与动态读取超时（随段落数自适应，上限 `LLM_MAX_TIMEOUT_S`）
- 自动重试（`LLM_RETRY_ATTEMPTS` 次，指数退避）：仅对超时/网络错误重试，鉴权失败立即抛出
- 详细错误类型分类：`connect_timeout` / `read_timeout` / `connect_error` / `auth` / `format_error`
- 统一异常处理，失败时抛出 `LLMCallError`

### `agent/prompt_templates.py`

管理系统 Prompt 和用户 Prompt 模板：
- `SYSTEM_PROMPT` / `build_user_prompt(paragraphs)`：结构标注 Prompt
- `REVIEW_SYSTEM_PROMPT` / `build_review_prompt(paragraphs, triggered_indices, rule_labels)`：语义审阅 Prompt（llm 全量 / hybrid 针对触发段落）

### `agent/doc_analyzer.py`

文档分析器：提取 `.docx` 段落文本 → 构造 Prompt → 调用 LLM → 返回 `DocumentStructure`。

### `agent/mode_router.py`

模式路由器：根据 `LLM_MODE` 将请求路由到 `rule` / `llm` / `hybrid` 三种处理分支。
- `_compute_hybrid_triggers`：评估三类触发条件（unknown 标签/标题歧义/潜在列表）
- hybrid 模式含门控逻辑：无触发时不调用 LLM，有触发时仅审阅触发段落

---

## 可量化基准（Quantifiable Benchmarks）

> 以下为当前测试集基准数据，用于衡量系统稳定性与可靠性。
> 测试平台：Python 3.11，python-docx 1.x，标准 x86 笔记本（8 核 16 GB）。

### 规则模式（rule）

| 指标 | 设计目标值 |
|---|---|
| 标题识别准确率（GB/T 编号格式） | ≥ 95% |
| 正文/空段分类准确率 | ≥ 98% |
| 单文档处理耗时 P50 / P95 | < 0.5 s / < 1.2 s（≤ 200 段） |
| 处理失败率（异常抛出） | < 0.1% |

### 混合模式（hybrid，GPT-4o）

| 指标 | 设计目标值 |
|---|---|
| 段落语义标签整体准确率 | ≥ 90%（含 abstract / keyword / reference / footer） |
| LLM 完全失败时规则兜底成功率 | 100% |
| 单文档端到端耗时 P50 / P95 | < 5 s / < 12 s（≤ 200 段，含 LLM 调用） |

### 格式输出质量

| 指标 | 设计目标值 |
|---|---|
| 空段压缩正确率（不误删跨容器） | 100% |
| 软回车拆段正确率 | 100% |
| 标签-格式映射覆盖率（无漏格角色） | 100%（h1/h2/h3/caption/body/abstract/keyword/reference/footer/list_item） |

> **说明**：上表为**设计目标值**，用于衡量系统稳定性与可靠性。
> 评测方法：在自有语料集上运行排版后，人工抽查 20% 段落，对照 spec 定义的字号/缩进/加粗/斜体进行对比。
> 若需在 CI 中自动收集覆盖率，运行 `python -m pytest tests/ -q` 并查看 `report.json` 中的 `labels.coverage` 与 `labels.consistency` 字段。

---

## ReAct 自校正 Agent（LangGraph）

### 架构概述

```
ingest → reason → act → validate ──(passed or max_iters)──► END
                   ▲                        │
                   └────────(retry)─────────┘
```

MyAgent 新增基于 [LangGraph](https://github.com/langchain-ai/langgraph) 的 **ReAct 迭代闭环**，将文档格式化升级为：

1. **Ingest** — 解析 `.docx` 得到 blocks
2. **Reason** — 生成 ActionPlan（思维链 + 动作列表）
3. **Act** — 执行动作（set_role / fix_heading_level / no_op …）
4. **Validate** — 运行 formatter，检查错误；若通过则结束，否则重试（最多 `REACT_MAX_ITERS` 次）

### 配置参数

| 参数 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `REACT_MAX_ITERS` | `REACT_MAX_ITERS` | `3` | ReAct 最大迭代次数 |
| `REACT_STRICT_SCHEMA` | `REACT_STRICT_SCHEMA` | `true` | 严格 Pydantic schema 校验 |
| `ENABLE_DOCLING` | `ENABLE_DOCLING` | `false` | 启用 Docling 文档解析 |
| `LLM_API_KEY` | `LLM_API_KEY` | `""` | 大模型 API Key（rule 模式不需要） |
| `LLM_BASE_URL` | `LLM_BASE_URL` | OpenAI | API 基础 URL |
| `LLM_MODEL` | `LLM_MODEL` | `gpt-4o` | 模型名称 |
| `LLM_MODE` | `LLM_MODE` | `hybrid` | 默认排版模式 |
| `LLM_TIMEOUT_S` | `LLM_TIMEOUT_S` | `60` | 读取超时（秒） |

### 运行方式

#### CLI（命令行）

```bash
# 规则模式（无需 API Key）
python format_docx.py input.docx output.docx --label-mode rule

# 混合模式
python format_docx.py input.docx output.docx --label-mode hybrid

# ReAct 迭代模式
python format_docx.py input.docx output.docx --label-mode react

# 输出报告
python format_docx.py input.docx output.docx --label-mode react --report report.json
```

#### Streamlit UI

```bash
streamlit run ui/app.py
```

#### Chainlit UI（ReAct 模式）

```bash
chainlit run ui/chainlit_app.py
```

访问后选择模式：`rule` / `llm` / `hybrid` / `react`，然后上传 `.docx` 文件。

### 常见问题排查

| 问题 | 原因 | 解决方案 |
|---|---|---|
| `ModuleNotFoundError: langgraph` | 未安装依赖 | `pip install langgraph>=0.2.0` |
| `ModuleNotFoundError: chainlit` | 未安装依赖 | `pip install chainlit>=1.0.0` |
| ReAct 模式无法调用 LLM | `LLM_API_KEY` 未设置 | 设置环境变量，或使用 rule 模式（无需 Key） |
| Docling 解析失败 | `docling` 包未安装 | 自动回退原 parser；若需启用请 `pip install docling` |
| `REACT_MAX_ITERS` 达到上限仍有错误 | 文档结构过于复杂 | 增大 `REACT_MAX_ITERS` 或改用 `hybrid` 模式 |

### 新增模块结构

```
agent/graph/
├── __init__.py          # 导出 GraphState, Action, ActionPlan 等
├── react_schemas.py     # Pydantic schema：Action / ActionPlan / Observation / GraphState
├── nodes.py             # LangGraph 节点函数：ingest / reason / act / validate / retry_router
└── workflow.py          # build_react_graph() + run_react_agent()

core/
├── docling_adapter.py   # Docling 解析适配器（可选，失败时回退原 parser）
└── schemas/
    ├── __init__.py
    └── report.py        # AgentReport / DiagnosticsReport / ReactTraceEntry …

ui/
└── chainlit_app.py      # Chainlit 前端（支持 rule/llm/hybrid/react 四种模式）
```
