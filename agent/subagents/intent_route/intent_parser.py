import os
import json
import re
import hashlib
import openai
import datetime
import requests
from typing import Dict, Optional
from duckduckgo_search import DDGS
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from agent.subagents.intent_route.template_router import resolve_template

# 尝试导入优质 API 密钥，如果没有配置则设为 None
try:
    from config import BING_API_KEY, GOOGLE_API_KEY, GOOGLE_CX
except ImportError:
    BING_API_KEY = None
    GOOGLE_API_KEY = None
    GOOGLE_CX = None

# ==========================================
# 缓存与额度配置
# ==========================================
CACHE_FILE_PATH = os.path.join(os.path.dirname(__file__), "search_cache.json")
QUOTA_FILE_PATH = os.path.join(os.path.dirname(__file__), "search_quota.json")
PREMIUM_DAILY_LIMIT = 30  # 每天允许使用优质搜索 API 的上限次数
_FORMATTING_REQUEST_COORDINATOR = None


def load_search_cache() -> Dict[str, str]:
    try:
        with open(CACHE_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_search_cache(cache: Dict[str, str]):
    try:
        with open(CACHE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ [Cache] 缓存保存失败: {e}")


SEARCH_CACHE = load_search_cache()


def check_and_update_quota() -> bool:
    """检查今天优质 API 的额度是否还有剩余"""
    today = str(datetime.date.today())
    try:
        if os.path.exists(QUOTA_FILE_PATH):
            with open(QUOTA_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}

        if data.get("date") != today:
            data = {"date": today, "count": 0}

        if data["count"] < PREMIUM_DAILY_LIMIT:
            data["count"] += 1
            with open(QUOTA_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return True  # 额度充足
        return False  # 额度耗尽
    except Exception:
        return False


# ==========================================
# 工具定义
# ==========================================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "当用户要求按照特定的规范（如公文格式、特定期刊格式等）排版，且你不知道具体参数时，使用此工具搜索该规范的具体要求（字号、行距等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，例如：'中文公文 标准排版 字号 行距'"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# ==========================================
# 多级搜索逻辑实现（漏斗模型）
# ==========================================
def _call_bing_api(query: str, api_key: str) -> Optional[str]:
    print(f"📡 [Premium] 正在呼叫 Bing 服务器: {query[:15]}...")
    endpoint = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {"q": query, "mkt": "zh-CN", "count": 3}

    try:
        response = requests.get(endpoint, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        results = response.json().get("webPages", {}).get("value", [])
        snippets = [f"- {item['snippet']}" for item in results]
        return "\n".join(snippets) if snippets else None
    except Exception as e:
        print(f"❌ Bing API 调用失败: {e}")
        return None


def _call_google_api(query: str, api_key: str, cx: str) -> Optional[str]:
    print(f"📡 [Premium] 正在呼叫 Google 服务器: {query[:15]}...")
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {"key": api_key, "cx": cx, "q": query, "num": 3}

    try:
        response = requests.get(endpoint, params=params, timeout=10)
        response.raise_for_status()
        results = response.json().get("items", [])
        snippets = [f"- {item['snippet']}" for item in results]
        return "\n".join(snippets) if snippets else None
    except Exception as e:
        print(f"❌ Google API 调用失败: {e}")
        return None


def execute_web_search(query: str, use_cache: bool = True) -> str:
    global SEARCH_CACHE

    # 1. 缓存层：0成本，0延时
    if use_cache:
        query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
        if query_hash in SEARCH_CACHE:
            print(f"💾 [Cache Hit] 命中搜索缓存: {query[:30]}...")
            return SEARCH_CACHE[query_hash]

    # 2. 优质 API 层：需有额度，且配置了 Key (自动路由 Bing 或 Google)
    has_quota = check_and_update_quota()
    if has_quota:
        result = None
        # 优先尝试 Bing
        if BING_API_KEY and BING_API_KEY.strip():
            result = _call_bing_api(query, BING_API_KEY)

        # 如果没配 Bing，或者 Bing 失败了，且配置了 Google，则尝试 Google
        if not result and GOOGLE_API_KEY and GOOGLE_API_KEY.strip() and GOOGLE_CX and GOOGLE_CX.strip():
            result = _call_google_api(query, GOOGLE_API_KEY, GOOGLE_CX)

        # 如果商业 API 成功拿到了数据，直接返回并缓存
        if result:
            if use_cache:
                SEARCH_CACHE[query_hash] = result
                save_search_cache(SEARCH_CACHE)
            return result

    # 3. 兜底层：DuckDuckGo 免费爬虫
    print(f"🦆 [Fallback Search] 商业API未配置或失败，改用 DuckDuckGo: {query[:20]}...")
    try:
        results = DDGS().text(query, max_results=3)
        if not results:
            return "未搜到相关具体规范，请根据通用标准推测。"

        formatted_results = [f"- {res['body']}" for res in results]
        final_result = "\n".join(formatted_results)

        if use_cache:
            SEARCH_CACHE[query_hash] = final_result
            save_search_cache(SEARCH_CACHE)

        return final_result
    except Exception as e:
        print(f"❌ [Search Error] 所有搜索渠道均失败: {e}")
        return "搜索失败，请依赖你的基础知识进行排版。"


# ==========================================
# 动态加载外部常识库
# ==========================================
KNOWLEDGE_FILE_PATH = os.path.join(os.path.dirname(__file__), "formatting_knowledge.md")


def load_knowledge_base() -> str:
    try:
        with open(KNOWLEDGE_FILE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "暂无外部常识库，请依赖自身内置知识或搜索。"


def build_intent_prompt() -> str:
    return f"""你是一个专业的 Word 文档排版解析器，主要对现有文档进行格式美化。
你的唯一任务是：提取用户的排版要求，并严格转化为指定的 JSON 格式。

【JSON 格式要求】（绝对不能改变此结构，只输出用户提到的字段，未提到的不要输出）
{{
  "fonts": {{
    "zh": "中文字体名称（如宋体、黑体、仿宋_GB2312）",
    "en": "英文字体名称（如Times New Roman、Arial）"
  }},
  "body": {{
    "font_size_pt": 浮点数,
    "font_name": "字体名称（会同时设置中英文字体）",
    "line_spacing": 浮点数,
    "space_before_pt": 浮点数,
    "space_after_pt": 浮点数,
    "first_line_chars": 整数（首行缩进字符数，如2表示缩进两个字符）,
    "color": "十六进制颜色（如FF0000）",
    "bold": 布尔值,
    "italic": 布尔值
  }},
  "paragraph": {{
    "alignment": "justify/left/center/right"
  }},
  "heading": {{
    "h1": {{
      "font_size_pt": 浮点数,
      "font_name": "字体名称",
      "line_spacing": 浮点数,
      "space_before_pt": 浮点数,
      "space_after_pt": 浮点数,
      "color": "十六进制颜色",
      "alignment": "center/left/right",
      "bold": 布尔值,
      "italic": 布尔值
    }},
    "h2": {{...}},
    "h3": {{...}}
  }},
  "page": {{
    "margins_cm": {{"top": 浮点数, "bottom": 浮点数, "left": 浮点数, "right": 浮点数}},
    "header_distance_cm": 浮点数,
    "footer_distance_cm": 浮点数
  }},
  "_hft": {{
    "header": {{
      "text": "页眉文字内容",
      "font_size_pt": 浮点数,
      "bold": 布尔值,
      "alignment": "left/center/right"
    }},
    "footer": {{
      "text": "页脚文字内容",
      "font_size_pt": 浮点数,
      "alignment": "left/center/right"
    }},
    "page_numbers": {{
      "position": "header/footer",
      "alignment": "left/center/right",
      "show_total": 布尔值,
      "start_at": 整数
    }},
    "toc_format": {{
      "font_size_pt": 浮点数,
      "font_name_zh": "中文字体名（如宋体）",
      "font_name_en": "英文字体名（如Times New Roman）",
      "bold_top_level": 布尔值（一级目录条目是否加粗）
    }},
    "header_remove_border": 布尔值（true表示删除页眉横线/分割线）,
    "abstract": {{
      "title": "摘要标题文字（默认"摘要"）",
      "font_size_pt": 浮点数,
      "bold": 布尔值,
      "alignment": "left/center/right"
    }}
  }},
  "_meta": {{
    "domain": "default/academic/gov/contract",
    "spec_path": "specs/default.yaml | specs/academic.yaml | specs/gov.yaml | specs/contract.yaml"
  }}
}}

【字段说明】
1. fonts: 全局字体设置。如果用户说"中文用黑体，英文用Arial"，就设置 fonts.zh 和 fonts.en
2. body.font_name / heading.h1.font_name: 单独为某个角色设置字体（优先级高于全局 fonts）
3. line_spacing: 值 < 5.0 表示**倍数行距**（1.5 = 1.5倍）；值 ≥ 5.0 表示**固定值行距**（20 = 固定20磅）
4. space_before_pt / space_after_pt: 段前/段后距，单位磅(pt)
5. first_line_chars: 首行缩进字符数（如"首行缩进2字符" → 2，"取消首行缩进" → 0）
6. paragraph.alignment: 正文段落的全局对齐方式（justify=两端对齐, left=左对齐）
7. heading.h1.alignment: 标题的对齐方式（注意：旧字段名 align 也可以，但推荐用 alignment）
8. bold / italic: 加粗/斜体
9. page.margins_cm: 页面页边距（单位厘米）
10. _meta.domain/spec_path: 模板中心提示（可选），用于路由模板
11. _hft: 页眉/页脚/页码/目录/摘要操作（只在用户提及相关需求时才输出此字段）
    - header: 设置页眉文字；footer: 设置页脚文字
    - page_numbers: 插入页码，position 指定放在页眉("header")还是页脚("footer")，start_at 指定起始页码
    - toc_format: 修改目录内容的字体和字号（只修改已有目录，不插入新目录）
    - header_remove_border: 设为 true 可删除页眉下方的横线（分割线）
    - abstract: 设置摘要段落的样式（字号、对齐、加粗等）
    - 以上子字段只输出用户提到的部分，未提到的不要输出

【注意事项】
- 用户说"字体改为宋体"但未指定中英文时，设为 body.font_name
- 用户说"标题用黑体"时，设为 heading.h1.font_name（以及 h2、h3 如果用户没区分）
- 用户说"中文用宋体，英文用 Times New Roman"时，设为 fonts.zh 和 fonts.en
- 如果用户没有提到某个字段，就不要输出该字段！

【基础排版常识库】
{load_knowledge_base()}

【工具调用策略】
1. 优先常识库：当用户指令简单或能在上方常识库找到答案时，严禁调用搜索工具！
2. 复杂需搜索：遇到不懂的宏观规范，请调用 web_search 工具。
3. 铁律：最终输出只准包含一个合法的 JSON 字符串，绝对不要有多余的解释文字！
"""


def _split_meta_fields(payload: dict) -> tuple[dict, dict, dict]:
    """分离 _meta/spec_path/domain 等路由字段和 _hft 页眉页脚目录字段，避免污染 overrides。

    Returns:
        (overrides, meta, hft_actions) — overrides 是 spec 覆盖字段，meta 是路由字段，
        hft_actions 是页眉/页脚/页码/目录/摘要操作字段。
    """
    if not isinstance(payload, dict):
        return {}, {}, {}
    data = dict(payload)
    meta = {}
    if isinstance(data.get("_meta"), dict):
        meta.update(data.pop("_meta"))
    for k in ("spec_path", "domain"):
        if k in data and isinstance(data[k], str):
            meta[k] = data.pop(k)
    hft_actions = data.pop("_hft", {}) or {}
    return data, meta, hft_actions


# ==========================================
# 核心排版意图解析（含 ReAct）
# ==========================================
async def parse_formatting_intent(user_text: str) -> dict:
    if not user_text or not LLM_API_KEY:
        return {}

    client = openai.AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=30.0)
    messages = [
        {"role": "system", "content": build_intent_prompt()},
        {"role": "user", "content": f"用户指令: {user_text}"}
    ]

    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.1
        )

        response_message = response.choices[0].message
        messages.append(response_message.model_dump())

        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                if tool_call.function.name == "web_search":
                    args = json.loads(tool_call.function.arguments)
                    query = args.get("query", "")

                    search_result = execute_web_search(query)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": "web_search",
                        "content": search_result
                    })

            final_response = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=500
            )
            final_content = final_response.choices[0].message.content.strip()
        else:
            final_content = response_message.content.strip()

        return _extract_json(final_content) or {}
    except Exception as e:
        print(f"❌ [Error] 解析排版意图异常: {e}")
        return {}


async def parse_formatting_request(
    user_text: str,
    *,
    current_spec_path: str = "specs/default.yaml",
) -> dict:
    """高级入口：返回 overrides + 模板路由决策 + HFT 操作。

    保留 Agent 能力：
    - 先执行 LLM ReAct 解析（可工具搜索）
    - 再结合模板中心路由进行稳健落地
    - 同时提取页眉/页脚/页码/目录/摘要操作（_hft 字段）
    """
    return await _get_formatting_request_coordinator().parse_formatting_request(
        user_text,
        current_spec_path=current_spec_path,
    )


def _get_formatting_request_coordinator():
    global _FORMATTING_REQUEST_COORDINATOR
    if _FORMATTING_REQUEST_COORDINATOR is None:
        from agent.subagents.orchestrator.cluster import (
            HeaderFooterIntentFallbackAgent,
            IntentUnderstandingAgent,
            JsonGenerationAgent,
            MasterControlAgent,
            TemplateRoutingAgent,
        )
        from agent.subagents.format_act.header_footer_toc import parse_header_footer_command as _local_hft_parse

        _FORMATTING_REQUEST_COORDINATOR = MasterControlAgent(
            intent_agent=IntentUnderstandingAgent(parse_intent=parse_formatting_intent),
            json_agent=JsonGenerationAgent(split_meta_fields=_split_meta_fields),
            template_agent=TemplateRoutingAgent(resolve_template=resolve_template),
            hft_fallback_agent=HeaderFooterIntentFallbackAgent(parse_hft_command=_local_hft_parse),
        )
    return _FORMATTING_REQUEST_COORDINATOR


# ==========================================
# 校对建议反馈解析
# ==========================================
async def parse_feedback_intent(user_text: str, total_items: int) -> dict:
    """解析用户对于 LLM 校对建议的自然语言反馈"""
    if not user_text or not LLM_API_KEY:
        return {"intent": "unknown", "rejected_indices": []}

    client = openai.AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=15.0)

    system_prompt = f"""你是一个专业的意图解析器。当前系统向用户展示了 {total_items} 条文档修改建议（编号从 1 到 {total_items}）。
请解析用户刚刚输入的意思，并严格返回如下 JSON 格式：
{{
  "intent": "accept_all" | "reject_all" | "partial" | "unknown",
  "rejected_indices": [整数列表]
}}

【解析规则】
1. "accept_all": 用户同意所有修改。rejected_indices 必须为 []。
2. "reject_all": 用户拒绝所有修改。rejected_indices 必须为 [1, 2, ..., {total_items}]。
3. "partial": 用户只拒绝了部分，或只接受了部分。在 rejected_indices 列出所有被拒绝的编号。
4. "unknown": 无法判断意图。

【输出铁律】
只准输出合法的 JSON 字符串！
"""
    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"用户意见: {user_text}"}
            ],
            temperature=0.1,
            max_tokens=200
        )
        content = response.choices[0].message.content.strip()
        result = _extract_json(content)
        if result and "intent" in result:
            return result
        return {"intent": "unknown", "rejected_indices": []}
    except Exception as e:
        print(f"❌ [Error] 解析反馈意图异常: {e}")
        return {"intent": "unknown", "rejected_indices": []}


# ==========================================
# 强力 JSON 提取器
# ==========================================
def _extract_json(text: str) -> Optional[dict]:
    if not text: return None
    text = text.strip()

    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"): text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except:
        pass

    code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1))
        except:
            pass

    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except:
            pass

    return None


# ==========================================
# 局部/定向排版解析（Feature 2）
# ==========================================
async def parse_partial_format_request(user_text: str) -> dict:
    """
    解析用户的局部排版要求（只改某一项），返回 {"property": ..., "overrides": {...}}。

    :param user_text: 用户的排版指令（已确认为 PARTIAL_FORMAT 意图）
    :return: dict，包含 "property"（变更属性名）、"overrides"（spec overrides）
    """
    if not user_text or not LLM_API_KEY:
        return {"property": "unknown", "overrides": {}}

    client = openai.AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=20.0)

    system_prompt = """你是一个文档排版属性解析器。
用户只想修改文档的某一项特定属性，不需要全文重排。
请提取用户要修改的具体属性及其新值，以 JSON 格式输出。

输出格式：
{
  "property": "line_spacing" | "body_font_size" | "heading_font_size" | "font_name" | "margins" | "indent" | "other",
  "description": "一句话描述用户的修改需求",
  "overrides": {  // spec overrides，只包含用户提到的字段
    "body": {"line_spacing": 1.5},
    ...
  }
}

示例：
- "只改行间距为1.5倍" → {"property": "line_spacing", "overrides": {"body": {"line_spacing": 1.5}}}
- "只把正文字号改为12pt" → {"property": "body_font_size", "overrides": {"body": {"font_size_pt": 12.0}}}
- "只把h1标题字号改为小二" → {"property": "heading_font_size", "overrides": {"heading": {"h1": {"font_size_pt": 18.0}}}}

只输出 JSON，不要任何解释文字。"""

    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"用户指令：{user_text}"}
            ],
            temperature=0.1,
            max_tokens=300,
        )
        content = response.choices[0].message.content.strip()
        result = _extract_json(content)
        if result and "overrides" in result:
            return result
    except Exception as e:
        print(f"❌ [Error] 解析局部排版意图异常: {e}")

    # fallback：直接调用完整解析
    raw = await parse_formatting_intent(user_text)
    overrides, _, _hft = _split_meta_fields(raw)
    return {"property": "unknown", "overrides": overrides}


# ==========================================
# 定位并重排特定内容（Feature 4）
# ==========================================
async def parse_locate_format_request(user_text: str) -> dict:
    """
    解析用户"定位某段内容并重新排版"的请求。

    :param user_text: 用户输入（包含要定位的内容片段或描述，以及格式要求）
    :return: dict，包含：
        "locate_text": 要在文档中定位的关键文字（用于模糊搜索）
        "format_action": "match_context" | "explicit"（用周围段落格式 or 显式指定）
        "overrides": spec overrides（仅当 format_action == "explicit" 时有效）
        "description": 人类可读描述
    """
    if not user_text or not LLM_API_KEY:
        return {"locate_text": "", "format_action": "match_context", "overrides": {}, "description": ""}

    client = openai.AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=20.0)

    system_prompt = """你是文档定位排版解析器。
用户会提供一段他在文档里看到的文字（可能是原文引用或描述），并说明该段排版与周围不一致或需要修改。

请提取：
1. locate_text：用户想定位的关键内容片段（从用户消息中摘取最具代表性的原文片段，用于在文档中搜索）
2. format_action：
   - "match_context"：用户希望该段格式与周围其他段落保持一致（默认值）
   - "explicit"：用户指定了具体的格式参数
3. overrides：仅当 format_action 为 explicit 时才填写
4. description：一句话总结用户的需求

输出 JSON 格式：
{
  "locate_text": "需要定位的关键文字片段",
  "format_action": "match_context",
  "overrides": {},
  "description": "将【大四上学期】这一段重新排版，使其与周围段落格式一致"
}

注意：
- locate_text 要从用户原文中直接摘取，不要改写
- 如果用户引用了很长的内容（多行），只取前30个字符即可
- 只输出 JSON，不要任何解释"""

    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"用户请求：{user_text}"}
            ],
            temperature=0.1,
            max_tokens=400,
        )
        content = response.choices[0].message.content.strip()
        result = _extract_json(content)
        if result and "locate_text" in result:
            return result
    except Exception as e:
        print(f"❌ [Error] 解析定位排版意图异常: {e}")

    # fallback：尝试从文本中提取引号内容作为 locate_text
    import re as _re
    m = _re.search(r'[「『\u201c\u2018\'【](.*?)[」』\u201d\u2019\'】]', user_text)
    locate_text = m.group(1)[:50] if m else user_text[:30]
    return {
        "locate_text": locate_text,
        "format_action": "match_context",
        "overrides": {},
        "description": f"定位并重排：{locate_text[:20]}...",
    }


# ==========================================
# 文档审阅 + 增量排版解析（/r 命令）
# ==========================================
async def parse_review_request(user_text: str) -> dict:
    """
    解析 /r 命令中的用户要求。

    /r 命令用于：
    1. 对已上传文档进行排版审阅（识别格式不一致等问题）；
    2. 若用户同时附带了排版要求，则以增量方式只修改指定内容，不动用户未提及的部分。

    :param user_text: /r 命令后的内容（可为空字符串，表示纯审阅）
    :return: dict，包含：
        "has_requirements": bool — 是否有具体的增量排版要求
        "overrides": dict — spec 增量修改字段（仅 has_requirements=True 时有效）
        "hft_actions": dict — 页眉/页脚/页码/目录增量操作（仅 has_requirements=True 时有效）
        "description": str — 需求描述
    """
    if not user_text or not user_text.strip():
        return {"has_requirements": False, "overrides": {}, "hft_actions": {}, "description": ""}

    if not LLM_API_KEY:
        return {"has_requirements": False, "overrides": {}, "hft_actions": {}, "description": ""}

    client = openai.AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=20.0)

    system_prompt = """你是一个文档增量排版解析器。
用户发送的是"/r"审阅指令后的补充要求。你需要：
1. 判断用户是否提出了具体的排版修改要求（has_requirements）
2. 如果有，提取需要增量修改的内容（只改用户提到的，不动其他内容）

输出 JSON 格式：
{
  "has_requirements": true/false,
  "description": "一句话描述用户的增量要求",
  "overrides": {
    // 与 /f 命令相同的 spec 字段，只包含用户提到的
    // 例如：{"body": {"line_spacing": 1.5}}
  },
  "hft_actions": {
    // 页眉/页脚/页码/目录操作（仅在用户提到时才输出）
    // "header": {"text": "..."}, "footer": {"text": "..."},
    // "page_numbers": {"position": "footer", "start_at": 1},
    // "toc_format": {"font_size_pt": 12.0, "font_name_zh": "宋体"},
    // "header_remove_border": true  ← 用户要求删除页眉横线时输出此项
  }
}

注意：
- 增量排版原则：只改用户明确提到的内容，其他格式保持不变
- 如果用户仅说"帮我审阅"、"检查格式"等审阅类词语，has_requirements=false，overrides 和 hft_actions 为空
- 用户说"删除/去掉/移除页眉横线/分割线"时，hft_actions 中输出 "header_remove_border": true，并且 has_requirements=true
- 只输出 JSON，不要任何解释文字"""

    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"用户的增量要求：{user_text}"}
            ],
            temperature=0.1,
            max_tokens=400,
        )
        content = response.choices[0].message.content.strip()
        result = _extract_json(content)
        if result and "has_requirements" in result:
            # 补充本地规则解析的 HFT 操作（捕捉 LLM 遗漏的，如删除页眉横线）
            try:
                from agent.subagents.format_act.header_footer_toc import parse_header_footer_command as _local_hft_parse
                local_hft = _local_hft_parse(user_text)
                hft_dict = result.setdefault("hft_actions", {})
                for key, val in local_hft.items():
                    if key not in hft_dict:
                        hft_dict[key] = val
                if local_hft:
                    result["has_requirements"] = True
            except Exception:
                pass
            return result
    except Exception as e:
        print(f"❌ [Error] 解析审阅增量意图异常: {e}")

    # fallback：假设有增量要求，尝试解析 spec overrides + 本地 HFT
    raw = await parse_formatting_intent(user_text)
    overrides, _, hft_actions = _split_meta_fields(raw)
    try:
        from agent.subagents.format_act.header_footer_toc import parse_header_footer_command as _local_hft_parse
        local_hft = _local_hft_parse(user_text)
        for key, val in local_hft.items():
            if key not in hft_actions:
                hft_actions[key] = val
    except Exception:
        pass
    has_req = bool(overrides or hft_actions)
    return {
        "has_requirements": has_req,
        "overrides": overrides,
        "hft_actions": hft_actions,
        "description": user_text[:50] if has_req else "",
    }
