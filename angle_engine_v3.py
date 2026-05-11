import json
import os
import re
import logging
from datetime import datetime, timedelta
from config_loader import load_config
from llm_client import call_ai_api, extract_json_from_text
from domain_config import DOMAIN

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
ANGLE_HISTORY_FILE = os.path.join(_DIR, "angle_history.json")


class AngleEngineV3:
    """
    V3 论点驱动选题引擎
    核心改变：先发现论点，再构建论证弧；先有故事，再有章节。
    V2 兜底：如果论点发现失败，回退到 V2 的视角选择逻辑。
    """

    def __init__(self):
        self.config = load_config()
        self.angles = DOMAIN["content_angles"]
        self._history = self._load_history()

    def select_and_plan(self, scored_news):
        """
        分析今日高分新闻，发现中心论点，生成论证弧选题包。
        返回 topic dict:
        { thesis, narrative_thread, granularity, title_proposal, outline, counter_argument, selected_angle }
        """
        if not scored_news:
            return None

        # 1. 分析新闻分布特征
        distribution = self._analyze_distribution(scored_news)

        # 2. V3: 论点发现 + 论证弧构建
        topic = self._thesis_driven_plan(scored_news, distribution)

        # 3. 记录本次选题到历史
        if topic:
            self._record_selection(topic)

        return topic

    def _analyze_distribution(self, news_list):
        """分析分类与标签分布，辅助 AI 决策"""
        cat_counts = {}
        all_tags = []
        entity_mentions = {}

        for item in news_list:
            p = item.get('parsed', {})
            cat = p.get('category', '其他')
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            all_tags.extend(p.get('tags', []))

            for kw in p.get('keywords', []):
                entity_mentions[kw] = entity_mentions.get(kw, 0) + 1

        top_entities = sorted(entity_mentions.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "category_distribution": cat_counts,
            "top_tags": list(set(all_tags))[:15],
            "top_entities": top_entities,
            "total_news": len(news_list),
        }

    def _thesis_driven_plan(self, news_list, distribution):
        """V3 核心：论点发现 + 论证弧构建"""
        from domain_config import get_prompt
        system_prompt = get_prompt('system_prompt')

        # 格式化新闻摘要
        news_summary = []
        for item in news_list[:15]:
            p = item.get('parsed', {})
            news_summary.append(
                f"ID {item.get('final_index')}. [{p.get('category')}] "
                f"{p.get('title')} ({p.get('score')}分) - {p.get('reasoning', '')}"
            )
        news_str = "\n".join(news_summary)

        # 历史去重约束
        history_constraint = self._build_history_constraint()

        user_prompt = f"""基于今日精选的硬科技新闻，请完成以下三个任务：

**任务零：发现今天值得讲的故事（核心论点）**
仔细阅读以下新闻，找到一个有张力的、值得用 4000 字深度论述的中心论点。

好的论点特征：
- 有争议性：不是"行业在发展"这种废话，而是"X正在侵蚀Y的护城河"
- 有证据链：至少 2-3 条新闻能为这个论点提供不同角度的证据
- 有因果关系：不是并列罗列，而是"A导致B，B触发C"
- 有具体实体：必须包含公司名、技术名或数据，禁止空泛的"产业趋势"

好的论点举例：
- "特斯拉自建晶圆厂意味着英伟达对 CoWoS 的议价权正在被分散"
- "出口管制从终端禁运升级到通道封堵，倒逼国产算力集群的交付闭环"
- "比亚迪的垂直整合模式让欧盟关税形同虚设"

差的论点举例：
- "算力基础设施面临物理瓶颈"（没有争议性）
- "半导体产业链正在重构"（太空泛）
- "AI技术持续发展"（废话）

**任务一：确定叙事粒度**
根据今日新闻的实际特征，选择最合适的叙事粒度：
- 宏观（产业大势）：多条新闻指向同一方向的产业趋势
- 中观（技术脉络/跨域碰撞）：聚焦某条技术线或领域交叉
- 微观（公司深描/事件催化）：围绕一家公司或一个事件

**任务二：围绕论点构建论证弧**
基于你发现的论点，生成论证大纲。大纲不是模板化的章节列表，而是论证这个论点的逻辑步骤：
- 开篇：抛出论点（用一个具体事件或数据切入，不要宏大叙述）
- 中间：2-4 个论证角度（每个角度用一条或多条新闻作为证据）
- 可选：反驳/复杂性（市场共识可能认为的反面观点）
- 收束：对创投的含义（具体、可操作，不要空泛的"关注XXX赛道"）

**今日新闻分布特征**：
- 分类分布: {json.dumps(distribution['category_distribution'], ensure_ascii=False)}
- 高频标签: {distribution['top_tags']}
- 高频实体: {distribution['top_entities']}

**今日精选新闻**：
{news_str}
{history_constraint}

**输出要求（JSON 格式）**：
{{
    "thesis": "中心论点（100字以内，必须有主语、谓语、宾语和冲突/张力）",
    "narrative_thread": "贯穿全文的暗线（一句话，每章都应与此产生关联）",
    "granularity": "宏观/中观/微观",
    "selected_angle": "最匹配的视角ID（megatrend/policy/regional_industry/company_rd/tech_roadmap/product_roundup/event_catalyst/cross_domain）",
    "title_proposal": "建议标题（必须包含具体实体名，有张力）",
    "outline": ["角度1标题（含实体名）", "角度2标题", ...],
    "counter_argument": "可能的反驳观点（可选，如有则增加文章思辨深度）",
    "sources": ["建议信源类型"]
}}"""

        response = call_ai_api(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            description="V3 Thesis Discovery & Planning"
        )

        if response:
            topic = extract_json_from_text(response)
            if topic and self._validate_topic(topic):
                logger.info(
                    f"📐 V3 论点发现成功: {topic.get('thesis', '')[:60]}... | "
                    f"粒度: {topic.get('granularity')} | 标题: {topic.get('title_proposal')}"
                )
                # 兜底：如果 outline 为空，用对应视角的模板
                angle_id = topic.get('selected_angle', 'megatrend')
                if angle_id in self.angles and not topic.get('outline'):
                    topic['outline'] = self.angles[angle_id]['outline_template']
                return topic

        # 兜底：回退到 V2 逻辑
        logger.warning("V3 论点发现失败，回退到 V2 视角选择")
        return self._v2_fallback(news_list, distribution)

    def _validate_topic(self, topic):
        """校验论点质量"""
        thesis = topic.get('thesis', '')
        if not thesis or len(thesis) < 15:
            return False

        # 论点必须包含至少一个实体名或数字
        has_entity = bool(re.search(r'[A-Z][a-zA-Z]+|[一-龥]{2,}(?:科技|集团|半导体|电子|能源|智能|电池|芯片)', thesis))
        has_number = bool(re.search(r'\d+', thesis))
        if not has_entity and not has_number:
            logger.warning(f"论点缺少实体或数据: {thesis}")
            return False

        # 论点不能太空泛
        vague_patterns = ["产业发展", "技术进步", "行业趋势", "持续增长", "未来可期"]
        if any(vague in thesis for vague in vague_patterns):
            logger.warning(f"论点太空泛: {thesis}")
            return False

        return True

    def _v2_fallback(self, news_list, distribution):
        """V2 兜底逻辑：使用 V2 的视角选择"""
        try:
            from angle_engine import AngleEngine
            v2_engine = AngleEngine()
            return v2_engine.select_and_plan(news_list)
        except Exception as e:
            logger.error(f"V2 兜底也失败了: {e}")
            return {
                "selected_angle": "megatrend",
                "title_proposal": "硬科技产业动态深度观察",
                "angle": "基于今日多维信号的产业趋势拆解",
                "outline": self.angles["megatrend"]["outline_template"],
                "sources": ["行业研报", "外媒快讯"],
                "thesis": "",
                "narrative_thread": "",
                "granularity": "宏观",
            }

    def _build_history_constraint(self):
        """构建历史去重约束"""
        recent_angles = self._get_recent_angles(days=7)
        recent_titles = self._get_recent_titles(days=7)

        constraint = ""
        if recent_angles:
            angle_counts = {}
            for a in recent_angles:
                angle_counts[a] = angle_counts.get(a, 0) + 1
            overused = [f"{a}({c}次)" for a, c in angle_counts.items() if c >= 2]
            if overused:
                constraint += f"\n\n⚠️ **视角多样性约束**：以下视角最近7天已被频繁使用，请优先选择其他视角：{', '.join(overused)}\n"

        if recent_titles:
            constraint += f"\n⚠️ **标题去重约束**：最近7天的标题如下（请避免相似措辞）：\n"
            for t in recent_titles:
                constraint += f"  - 「{t}」\n"
            constraint += "请确保今天的标题在视角、措辞、核心关键词上都与上述标题明显不同。\n"

        return constraint

    # ==================== 选题历史管理 ====================

    def _load_history(self):
        if not os.path.exists(ANGLE_HISTORY_FILE):
            return {"selections": []}
        try:
            with open(ANGLE_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"selections": []}

    def _record_selection(self, topic):
        try:
            record = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "angle": topic.get("selected_angle", "unknown"),
                "title": topic.get("title_proposal", ""),
                "thesis": topic.get("thesis", ""),
            }
            self._history.setdefault("selections", []).append(record)

            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            self._history["selections"] = [
                s for s in self._history["selections"]
                if s.get("date", "") >= cutoff
            ]

            with open(ANGLE_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.warning(f"记录选题历史失败（不影响主流程）: {e}")

    def _get_recent_angles(self, days=7):
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [
            s.get("angle", "")
            for s in self._history.get("selections", [])
            if s.get("date", "") >= cutoff
        ]

    def _get_recent_titles(self, days=7):
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [
            s.get("title", "")
            for s in self._history.get("selections", [])
            if s.get("date", "") >= cutoff and s.get("title")
        ]


if __name__ == "__main__":
    pass
