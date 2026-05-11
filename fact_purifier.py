import logging
from llm_client import call_ai_api

logger = logging.getLogger(__name__)

class FactPurifier:
    """
    智能事实审计站 (V2.0 - 实体优先版)
    目标：提取确切的公司、产品型号、具体数据，拒绝模棱两可的描述
    """
    
    @staticmethod
    def purify(raw_search_context, topic_title, current_date):
        if not raw_search_context: return ""
        
        logger.info(f"🔍 正在执行‘实体优先’审计：针对《{topic_title}》...")
        
        system_prompt = f"""
        你是一位顶尖的商业情报调查员。
        今天是 {current_date}。
        
        【你的任务】
        从杂乱的素材中提炼出一份《核心实体清单》。
        
        【提取准则】
        1. 必须包含具体的【厂商名】。
        2. 必须包含确切的【产品型号/技术名称】（如果素材中没有提到具体型号，请如实记录其展示的领域）。
        3. 必须包含【具体动作/数据】（如：发布了、展示了、宣布降价 X%）。
        4. 严禁扣帽子：不要将普通高端产品自动冠以“AI”、“突破性”等修饰语，除非素材原文如此。
        5. 时间对齐：基于选题意图，自动过滤不相关的往届旧闻。
        
        【输出格式】
        请输出一份清单，格式如下：
        [公司名] - [具体产品/技术/动作] - [数据/细节证据] - [时间戳属性]
        """
        
        user_prompt = f"请审计以下原始调研素材，提炼《{topic_title}》的实体清单：\n\n{raw_search_context[:8000]}"
        
        purified_facts = call_ai_api([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ], description="Entity Extraction", custom_timeout=150)
        
        return purified_facts or "（审计未发现有效实体）"
