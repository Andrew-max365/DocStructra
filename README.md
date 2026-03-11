# Structura — 中文文档智能排版 Agent

中文 Word 文档全自动排版工具，支持无缝接入大模型 API 实现智能排版分析与错别字校对。系统内置基于 LangGraph 的 ReAct 自校正迭代架构，提供极速稳定的排版体验。

---

## 🎯 运行模式说明 (必读)

为了兼顾普通用户的“傻瓜式”体验与高级用户的定制化需求，系统对运行模式的入口进行了科学的区分。当前代码实际支持 **`hybrid`** 和 **`react`** 两种模式，具体调用规则如下：

### 1. Web UI 交互界面（极简模式）
* **强制 ReAct 迭代**：在前端 UI 中，去除了繁琐的模式切换按钮。用户上传文档后，系统**默认且强制使用最高级的 `react` 模式**（即 Ingest → Reason → Act → Validate 的多轮自校正闭环），以保障最佳的排版质量。
* **隐藏的 Hybrid 兜底**：如果遇到极其复杂的文档导致 ReAct 流水线崩溃，系统会在后台**静默降级为 `hybrid` 混合模式**（规则扫描+异常段落大模型介入）重新处理，确保您最终一定能拿到排版好的文档。

### 2. 命令行 CLI 与 API（高阶模式）
* 面向开发者或自动化脚本，您可以通过参数**显式选择**使用哪种模式。
* `--label-mode` 仅支持传入 `hybrid` 或 `react`。

---

## ✨ 核心特性

* **多规范模板**：内置 `default` (通用)、`academic` (学术论文)、`gov` (政府公文 GB/T 9704)、`contract` (合同协议) 等排版规范。
* **全新交互式 UI**：基于 Chainlit 构建的可视化界面，支持文档流式处理直播、错别字 Diff 可视化确认（支持一键“✅ 全部接受 / ❌ 全部拒绝”）。
* **自然语言增量排版**：支持在 UI 聊天中通过 `/f` 或 `/format` 前缀发送增量指令（如 `/f 把大标题改成红色，正文字号改成 14`），系统会对刚才排版好的文档进行**增量修改**，无需重新上传。
* **页面级排版控制**：支持通过自然语言调整页边距、页眉页脚距离等 section 级参数（如“上3下2.5左2.6右2.6厘米”）。
* **模板中心 + 领域路由**：Agent 会结合 LLM 解析与领域路由自动选择 `default/academic/gov/contract` 模板后再应用增量配置。
* **生产级 API 服务**：内置基于 FastAPI 的服务端，支持 API Key 鉴权与一键 Bundle 下载。

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> **推荐**：为了正常启动最新架构，请确保环境内已安装 `chainlit>=1.0.0` 和 `langgraph>=0.2.0`。

### 2. 环境变量配置

请配置以下大模型相关的环境变量（可通过 `export` 或写入 `.env` 文件）：

```bash
export LLM_API_KEY="your-api-key"                 # 大模型 API 密钥（必填）
export LLM_BASE_URL="[https://api.openai.com/v1](https://api.openai.com/v1)"   # API 基础 URL (支持兼容 OpenAI 格式的国产大模型)
export LLM_MODEL="gpt-4o"                         # 使用的模型名称
export REACT_MAX_ITERS="3"                        # ReAct 模式的最大允许重试迭代次数
```

### 3. 启动 Web 交互界面 (推荐)

```bash
chainlit run ui/chainlit_app.py
```
启动后访问本地端口，**直接上传 `.docx` 文件**即可开始全自动排版（自动走 ReAct 流程）。
* 💬 **自由交谈**：直接发送文本与助手对话。
* 🎨 **排版指令**：以 `/f` 或 `/format` 开头发送指令，微调当前文档的格式。

### 4. 命令行使用 (CLI)

如果您不需要可视化界面，可以通过命令行高效处理：

```bash
# 1. 混合模式 (规则扫描为主，仅对识别困难的异常段落唤醒大模型，速度极快)
python format_docx.py input.docx output.docx --label-mode hybrid

# 2. ReAct 迭代模式 (大模型深度参与多轮自校正，适合排版极为混乱的文档)
python format_docx.py input.docx output.docx --label-mode react

# 3. 指定专项模板 (以政府公文规范为例)
python format_docx.py input.docx output.docx --label-mode hybrid --spec specs/gov.yaml
```

---

## 🔌 API 服务端部署

系统提供面向生产环境的 REST API，支持严格鉴权配置，方便接入现有业务流：

### 启动服务

```bash
export SERVER_API_KEY="your-strong-secret-key"    # 生产环境强烈建议配置鉴权密钥
export REQUIRE_AUTH=true                          # 设为 true 开启强制鉴权防线 (fail-fast)
uvicorn api.server:app --host 0.0.0.0 --port 8000
```

### 接口调用示例

**格式化文档并返回 JSON 报告：**
```bash
curl -X POST "[http://127.0.0.1:8000/v1/agent/format](http://127.0.0.1:8000/v1/agent/format)" \
  -H "X-API-Key: your-strong-secret-key" \
  -F "file=@tests/samples/sample.docx" \
  -F "label_mode=hybrid" \
  -F "spec_path=specs/default.yaml"
```

**一键下载打包产物 (ZIP Bundle：含新文档及 JSON 分析报告)：**
```bash
curl -X POST "[http://127.0.0.1:8000/v1/agent/format/bundle](http://127.0.0.1:8000/v1/agent/format/bundle)" \
  -H "X-API-Key: your-strong-secret-key" \
  -F "file=@tests/samples/sample.docx" \
  -o structura_bundle.zip
```

---

## 📁 核心目录结构

```text
MyAgent/
├── agent/
│   ├── graph/             # LangGraph ReAct Agent 核心 (节点、状态机、工作流)
│   ├── intent_parser.py   # 增量排版自然语言意图解析器
│   └── doc_analyzer.py    # LLM 分析与交互封装
├── core/                  # 底层规则排版引擎，负责具体格式刷写
├── service/               # FastAPI 路由与业务逻辑抽象层
├── specs/                 # 排版规范 YAML 配置集 (gov, academic, default, contract)
├── ui/                    # 交互前端 (chainlit_app.py 及 Diff 渲染工具)
├── format_docx.py         # 命令行 CLI 入口程序
└── api/server.py          # FastAPI 服务端启动入口
```
