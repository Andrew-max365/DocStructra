# Structura — 中文文档智能排版 Agent

Structura 是一款全自动的中文 Word 文档排版工具，无缝接入大模型 API 实现智能排版分析与错别字校对。系统内置基于 LangGraph 的 ReAct 自校正迭代架构，并最新引入了多模态视觉审查（Visual Review）机制，为您提供极速、稳定且出版级的排版体验。

---

## ✨ 核心特性

* **多规范模板路由**：内置 `default` (通用)、`academic` (学术论文)、`gov` (政府公文 GB/T 9704)、`contract` (合同协议) 等排版规范。Agent 会结合 LLM 解析与领域路由自动选择合适的模板。
* **ReAct 智能迭代闭环**：基于 LangGraph 构建的 Ingest → Reason → Act → Validate 工作流，大模型深度参与多轮自校正，轻松应对极为混乱的排版。
* **全新多模态视觉审查 (Phase 3)**：支持调用 Vision API（如 gpt-4o-mini）结合 LibreOffice 进行文档渲染与视觉反思，从“视觉直观”层面审视排版结果，进一步提高排版精度。
* **自然语言增量排版**：支持在可视化 UI 聊天中通过 `/f` 或 `/format` 前缀发送增量指令（如“/f 把大标题改成红色，正文字号改成 14”），系统会对文档进行局部修改，无需重新上传。
* **外部知识与工具扩展**：支持配置 Google/Bing 搜索引擎 API 获取实时信息，并可开启 Docling 适配器增强文档解析能力。
* **生产级 API 服务**：内置基于 FastAPI 的服务端，支持严密的安全鉴权体系、健康检查以及一键 Bundle 产物下载。

---

## 🎯 运行模式说明 (必读)

系统兼顾了普通用户的“傻瓜式”体验与高级用户的定制化需求，支持以下两种核心模式：

### 1. Web UI 交互界面（极简模式）
* **强制 ReAct 迭代**：前端 UI 默认且强制使用最高级的 `react` 模式，保障最佳质量。
* **隐藏的 Hybrid 兜底**：若遇到极其复杂的文档导致 ReAct 流水线崩溃，系统会在后台静默降级为 `hybrid` 混合模式重新处理。

### 2. 命令行 CLI 与 API（高阶模式）
* 面向开发者或自动化脚本，您可以通过参数显式指定 `--label-mode hybrid`（规则为主，极速）或 `--label-mode react`（大模型深度介入）。

---

## 🚀 快速开始

### 1. 环境依赖

```bash
# 推荐使用 Python 3.10 及以上版本
pip install -r requirements.txt
```
> **提示**：如果您需要开启多模态视觉审查功能，请确保系统中已安装 LibreOffice，以便工具能调用 `soffice` 命令进行图片渲染。

### 2. 安全配置环境变量

为了更安全地管理 API 密钥并避免硬编码，请在项目根目录下创建一个名为 **`key.env`** 的本地文件，并写入您的配置：

```env
# --- 基础大模型配置 (必填) ---
LLM_API_KEY="your-llm-api-key"
LLM_BASE_URL="[https://api.openai.com/v1](https://api.openai.com/v1)"
LLM_MODEL="gpt-4o"
REACT_MAX_ITERS="3"

# --- 多模态视觉审查 Phase 3 (可选) ---
VISUAL_REVIEW_ENABLED="false"     # 设为 true 开启视觉反思
VISION_API_KEY="your-vision-api-key"
VISION_MODEL="gpt-4o-mini"
# LIBREOFFICE_PATH="soffice"      # 自定义 LibreOffice 路径

# --- 扩展工具支持 (可选) ---
BING_API_KEY="your-bing-key"
GOOGLE_API_KEY="your-google-key"
GOOGLE_CX="your-google-cx"
ENABLE_DOCLING="false"
```

### 3. 启动 Web 交互界面 (推荐)

```bash
chainlit run ui/chainlit_app.py
```
启动后访问本地端口，直接上传 `.docx` 文件即可开始排版。支持在对话框中发送 `/f` 指令进行增量微调。

### 4. 命令行使用 (CLI)

无需可视化界面的高效处理方案：

```bash
# 混合模式 (速度极快，适合日常排版)
python -m cli.format_docx input.docx output.docx --label-mode hybrid

# ReAct 模式 (深度自校正，适合高度混乱文档)
python -m cli.format_docx input.docx output.docx --label-mode react

# 指定专项模板
python -m cli.format_docx input.docx output.docx --label-mode hybrid --spec specs/gov.yaml
```

---

## 🔌 API 服务端部署

提供面向生产环境的 REST API。生产环境中**强烈建议**启用鉴权以防止未授权访问。

### 启动服务

```bash
# 在环境或 key.env 中配置鉴权：
export SERVER_API_KEY="your-strong-secret-key"
export REQUIRE_AUTH="true"  # 开启强制鉴权防线

uvicorn api.server:app --host 0.0.0.0 --port 8000
```

### 核心接口调用示例

**1. 健康检查：**
```bash
curl [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
```

**2. 格式化文档 (返回 JSON 及 Base64 编码的文档)：**
```bash
curl -X POST "[http://127.0.0.1:8000/v1/agent/format](http://127.0.0.1:8000/v1/agent/format)" \
  -H "X-API-Key: your-strong-secret-key" \
  -F "file=@tests/samples/sample.docx" \
  -F "label_mode=hybrid" \
  -F "spec_path=specs/default.yaml"
```

**3. 一键下载打包产物 (ZIP Bundle)：**
```bash
# 包含排版后的 Word 文档、JSON 诊断报告及 Agent 执行摘要
curl -X POST "[http://127.0.0.1:8000/v1/agent/format/bundle](http://127.0.0.1:8000/v1/agent/format/bundle)" \
  -H "X-API-Key: your-strong-secret-key" \
  -F "file=@tests/samples/sample.docx" \
  -F "label_mode=react" \
  -o structura_bundle.zip
```

---

## 📁 核心目录结构

```text
MyAgent/
├── agent/                    # Agent 核心逻辑、大模型调度与 ReAct 图
│   └── cluster/              # Agent 集群（总控制 Agent + 功能 Agent）
├── api/                      # FastAPI REST API 服务端
├── core/                     # Word 文档底层读写、排版及规则引擎
├── ui/                       # 基于 Chainlit 的交互式 Web 界面
├── service/                  # 业务逻辑与服务编排层
├── specs/                    # YAML 格式排版规范 (default, academic, gov 等)
├── tests/                    # 单元与集成测试用例
├── docs/                     # 附加文档说明 (如 API_USAGE.md)
├── config/                   # 全局配置管理 (对接 key.env)
│   └── __init__.py
├── cli/                      # CLI 命令行入口
│   └── format_docx.py
├── chainlit.md               # Chainlit 欢迎页面 Markdown
└── pyproject.toml            # Python 项目元数据
```
