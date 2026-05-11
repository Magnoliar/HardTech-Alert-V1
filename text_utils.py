"""公共文本处理工具 — 黑话替换 + 物理净化"""
import logging
from llm_client import call_ai_api

logger = logging.getLogger(__name__)

BUZZWORD_REPLACER = {
    "范式转移": "逻辑更迭", "范式": "逻辑模型", "维度": "层面", "赋能": "驱动",
    "重构": "重组", "愿景": "目标", "助力": "帮助", "标志着": "意味着",
    "关键转折点": "重要节点", "见证了": "经历了", "协同": "配合",
    "值得注意的是": "实际情况是", "深入探讨": "详细拆解"
}


def surgical_purify(text):
    """黑话物理替换 + humanizer 轻度净化"""
    if not text:
        return ""
    for old, new in BUZZWORD_REPLACER.items():
        text = text.replace(old, new)
    try:
        from humanizer_plugin import HumanizerPlugin
        text = HumanizerPlugin.purify(text, intensity="light")
    except Exception:
        pass
    return text.strip()
