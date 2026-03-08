# config.py
# 从环境变量读取大模型相关配置，所有变量均有合理默认值
import os
from dotenv import load_dotenv

# 尝试从运行目录下的 key.env 文件中加载环境变量
load_dotenv(dotenv_path="key.env")
# 大模型 API 密钥
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

# API 基础 URL（兼容 OpenAI 接口规范的国产模型均可对接）
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")

# 使用的模型名称
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o")

# 请求超时秒数（强制正整数，小于 1 时修正为 1）
LLM_TIMEOUT_S: int = max(1, int(os.getenv("LLM_TIMEOUT_S", "60")))

# 建立 TCP 连接的超时秒数（应远小于读取超时）
LLM_CONNECT_TIMEOUT_S: int = max(1, int(os.getenv("LLM_CONNECT_TIMEOUT_S", "10")))

# 动态超时上限：段落数自适应时 read timeout 的最大值
LLM_MAX_TIMEOUT_S: int = max(1, int(os.getenv("LLM_MAX_TIMEOUT_S", "120")))

# 超时/网络错误的最大重试次数（含首次，≥1）
LLM_RETRY_ATTEMPTS: int = max(1, int(os.getenv("LLM_RETRY_ATTEMPTS", "3")))

# 重试指数退避基础等待秒数（实际等待 = base * 2^(attempt-1)）
LLM_RETRY_BACKOFF_S: float = max(0.0, float(os.getenv("LLM_RETRY_BACKOFF_S", "1")))

# 排版模式：hybrid（混合，推荐）| react（Agent 迭代）
LLM_MODE: str = os.getenv("LLM_MODE", "hybrid")  # hybrid | react

# API 服务端鉴权 Key（为空则不启用认证，适合本地 Demo；生产环境请务必设置）
SERVER_API_KEY: str = os.getenv("SERVER_API_KEY", "")

# 生产环境硬性鉴权开关：REQUIRE_AUTH=true 时，若 SERVER_API_KEY 为空则启动时抛出异常
REQUIRE_AUTH: bool = os.getenv("REQUIRE_AUTH", "false").strip().lower() == "true"

# ReAct / LangGraph 配置
REACT_MAX_ITERS: int = max(1, int(os.getenv("REACT_MAX_ITERS", "3")))
REACT_STRICT_SCHEMA: bool = os.getenv("REACT_STRICT_SCHEMA", "true").strip().lower() == "true"
ENABLE_DOCLING: bool = os.getenv("ENABLE_DOCLING", "false").strip().lower() == "true"

# Bing 搜索配置:填入你的 Azure Bing Key
BING_API_KEY = ""

# Google 搜索配置
GOOGLE_API_KEY = ""
GOOGLE_CX = ""  #Google 搜索引擎 ID

# ---------------------------------------------------------------------------
# Phase 3: 视觉审查 (Visual Review) 配置
# ---------------------------------------------------------------------------

# 视觉审查总开关（True 时在工作流 Validate 后追加多模态视觉审查）
VISUAL_REVIEW_ENABLED: bool = os.getenv("VISUAL_REVIEW_ENABLED", "false").strip().lower() == "true"

# 多模态模型名称（必须支持图片输入，如 gpt-4o / qwen-vl-max）
VISUAL_REVIEW_MODEL: str = os.getenv("VISUAL_REVIEW_MODEL", LLM_MODEL)

# 最大渲染页数（控制 token 消耗与延迟）
VISUAL_REVIEW_MAX_PAGES: int = max(1, int(os.getenv("VISUAL_REVIEW_MAX_PAGES", "3")))

# LibreOffice soffice 可执行文件路径
LIBREOFFICE_PATH: str = os.getenv("LIBREOFFICE_PATH", "soffice")

# PNG 渲染分辨率（DPI）
VISUAL_REVIEW_DPI: int = max(72, int(os.getenv("VISUAL_REVIEW_DPI", "150")))

# 视觉反思最大迭代次数
REFLECTION_MAX_ITERS: int = max(0, int(os.getenv("REFLECTION_MAX_ITERS", "2")))

VISION_API_KEY: str = os.getenv("VISION_API_KEY", "")
VISION_BASE_URL: str = os.getenv("VISION_BASE_URL", "https://api.openai.com/v1")
VISION_MODEL: str = os.getenv("VISION_MODEL", "gpt-4o-mini")
