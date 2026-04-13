# agent/intent_classifier.py
"""
意图分类器：在用户输入到达 LLM 排版解析器之前，快速判断意图类型。
优先使用关键词规则分类，规则无法确定时才调用 LLM。

Phase 3 增强：
- 新增 VISUAL_REVIEW 意图
- LLM fallback：规则置信度低于阈值时调用 LLM 做二次判定
- IntentContext：多轮对话上下文记忆
- classify_intent_enhanced：综合规则 + LLM + 上下文的入口
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# LLM fallback 的置信度阈值：规则分类低于此值时触发 LLM
LLM_FALLBACK_THRESHOLD = 0.7

# 上下文记忆保留的最大轮数
MAX_CONTEXT_TURNS = 10


class IntentType(str, Enum):
    FORMAT = "format"              # 排版需求
    CHAT = "chat"                  # 闲聊 / 感谢 / 问候
    QUERY = "query"                # 询问排版状态 / 效果
    FEEDBACK = "feedback"          # 对校对建议的反馈
    VISUAL_REVIEW = "visual_review"  # 视觉审查需求（Phase 3）
    AUDIT = "audit"                # 文档排版审阅/一致性检查
    PARTIAL_FORMAT = "partial_format"  # 局部/定向排版（只改某一项）
    LOCATE_FORMAT = "locate_format"    # 定位特定内容并重新排版
    HEADER_FOOTER_TOC = "header_footer_toc"  # 页眉/页脚/页码/目录操作
    AMBIGUOUS = "ambiguous"        # 模糊需求，需追问


class IntentResult(BaseModel):
    intent: IntentType
    confidence: float  # 0.0 - 1.0
    message: Optional[str] = None  # 给用户的追问或回复建议
    source: str = "rule"  # "rule" | "llm" | "context" — 分类来源


# ---------------------------------------------------------------------------
# Phase 3: 意图上下文记忆（多轮对话场景支持）
# ---------------------------------------------------------------------------

@dataclass
class IntentContext:
    """
    多轮对话意图上下文，维护最近 N 轮的意图历史。

    用途：
    - 当用户连续讨论排版，短输入（如"行距也改一下"）可根据上下文判定为 FORMAT
    - 当用户刚看完视觉审查，后续简短回复可判定为 FEEDBACK
    """

    # 意图历史（最新在末尾）
    history: List[IntentResult] = field(default_factory=list)
    # 当前是否有待处理的校对建议
    has_pending_proofread: bool = False
    # 当前是否有待处理的视觉审查结果
    has_pending_visual_review: bool = False
    # 当前文档路径（如有）
    current_doc_path: Optional[str] = None

    def add(self, result: IntentResult) -> None:
        """记录一轮意图结果"""
        self.history.append(result)
        if len(self.history) > MAX_CONTEXT_TURNS:
            self.history = self.history[-MAX_CONTEXT_TURNS:]

    @property
    def last_intent(self) -> Optional[IntentType]:
        """最近一轮的意图类型"""
        return self.history[-1].intent if self.history else None

    @property
    def recent_format_count(self) -> int:
        """最近连续 FORMAT 意图的次数"""
        count = 0
        for r in reversed(self.history):
            if r.intent == IntentType.FORMAT:
                count += 1
            else:
                break
        return count


# =========================================================
# 关键词规则库
# =========================================================

# 排版相关关键词（高精度）
_FORMAT_KEYWORDS = [
    # 字体
    r"字体", r"宋体", r"黑体", r"楷体", r"仿宋", r"Times\s*New\s*Roman", r"Arial",
    # 字号
    r"字号", r"小四", r"三号", r"四号", r"五号", r"小三", r"小二",
    r"font.?size", r"\d+\s*pt", r"\d+\s*磅",
    # 行距 / 段距
    r"行距", r"行间距", r"段[前后]", r"段落[前后]?.*磅",
    r"倍行距", r"固定值", r"line.?spacing",
    # 缩进
    r"首行缩进", r"缩进", r"indent",
    # 对齐
    r"对齐", r"居中", r"左对齐", r"右对齐", r"两端对齐",
    # 格式类型
    r"排版", r"排一下", r"格式", r"论文格式", r"公文格式", r"APA",
    r"毕业论文", r"学位论文",
    # 样式
    r"加粗", r"斜体", r"下划线", r"颜色",
    # 标题
    r"标题", r"一级标题", r"二级标题",
    # 页面
    r"页边距", r"页眉", r"页脚",
]

# 闲聊关键词
_CHAT_KEYWORDS = [
    r"^谢谢[了你]?$", r"^感谢$", r"^好的$", r"^太好了$",
    r"^你好[啊呀]?$", r"^嗨$", r"^hi$", r"^hello$",
    r"^拜拜$", r"^再见$", r"^ok$", r"^好[的嘞]$",
    r"^明白了$", r"^知道了$", r"^收到$",
    r"^没[问事]?了$", r"^可以了$", r"^没有了$",
    r"^厉害$", r"^牛$", r"^棒$",
]

# 询问关键词
_QUERY_KEYWORDS = [
    r"现在.*什么[样格]", r"效果.*怎么样", r"改了.*什么",
    r"哪些.*修改", r"看[一看]?.*结果", r"报告",
    r"改了多少", r"格式化.*了吗",
]

# 反馈关键词（对校对建议的回复）
_FEEDBACK_KEYWORDS = [
    r"全部接受", r"全部拒绝", r"都[不别]改",
    r"接受", r"拒绝", r"第[0-9一二三四五六七八九十]+",
    r"保留.*其余", r"只改.*第",
]

# 视觉审查关键词（Phase 3）- 仅匹配明确涉及视觉美观的关键词
_VISUAL_REVIEW_KEYWORDS = [
    r"视觉", r"审查", r"看看排版", r"排版.*好不好",
    r"视觉效果", r"跑偏了", r"不好看", r"看看.*效果",
    r"检查视觉", r"视觉检查", r"美观度",
]

# 文档审阅/一致性检查关键词
_AUDIT_KEYWORDS = [
    r"审阅", r"审核", r"检查.*格式", r"格式.*检查", r"一致性",
    r"有没有.*问题", r"格式.*错误", r"排版.*错误", r"错误.*排版",
    r"哪[里些].*不[对一]", r"帮.*看看", r"查一下", r"查.*排版",
    r"格式.*规范", r"不规范", r"不符合", r"漏.*排版", r"忘.*排版",
    r"有[些什么哪].*地方.*不[对统一规范]", r"检查.*一下",
    r"括号.*混用", r"标点.*混用", r"字号.*不[同一致]",
    r"标题.*不[同一]", r"是否.*规范",
]

# 局部/定向排版关键词（只改某一项）
_PARTIAL_FORMAT_KEYWORDS = [
    r"只[改修调]", r"仅[改修调]", r"只需要", r"只想",
    r"单独[改修调]", r"只把.*改", r"只[要需]改",
    r"不用全部", r"不用重新排版", r"不需要全文",
    r"只修改.*正文", r"把.*[改修]成", r"只调整",
    r"不[要需]改.*其他", r"只针对.*[改修调]",
]

# 页眉/页脚/页码/目录关键词
_HEADER_FOOTER_TOC_KEYWORDS = [
    r"页眉", r"页脚", r"页码", r"目录",
    r"header", r"footer", r"page\s*number",
    r"从第.*页.*页码", r"增加.*目录", r"插入.*目录", r"添加.*目录",
    r"增加.*页码", r"插入.*页码", r"添加.*页码",
    r"增加.*页眉", r"增加.*页脚", r"设置.*页眉", r"设置.*页脚",
]

# 定位并重新排版关键词
_LOCATE_FORMAT_KEYWORDS = [
    r"这.*部分.*怎么.*排版", r"这.*段.*格式.*不[同对]",
    r"这里.*排版.*不[同对]", r"和.*其他.*不[同一]",
    r"和.*其他.*地方.*不[同一]", r"重新排版.*这",
    r"这.*排版.*不.*[对规范统一]",
    r"你.*检查.*[一下这]", r"[找定]位.*重新排",
    r"这一部分.*怎么.*和", r"这部分.*格式",
    r"这.*格式.*[不对不一致]",
]


def _match_any(text: str, patterns: list[str]) -> bool:
    """检查文本是否匹配任一模式"""
    text_lower = text.strip().lower()
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


def classify_intent(user_text: str, has_pending_proofread: bool = False) -> IntentResult:
    """
    快速分类用户意图。

    :param user_text: 用户输入文本
    :param has_pending_proofread: 是否有待处理的校对建议（影响 feedback 判定）
    :return: IntentResult 包含意图类型和置信度
    """
    text = (user_text or "").strip()

    # 空输入
    if not text:
        return IntentResult(intent=IntentType.CHAT, confidence=1.0)

    # 极短文本优先检查闲聊
    if len(text) <= 10 and _match_any(text, _CHAT_KEYWORDS):
        return IntentResult(
            intent=IntentType.CHAT,
            confidence=0.95,
            message=_get_chat_reply(text),
        )

    # 有待处理的校对时，检查反馈意图
    if has_pending_proofread and _match_any(text, _FEEDBACK_KEYWORDS):
        return IntentResult(intent=IntentType.FEEDBACK, confidence=0.9)

    # 页眉/页脚/页码/目录操作（最具体，最先检查）
    if _match_any(text, _HEADER_FOOTER_TOC_KEYWORDS):
        return IntentResult(intent=IntentType.HEADER_FOOTER_TOC, confidence=0.95)

    # 定位特定内容并重排（在 VISUAL_REVIEW 之前，避免"检查"词误判）
    if _match_any(text, _LOCATE_FORMAT_KEYWORDS):
        return IntentResult(intent=IntentType.LOCATE_FORMAT, confidence=0.85)

    # 视觉审查关键词（在 AUDIT 之前，保持原有优先级）
    if _match_any(text, _VISUAL_REVIEW_KEYWORDS):
        return IntentResult(intent=IntentType.VISUAL_REVIEW, confidence=0.9)

    # 文档审阅/一致性检查
    if _match_any(text, _AUDIT_KEYWORDS):
        return IntentResult(intent=IntentType.AUDIT, confidence=0.9)

    # 局部/定向排版（只改某一项）
    if _match_any(text, _PARTIAL_FORMAT_KEYWORDS):
        return IntentResult(intent=IntentType.PARTIAL_FORMAT, confidence=0.9)

    # 排版关键词
    if _match_any(text, _FORMAT_KEYWORDS):
        return IntentResult(intent=IntentType.FORMAT, confidence=0.95)

    # 查询关键词
    if _match_any(text, _QUERY_KEYWORDS):
        return IntentResult(intent=IntentType.QUERY, confidence=0.85)

    # 文本较长（>20字）且不匹配以上任何，倾向于排版需求
    if len(text) > 20:
        return IntentResult(
            intent=IntentType.FORMAT,
            confidence=0.6,
            message="检测到可能的排版需求，正在解析...",
        )

    # 真正无法判断
    return IntentResult(
        intent=IntentType.AMBIGUOUS,
        confidence=0.4,
        message="请问您是想对文档进行排版调整吗？请描述具体的排版需求（如字体、字号、行距等）。",
    )


def classify_intent_with_llm(user_text: str, context: Optional[IntentContext] = None) -> IntentResult:
    """
    使用 LLM 作为 fallback 分类意图（常用于复杂多轮长难句匹配）。
    """
    from agent.subagents.validate_review.api import LLMClient
    
    system_prompt = (
        "你是一个意图分类助手。请根据用户的输入（以及可选的上下文）判断其真实的意图。\n"
        "可选的意图类型包括：\n"
        "  - format：用户想要提出、修改具体的排版规则，或提供样板文档。\n"
        "  - chat：闲聊、感谢、问候、结束对话。\n"
        "  - query：询问当前的排版进度、效果，或索要排版报告。\n"
        "  - feedback：针对之前的修改建议，表示接受或拒绝。\n"
        "  - visual_review：要求从视觉外观、美观度上对现有的排版结果进行审查。\n"
        "  - audit：要求对文档进行排版一致性审阅，检查格式是否统一（如括号混用、标题格式不一、字号不一等）。\n"
        "  - partial_format：用户只想修改文档的某一项具体属性（如只改行距、只改正文字号），而不是全文重新排版。\n"
        "  - locate_format：用户指出文档中某段或某部分排版与其他地方不一致，要求定位并修复。\n"
        "  - header_footer_toc：用户要求添加/修改页眉、页脚、页码或目录。\n"
        "  - ambiguous：需求过于模糊，无法归类。\n\n"
        "必须严格输出 JSON 格式，包含：{'intent': 'xxx', 'confidence': 0.0-1.0}。"
    )
    
    # 组装上下文说明
    ctx_info = ""
    if context and context.history:
        recent = [r.intent.value for r in context.history[-3:]]
        ctx_info = f"\n最近的几轮意图历史为: {', '.join(recent)}。"
        if context.has_pending_proofread:
            ctx_info += " 当前有待确认的校对建议。"
        if context.has_pending_visual_review:
            ctx_info += " 当前有刚生成的视觉审查报告。"

    user_prompt = f"用户输入文本：\"{user_text}\"{ctx_info}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        client = LLMClient()
        raw = client._execute_chat_completion(messages, timeout=10)
        data = json.loads(client._normalize_json_text(raw))
        intent_val = data.get("intent", "ambiguous")
        try:
            intent = IntentType(intent_val)
        except ValueError:
            intent = IntentType.AMBIGUOUS
        
        confidence = float(data.get("confidence", 0.5))
        return IntentResult(intent=intent, confidence=confidence, source="llm")
    except Exception as e:
        logger.warning(f"LLM 意图分类 fallback 失败: {e}")
        return IntentResult(intent=IntentType.AMBIGUOUS, confidence=0.0, source="llm_error")


def classify_intent_enhanced(user_text: str, context: Optional[IntentContext] = None) -> IntentResult:
    """
    综合规则 + LLM + 上下文的增强版分类入口。
    
    1. 优先使用快速规则分类
    2. 结合上下文（如果规则信心足但存在更近的上下文引导定势）
    3. 如果规则无法确定且不是极短闲聊，则 fallback 到 LLM
    """
    text = (user_text or "").strip()
    
    # 基本规则分类
    rule_res = classify_intent(text, has_pending_proofread=context.has_pending_proofread if context else False)
    
    # 结合视觉反馈上下文修正：如果刚做完视觉审查，且用户回复简短，很可能是反馈或闲聊确认
    if context and context.has_pending_visual_review and len(text) < 15:
        if rule_res.intent in [IntentType.FORMAT, IntentType.AMBIGUOUS]:  
            # 若不是明确的排版词 或 很短的反馈
            if not _match_any(text, _FORMAT_KEYWORDS + _VISUAL_REVIEW_KEYWORDS):
                 rule_res.intent = IntentType.FEEDBACK
                 rule_res.source = "context"
                 rule_res.confidence = 0.8
    
    # Fallback to LLM 如果置信度低且不是明显闲聊
    if rule_res.confidence < LLM_FALLBACK_THRESHOLD and len(text) > 4:
        llm_res = classify_intent_with_llm(text, context)
        if llm_res.confidence > rule_res.confidence:
            return llm_res
            
    return rule_res


# =========================================================
# 中文字号预解析（减少 LLM 数值映射错误）
# =========================================================

CHINESE_FONT_SIZE_MAP = {
    "初号": 42.0, "小初": 36.0,
    "一号": 26.0, "小一": 24.0,
    "二号": 22.0, "小二": 18.0,
    "三号": 16.0, "小三": 15.0,
    "四号": 14.0, "小四": 12.0,
    "五号": 10.5, "小五": 9.0,
    "六号": 7.5,  "小六": 6.5,
    "七号": 5.5,  "八号": 5.0,
}


def preprocess_chinese_sizes(text: str) -> str:
    """
    将用户文本中的中文字号预转换为磅值标注，降低 LLM 犯错概率。
    例如："正文小四" → "正文小四(12.0pt)"
    """
    result = text
    # 先处理"小X"（避免"小四"被"四号"匹配覆盖）
    for name in sorted(CHINESE_FONT_SIZE_MAP.keys(), key=len, reverse=True):
        pt = CHINESE_FONT_SIZE_MAP[name]
        # 在字号名后追加磅值标注，但不重复追加
        pattern = re.compile(rf"({re.escape(name)})(?!\s*[\(（]\d)")
        result = pattern.sub(rf"\1({pt}pt)", result)
    return result


# =========================================================
# 闲聊回复模板
# =========================================================

def _get_chat_reply(text: str) -> str:
    text_lower = text.strip().lower()
    if any(w in text_lower for w in ["谢谢", "感谢", "太好了"]):
        return "不客气！如果还有排版需求，随时告诉我 😊"
    if any(w in text_lower for w in ["你好", "嗨", "hi", "hello"]):
        return "你好！我是文档排版助手，可以帮你调整 Word 文档的格式。请上传 .docx 文件或告诉我你的排版需求。"
    if any(w in text_lower for w in ["拜拜", "再见"]):
        return "再见！下次有排版需求欢迎找我 👋"
    return "好的！有需要随时找我 😊"
