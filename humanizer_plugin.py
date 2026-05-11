import re
import logging
from llm_client import call_ai_api

logger = logging.getLogger(__name__)

class HumanizerPlugin:
    """
    完全体去 AI 化外挂插件 (Based on blader/humanizer 2.2.0)
    集成 24 种特征模式识别与灵魂注入逻辑
    """

    # 严禁使用的 AI 黑话
    AI_BLACKLIST = [
        "范式", "维度", "重构", "赋能", "愿景", "助力", "见证", "标志着",
        "关键转折点", "景观", "蓝图", "协同", "深入探讨", "值得注意的是"
    ]

    @staticmethod
    def purify(text, intensity="light"):
        if not text: return ""

        # 1. 基础规则过滤 (静态替换)
        text = HumanizerPlugin._static_rules(text)

        # 2. 物理去噪：去除所有双引号、单引号和括号内容（包括中英文）
        text = re.sub(r'[“”‘’]', '', text)
        text = re.sub(r'[\(（](?=[^a-zA-Z0-9])[^)）]{50,}[\)）]', '', text)

        if intensity == "heavy":
            return HumanizerPlugin._soulful_rewrite(text)

        return text.strip()

    @staticmethod
    def _static_rules(text):
        # A. 清除样式污染 (加粗、破折号滥用、Emoji)
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # 去加粗
        text = re.sub(r'[\u2600-\u27BF\U0001f300-\U0001faff]', '', text)  # 去 Emoji
        text = text.replace('\u2014', ',').replace('\u201c', '"').replace('\u201d', '"')  # 标点标准化

        # B. 剔除 AI 标志性废话短语
        void_phrases = {
            r'值得注意的是': '',
            r'不仅如此': '而且',
            r'总而言之': '',
            r'在.*?的背景下': '',
            r'标志着.*?的开端': '意味着',
            r'见证了': '经历了',
            r'致力于': '要做',
            r'旨在': '为了',
            r'作为.*?的见证': '是',
            r'发挥了关键作用': '很重要',
            r'提供了一个.*?的平台': '让'
        }
        for pattern, replacement in void_phrases.items():
            text = re.sub(pattern, replacement, text)


        # C. AI 黑话检查
        for word in HumanizerPlugin.AI_BLACKLIST:
            if word in text:
                logger.debug(f"AI黑话命中: {word}")
                text = text.replace(word, '')
        return text

    @staticmethod
    def _soulful_rewrite(text):
        """完全依照 blader/humanizer 的 6 步流程进行深度去 AI 化重写"""
        logger.info("🎭 正在进行深度去 AI 化重写...")

        system_prompt = """
        你是一位在硬科技产业深耕多年的资深行业观察者。
        你的任务是把一段充满 AI 腔调的文字，重写成那种"一眼看过去就是真人在说话"的内容。

        【脱脂目标】
        1. 物理净化：禁止加粗、破折号、(括号)、双引号。文字要像水一样流利。
        2. 词汇禁令：绝对禁止使用"标志着"、"关键转折点"、"景观"、"蓝图"、"愿景"、"助力"。
        3. 系词修正：不要用"作为...的见证"或"服务于"，直接说"是"。

        【注入人类视角】
        - 有观点就直说。用事实和数据说话。
        - 节奏感：长短句错落。关键金句单独成行。
        - 别讲大道理：只拆解利益和逻辑。
        - 像真人在说话：适度使用"我"、"我们"，文字里要透着那种看透本质的清醒。

        【输出要求】
        直接输出最终正文，不要任何解释，不要包含 [角度] [标题] 等标签。
        """

        user_prompt = f"请彻底去 AI 化并润色以下内容，赋予它人类的观点和节奏感：\n\n{text}"

        # 执行双重 Audit 流程
        draft = call_ai_api([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ], description="Soulful Rewrite (Draft)")

        if not draft:
            return text  # 第一次 LLM 失败，返回原文

        # 第二次 Pass: 寻找残留 AI 痕迹
        audit_query = f"以下这段文字中，还有哪些地方看起来明显是 AI 生成的？请指出并给出终极修正版。\n\n{draft}"
        final_response = call_ai_api([
            {"role": "system", "content": "你是一位极其苛刻的去 AI 审计专家。直接给出最终修正版。"},
            {"role": "user", "content": audit_query}
        ], description="Soulful Rewrite (Audit Pass)")

        return final_response or draft

if __name__ == "__main__": pass
