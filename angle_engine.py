import json
import os
import logging
from datetime import datetime, timedelta
from config_loader import load_config
from llm_client import call_ai_api, extract_json_from_text
from domain_config import DOMAIN

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
ANGLE_HISTORY_FILE = os.path.join(_DIR, "angle_history.json")


class AngleEngine:
    """
    内容角度引擎 — 根据今日新闻分布智能选择最适合的选题视角
    6 大预设视角: megatrend / policy / regional_industry / company_rd / tech_roadmap / product_roundup

    V2 增强: 选题历史感知 — 避免连续多天使用相同视角和雷同标题
    """

    def __init__(self):
        self.config = load_config()
        self.angles = DOMAIN["content_angles"]
        self._history = self._load_history()

    def select_and_plan(self, scored_news):
        """
        分析今日高分新闻，选择最优视角，生成选题包。
        返回 ArticleWriter 所需的 topic dict:
        { title_proposal, outline, angle, sources, related_news_ids }
        """
        if not scored_news:
            return None

        # 1. 分析新闻分布特征
        distribution = self._analyze_distribution(scored_news)

        # 2. AI 辅助选择视角 + 生成选题（带历史去重）
        topic = self._ai_plan(scored_news, distribution)

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

            # 简单实体频率统计
            for kw in p.get('keywords', []):
                entity_mentions[kw] = entity_mentions.get(kw, 0) + 1

        # 找出高频实体
        top_entities = sorted(entity_mentions.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "category_distribution": cat_counts,
            "top_tags": list(set(all_tags))[:15],
            "top_entities": top_entities,
            "total_news": len(news_list),
        }

    def _ai_plan(self, news_list, distribution):
        """调用 AI 选择视角并生成选题（带历史去重约束）"""
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

        # 格式化可选视角
        angle_options = []
        for angle_id, angle_def in self.angles.items():
            angle_options.append(
                f"- {angle_id}: {angle_def['name']} — {angle_def['description']} "
                f"(触发条件: {angle_def['trigger']})"
            )
        angles_str = "\n".join(angle_options)

        # === 历史去重约束 ===
        recent_angles = self._get_recent_angles(days=7)
        recent_titles = self._get_recent_titles(days=7)

        history_constraint = ""
        if recent_angles:
            angle_counts = {}
            for a in recent_angles:
                angle_counts[a] = angle_counts.get(a, 0) + 1
            overused = [f"{a}({c}次)" for a, c in angle_counts.items() if c >= 2]
            if overused:
                history_constraint += f"\n\n⚠️ **视角多样性约束（极其重要）**：\n"
                history_constraint += f"以下视角最近7天已被频繁使用，请优先选择其他视角：{', '.join(overused)}\n"

        if recent_titles:
            history_constraint += f"\n⚠️ **标题去重约束**：\n"
            history_constraint += f"最近7天的标题如下（请避免使用相似措辞、相同关键词组合）：\n"
            for t in recent_titles:
                history_constraint += f"  - 「{t}」\n"
            history_constraint += "请确保今天的标题在视角、措辞、核心关键词上都与上述标题明显不同。\n"

        user_prompt = f"""基于今日精选的硬科技新闻和新闻分布特征，请完成两个任务：

**任务一：选择最优视角**
从以下预设视角中选择 1 个最适合今日新闻的深度写作角度：
{angles_str}

**今日新闻分布特征**：
- 分类分布: {json.dumps(distribution['category_distribution'], ensure_ascii=False)}
- 高频标签: {distribution['top_tags']}
- 高频实体: {distribution['top_entities']}

**今日精选新闻**：
{news_str}
{history_constraint}
**任务二：生成选题方案**
基于选中的视角，策划 1 个有深度的内容选题。标题要有张力和数据感,适合硬科技创投人群阅读。

**输出要求（JSON 格式）**：
{{
    "selected_angle": "angle_id",
    "title_proposal": "建议标题",
    "angle": "核心切入角度描述（100字内）",
    "outline": ["章节1标题", "章节2标题", "章节3标题", "章节4标题"],
    "sources": ["建议信源类型"]
}}"""

        response = call_ai_api(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            description="Angle Selection & Planning"
        )

        if response:
            topic = extract_json_from_text(response)
            if topic:
                # 补充对应视角的模板大纲 (如果 AI 的大纲不够好可以兜底)
                angle_id = topic.get('selected_angle', 'megatrend')
                if angle_id in self.angles and not topic.get('outline'):
                    topic['outline'] = self.angles[angle_id]['outline_template']
                logger.info(f"📐 选定视角: {angle_id} | 选题: {topic.get('title_proposal')}")
                return topic

        # 兜底
        logger.warning("角度引擎 AI 规划失败，使用 megatrend 默认模板")
        return {
            "selected_angle": "megatrend",
            "title_proposal": f"硬科技产业动态深度观察",
            "angle": "基于今日多维信号的产业趋势拆解",
            "outline": self.angles["megatrend"]["outline_template"],
            "sources": ["行业研报", "外媒快讯"],
        }

    # ==================== 选题历史管理 ====================

    def _load_history(self):
        """加载选题历史"""
        if not os.path.exists(ANGLE_HISTORY_FILE):
            return {"selections": []}
        try:
            with open(ANGLE_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"selections": []}

    def _record_selection(self, topic):
        """记录本次选题到历史"""
        try:
            record = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "angle": topic.get("selected_angle", "unknown"),
                "title": topic.get("title_proposal", ""),
            }
            self._history.setdefault("selections", []).append(record)

            # 清理 30 天前的记录
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
        """获取最近 N 天使用的视角 ID 列表"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [
            s.get("angle", "")
            for s in self._history.get("selections", [])
            if s.get("date", "") >= cutoff
        ]

    def _get_recent_titles(self, days=7):
        """获取最近 N 天的文章标题列表"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [
            s.get("title", "")
            for s in self._history.get("selections", [])
            if s.get("date", "") >= cutoff and s.get("title")
        ]
