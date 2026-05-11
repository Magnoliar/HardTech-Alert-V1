# keyword_scheduler.py — 关键词智能调度器
# ============================================================
# 三层优化:
#   A. 轮转调度: core 分 daily/rotating, extended 激活, entities 随机轮转
#   B. 表现自适应: 根据 topic_history 调整搜索频率和结果数
#   C. AI 动态关键词: 每天 1 次 LLM 调用建议新关键词
#
# 鲁棒性:
#   - topic_tracker 不可用 → 方案 B 退化为固定配额
#   - LLM 调用失败 → 方案 C 静默跳过，不影响 A/B
#   - dynamic_keywords.json 损坏 → 自动忽略，仅用静态关键词
# ============================================================

import json
import os
import hashlib
import logging
import random
from datetime import datetime, timedelta
from domain_config import DOMAIN

logger = logging.getLogger(__name__)

DYNAMIC_KW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dynamic_keywords.json')

# ===================== 每日必搜关键词 =====================
# 高时效性: 政策/投融资/突发类，错过一天就可能过时
# 不在此集合中的 core 关键词自动归入轮转池
DAILY_KEYWORDS = {
    "美国出口管制 芯片",
    "实体清单 半导体",
    "芯片法案 CHIPS Act",
    "半导体 投融资事件",
    "反垄断 科技企业",
    "算力集群 数据中心",       # AI 算力市场变化快
    "固态电池 量产",            # 量产进度日更
    "人形机器人 具身智能",      # 当前风口话题
    "eVTOL 低空飞行器 适航",   # 政策审批时效性强
}

# 每个领域每天从轮转池中选多少个关键词
ROTATING_PICK_COUNT = 3
# 每个领域每天选多少个实体
ENTITY_PICK_COUNT = 2
# 动态关键词保留天数
DYNAMIC_KW_RETENTION_DAYS = 3
# 每个领域最多使用多少个动态关键词
DYNAMIC_KW_PER_DOMAIN = 2


class KeywordScheduler:
    """
    智能关键词调度器

    调用方式:
        scheduler = KeywordScheduler(tracker=topic_tracker_instance)
        plan = scheduler.get_todays_search_plan()
        # plan = {
        #     "半导体与元器件": {
        #         "queries": [("先进封装 CoWoS", 3), ("EDA工具", 5), ...],
        #         "entities": ["ASML", "Samsung Foundry"]
        #     }, ...
        # }
    """

    def __init__(self, tracker=None):
        """
        参数:
            tracker: TopicTracker 实例（可选）。提供时启用方案 B（自适应配额）。
        """
        self.tracker = tracker
        self._dynamic_keywords = self._load_dynamic_keywords()

    # ===================== 方案 A: 轮转调度 =====================

    def get_todays_search_plan(self):
        """
        生成今天的搜索计划。

        返回: dict — {domain_key: {"queries": [(keyword, max_results), ...], "entities": [str, ...]}}
        """
        plan = {}
        today_str = datetime.now().strftime("%Y-%m-%d")
        # 用日期生成确定性种子 → 同一天多次调用结果一致
        day_seed = int(hashlib.md5(today_str.encode()).hexdigest()[:8], 16)

        total_queries = 0
        total_entities = 0

        for domain_key, keywords in DOMAIN["keyword_matrix"].items():
            domain_seed = day_seed + int(hashlib.md5(domain_key.encode()).hexdigest()[:8], 16)

            core = keywords.get("core", [])
            extended = keywords.get("extended", [])
            entities = keywords.get("entities", [])

            queries = []

            # --- 1. 每日必搜 (DAILY_KEYWORDS 中的 core) ---
            daily = [q for q in core if q in DAILY_KEYWORDS]
            for q in daily:
                max_r = self._adaptive_max_results(q)
                queries.append((q, max_r))

            # --- 2. 轮转池: 非必搜 core + 全部 extended ---
            rotating_pool = [q for q in core if q not in DAILY_KEYWORDS] + extended
            n_select = min(ROTATING_PICK_COUNT, len(rotating_pool))
            if n_select > 0:
                selected_rotating = self._deterministic_select(rotating_pool, n_select, domain_seed)
                for q in selected_rotating:
                    max_r = self._adaptive_max_results(q)
                    queries.append((q, max_r))

            # --- 3. 实体轮转 (随机选，而非总是前N个) ---
            n_entities = min(ENTITY_PICK_COUNT, len(entities))
            selected_entities = []
            if n_entities > 0:
                selected_entities = self._deterministic_select(entities, n_entities, domain_seed + 1)

            # --- 4. 动态关键词 (AI 建议的) ---
            dynamic = self._dynamic_keywords.get(domain_key, [])
            for q in dynamic[:DYNAMIC_KW_PER_DOMAIN]:
                max_r = self._adaptive_max_results(q)
                queries.append((q, min(3, max_r)))  # 动态词限制 max 3

            plan[domain_key] = {
                "queries": queries,
                "entities": selected_entities,
            }
            total_queries += len(queries)
            total_entities += len(selected_entities)

        self._log_plan(plan, total_queries, total_entities)
        return plan

    def _deterministic_select(self, pool, n, seed):
        """
        确定性随机选择：同一天 + 同一领域 = 同一结果（幂等）
        不同天 = 不同选择（轮转）
        """
        if not pool or n <= 0:
            return []
        rng = random.Random(seed)
        return rng.sample(pool, min(n, len(pool)))

    # ===================== 方案 B: 表现自适应 =====================

    def _adaptive_max_results(self, query):
        """
        根据 topic_history 中该关键词的历史表现调整 max_results。

        高质量关键词（高分文章多）→ max_results = 5
        普通关键词 → max_results = 3
        低质量/过饱和关键词 → max_results = 1-2

        安全降级: tracker 不可用时返回 5（默认）
        """
        if not self.tracker:
            return 5

        try:
            # 复用 topic_tracker 的配额系统
            quota = self.tracker.get_topic_quota(query)
            if quota <= 0:
                return 1  # 不完全跳过，给最低配额保底
            return min(5, max(1, quota))
        except Exception:
            return 5

    def get_keyword_performance_report(self):
        """
        生成关键词表现报告（用于诊断和调优）

        返回: dict — {keyword: {"days_appeared": int, "avg_score": float, "total_articles": int}}
        """
        if not self.tracker:
            return {}

        try:
            report = {}
            history = self.tracker._history.get("scored_articles", {})

            # 统计每个关键词的表现
            keyword_stats = {}
            for date_str, articles in history.items():
                for art in articles:
                    for tag in art.get("tags", []) + art.get("keywords", []):
                        tag_lower = tag.lower().strip()
                        if not tag_lower:
                            continue
                        if tag_lower not in keyword_stats:
                            keyword_stats[tag_lower] = {"scores": [], "dates": set()}
                        keyword_stats[tag_lower]["scores"].append(art.get("score", 0))
                        keyword_stats[tag_lower]["dates"].add(date_str)

            for kw, stats in keyword_stats.items():
                scores = stats["scores"]
                report[kw] = {
                    "days_appeared": len(stats["dates"]),
                    "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
                    "total_articles": len(scores),
                }

            return report
        except Exception:
            return {}

    # ===================== 方案 C: AI 动态关键词 =====================

    def suggest_dynamic_keywords(self, top_news):
        """
        基于今天的 Top 新闻，用 LLM 建议明天应该新增的搜索关键词。

        每天调用 1 次，成本极低（1 次 LLM 调用）。
        失败时静默跳过，不影响 A/B。

        参数:
            top_news: list — AI.py 中 final_list_for_ai 的前 15 条
        """
        if not top_news:
            return

        try:
            from llm_client import call_ai_api, extract_json_from_text
            from domain_config import get_prompt

            # 构建当前关键词列表（告诉 AI 已有哪些）
            existing_keywords = []
            for domain_key, keywords in DOMAIN["keyword_matrix"].items():
                for q in keywords.get("core", []) + keywords.get("extended", []):
                    existing_keywords.append(q)

            # 构建今日新闻摘要
            news_summary = []
            for item in top_news[:10]:
                p = item.get("parsed", {})
                news_summary.append(
                    f"[{p.get('score', 0)}分] {p.get('title', '')} | "
                    f"Tags: {', '.join(p.get('tags', []))} | "
                    f"分类: {p.get('category', '')}"
                )

            system_prompt = get_prompt("system_prompt")
            user_prompt = (
                "你是一位硬科技产业情报系统的关键词优化专家。\n\n"
                "**任务**：基于今天的新闻结果，建议 3-5 个应该新增到明天搜索计划中的关键词。\n\n"
                "**目标**：\n"
                "1. 捕捉今天新闻中出现的新兴话题、新公司、新技术\n"
                "2. 发现已有关键词未覆盖的盲区\n"
                "3. 追踪快速演变的事件（如新政策、新产品发布）\n\n"
                "**已有关键词列表**（不要重复建议）：\n"
                f"{chr(10).join('- ' + k for k in existing_keywords)}\n\n"
                "**今日新闻（Top 10）**：\n"
                f"{chr(10).join(news_summary)}\n\n"
                "**输出格式**：返回一个 JSON 对象，key 是领域分类，value 是关键词列表。\n"
                "领域分类必须从以下选择：\n"
                f"{list(DOMAIN['keyword_matrix'].keys())}\n\n"
                "示例：\n"
                '{"半导体与元器件": ["Glass interposer 玻璃中介层", "TSMC 2nm 量产"], '
                '"AI与算力": ["Blackwell Ultra 产能"]}\n\n'
                "重要规则：\n"
                "- 每个关键词 2-6 个词，具体且可搜索\n"
                "- 不要建议过于宽泛的词（如\"半导体\"\"AI\"）\n"
                "- 最多返回 5 个关键词\n"
                "- 只返回 JSON，不要其他文字"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            response = call_ai_api(messages, description="Dynamic Keywords")
            if not response:
                return

            suggestions = extract_json_from_text(response)
            if not isinstance(suggestions, dict):
                logger.warning("动态关键词建议格式不正确，跳过")
                return

            # 验证和清理
            valid_domains = set(DOMAIN["keyword_matrix"].keys())
            cleaned = {}
            total_new = 0
            for domain, kws in suggestions.items():
                if domain not in valid_domains:
                    continue
                if not isinstance(kws, list):
                    continue
                # 过滤掉已存在的关键词
                new_kws = []
                for kw in kws:
                    if isinstance(kw, str) and kw.strip() and kw.strip() not in existing_keywords:
                        new_kws.append(kw.strip())
                if new_kws:
                    cleaned[domain] = new_kws[:3]  # 每个领域最多3个
                    total_new += len(cleaned[domain])

            if cleaned:
                self._save_dynamic_keywords(cleaned)
                logger.info(f"🎯 方案C: AI 建议新增 {total_new} 个动态关键词")
                for domain, kws in cleaned.items():
                    logger.info(f"   {domain}: {kws}")
            else:
                logger.info("🎯 方案C: AI 未建议新关键词（当前覆盖充分）")

        except ImportError:
            logger.warning("方案C: llm_client 不可用，跳过动态关键词建议")
        except Exception as e:
            logger.warning(f"方案C: 动态关键词建议失败（不影响主流程）: {e}")

    # ===================== 动态关键词持久化 =====================

    def _load_dynamic_keywords(self):
        """加载 AI 建议的动态关键词（仅最近 N 天的）"""
        if not os.path.exists(DYNAMIC_KW_FILE):
            return {}
        try:
            with open(DYNAMIC_KW_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cutoff = (datetime.now() - timedelta(days=DYNAMIC_KW_RETENTION_DAYS)).strftime("%Y-%m-%d")
            merged = {}

            for date_str, day_suggestions in data.get("suggestions", {}).items():
                if date_str < cutoff:
                    continue
                for domain, kws in day_suggestions.items():
                    if domain not in merged:
                        merged[domain] = set()
                    merged[domain].update(kws)

            return {d: list(kws) for d, kws in merged.items()}

        except Exception as e:
            logger.warning(f"加载动态关键词失败，忽略: {e}")
            return {}

    def _save_dynamic_keywords(self, new_suggestions, date_str=None):
        """保存动态关键词建议（追加模式，自动清理过期）"""
        try:
            if not date_str:
                date_str = datetime.now().strftime("%Y-%m-%d")

            data = {"suggestions": {}}
            if os.path.exists(DYNAMIC_KW_FILE):
                try:
                    with open(DYNAMIC_KW_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    data = {"suggestions": {}}

            # 追加今天的建议
            data.setdefault("suggestions", {})
            data["suggestions"][date_str] = new_suggestions

            # 清理过期数据
            cutoff = (datetime.now() - timedelta(days=DYNAMIC_KW_RETENTION_DAYS * 2)).strftime("%Y-%m-%d")
            data["suggestions"] = {
                d: s for d, s in data["suggestions"].items() if d >= cutoff
            }

            with open(DYNAMIC_KW_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"保存动态关键词失败: {e}")

    # ===================== 日志 =====================

    def _log_plan(self, plan, total_queries, total_entities):
        """输出今日搜索计划摘要"""
        logger.info(f"📋 关键词调度计划: {total_queries} 查询 + {total_entities} 实体")
        for domain, p in plan.items():
            q_list = [f"{q}({m})" for q, m in p["queries"]]
            logger.info(f"   {domain}: [{', '.join(q_list)}] | 实体: {p['entities']}")
