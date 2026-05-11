# topic_tracker.py — 话题级去重 & 新鲜度衰减引擎
# ============================================================
# 纯算法模块，不调用任何 LLM。
# 依赖之前成功运行保存的 topic_history.json 历史记录。
# AI 完全失效时：历史为空 → 所有机制安全透传 → 退化为原始行为。
# ============================================================

import json
import os
import re
import hashlib
import logging
from datetime import datetime, timedelta
from collections import Counter
from config_loader import load_config

logger = logging.getLogger(__name__)

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'topic_history.json')

# --- URL 模式黑名单 (L1) ---
# 这些是标签聚合页 / 分类页 / 搜索结果页，不是真正的新闻文章
URL_BLACKLIST_PATTERNS = [
    r'/tags?/',              # /tag/ 或 /tags/
    r'/news-tags/',          # techpowerup 等标签页
    r'/category/',           # 分类聚合页
    r'/topic/',              # 话题聚合页
    r'/search\?',            # 搜索结果页
    r'#comments?$',          # 评论锚点
    r'/page/\d+',            # 分页URL
    r'\?page=',              # 分页参数
    r'/rss\b',               # RSS 页面
    r'/feed\b',              # Feed 页面
]


def is_blacklisted_url(url):
    """L1: 检查 URL 是否命中黑名单（标签页/聚合页等非文章 URL）"""
    if not url:
        return False
    return any(re.search(pattern, url, re.IGNORECASE) for pattern in URL_BLACKLIST_PATTERNS)


class TopicTracker:
    """
    话题追踪器 — 管理 topic_history.json

    职责:
    1. 记录每日 AI 评分后的输出（标题/摘要/标签/关键词/分数）
    2. 计算话题新鲜度衰减（L2）
    3. 提供话题每日上限（L3）
    4. 为 source_manager 提供关键词配额建议（L4）
    5. 突发保护：原始分 ≥ breakout_threshold 时豁免所有抑制

    鲁棒性设计:
    - 所有 public 方法在异常时返回安全默认值（0 penalty / 不限制）
    - 历史文件不存在 → 自动创建空历史
    - tags/keywords 为空 → penalty = 0（安全透传）
    """

    def __init__(self):
        self._config = self._load_settings()
        self._history = self._load_history()

    def _load_settings(self):
        """加载配置参数，使用安全默认值"""
        try:
            config = load_config()
            settings = config.app_settings
        except Exception:
            settings = {}

        return {
            'lookback_days': settings.get('topic_lookback_days', 7),
            'penalty_per_day': settings.get('freshness_penalty_per_day', 5),
            'penalty_max': settings.get('freshness_penalty_max', 20),
            'topic_daily_cap': settings.get('topic_daily_cap', 3),
            'breakout_threshold': settings.get('breakout_score_threshold', 90),
        }

    # --- 历史记录管理 ---

    def _load_history(self):
        """加载话题历史。文件缺失/损坏时返回空历史（安全降级）"""
        if not os.path.exists(HISTORY_FILE):
            return {"scored_articles": {}}
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) or "scored_articles" not in data:
                return {"scored_articles": {}}
            return data
        except Exception as e:
            logger.warning(f"加载 topic_history.json 失败，使用空历史: {e}")
            return {"scored_articles": {}}

    def _save_history(self):
        """保存话题历史。含自动过期清理。"""
        try:
            retention_days = self._config.get('lookback_days', 7) * 2  # 保留2x回溯窗口
            cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")

            articles = self._history.get("scored_articles", {})
            self._history["scored_articles"] = {
                date: items for date, items in articles.items()
                if date >= cutoff
            }

            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 topic_history.json 失败: {e}")

    # --- L2: 新鲜度衰减 ---

    def calc_freshness_penalty(self, tags, keywords):
        """
        计算话题新鲜度衰减扣分值。

        算法：统计 tags+keywords 中任意词在回溯窗口内出现的天数，
        取最高频的词作为基准计算扣分。

        返回值: int (0 ~ penalty_max)
        安全降级: 任何异常时返回 0（不扣分）
        """
        try:
            if not tags and not keywords:
                return 0

            lookback = self._config['lookback_days']
            penalty_per_day = self._config['penalty_per_day']
            penalty_max = self._config['penalty_max']
            today = datetime.now().date()

            # 收集回溯窗口内的所有历史 tag/keyword
            all_terms = [t.lower().strip() for t in (tags or []) + (keywords or []) if t.strip()]
            if not all_terms:
                return 0

            # 统计匹配天数：同一天内只要有任何 term 匹配就算一天
            matching_days = set()
            for date_str, day_articles in self._history.get("scored_articles", {}).items():
                try:
                    day_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if (today - day_date).days > lookback or day_date >= today:
                    continue

                for art in day_articles:
                    hist_terms = set(
                        t.lower().strip()
                        for t in art.get('tags', []) + art.get('keywords', [])
                        if t.strip()
                    )
                    if hist_terms & set(all_terms):
                        matching_days.add(date_str)
                        break

            days_appeared = len(matching_days)
            if days_appeared < 2:
                return 0

            # 从第2天开始每多1天扣 penalty_per_day
            penalty = min((days_appeared - 1) * penalty_per_day, penalty_max)
            return penalty

        except Exception as e:
            logger.warning(f"新鲜度衰减计算失败，安全跳过: {e}")
            return 0

    # --- L3: 话题每日上限 ---

    def get_topic_cap(self, tags):
        """
        返回该话题今天最多还能保留的条数。

        基于今天已输出的同话题文章数量判断。
        安全降级: 异常时返回 999（不限制）
        """
        try:
            if not tags:
                return 999  # 无标签 → 不限制

            cap = self._config['topic_daily_cap']
            today_str = datetime.now().strftime("%Y-%m-%d")
            today_articles = self._history.get("scored_articles", {}).get(today_str, [])

            input_terms = set(t.lower().strip() for t in tags if t.strip())
            if not input_terms:
                return 999

            # 统计今天已有多少条包含相同 tags 的文章
            overlap_count = 0
            for art in today_articles:
                hist_terms = set(
                    t.lower().strip()
                    for t in art.get('tags', []) + art.get('keywords', [])
                    if t.strip()
                )
                if len(input_terms & hist_terms) >= 1:
                    overlap_count += 1

            remaining = max(0, cap - overlap_count)
            return remaining

        except Exception as e:
            logger.warning(f"话题上限计算失败，安全跳过: {e}")
            return 999

    # --- L4: 关键词配额建议 ---

    def get_topic_quota(self, search_query):
        """
        为 source_manager 提供搜索配额建议。

        如果搜索关键词对应的话题在近期已大量覆盖，建议减少搜索量。
        返回值: int (建议 max_results，0 表示建议跳过)
        安全降级: 异常时返回 5（默认配额，不限制）
        """
        try:
            lookback = self._config['lookback_days']
            today = datetime.now().date()

            # 将搜索词拆分为子词来匹配
            query_terms = set(
                w.lower().strip()
                for w in re.split(r'[\s,，、]+', search_query)
                if len(w.strip()) > 1
            )
            if not query_terms:
                return 5

            # 统计回溯窗口内多少篇文章匹配该关键词
            total_matches = 0
            matching_days = set()

            for date_str, day_articles in self._history.get("scored_articles", {}).items():
                try:
                    day_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if (today - day_date).days > lookback or day_date >= today:
                    continue

                day_matched = False
                for art in day_articles:
                    hist_terms = set(
                        t.lower().strip()
                        for t in art.get('tags', []) + art.get('keywords', [])
                        if t.strip()
                    )
                    # 也检查标题
                    title_words = set(
                        w.lower().strip()
                        for w in re.split(r'[\s,，、]+', art.get('title', ''))
                        if len(w.strip()) > 1
                    )
                    combined = hist_terms | title_words

                    if query_terms & combined:
                        total_matches += 1
                        day_matched = True

                if day_matched:
                    matching_days.add(date_str)

            days_count = len(matching_days)

            # 决策逻辑
            if days_count >= 5 and total_matches >= 10:
                return 0  # 建议跳过
            elif days_count >= 3 and total_matches >= 6:
                return 1  # 只搜1条
            elif days_count >= 2 and total_matches >= 4:
                return 2  # 减半
            else:
                return 5  # 正常配额

        except Exception as e:
            logger.warning(f"关键词配额计算失败，使用默认值: {e}")
            return 5

    # --- 突发保护 ---

    def is_breakout(self, score):
        """判断是否为突发爆款新闻（豁免一切抑制）"""
        return score >= self._config['breakout_threshold']

    # --- 记录 AI 评分输出 ---

    def record_scored_articles(self, scored_articles, date_str=None):
        """
        记录 AI 评分后的最终输出到 topic_history.json。

        这是整个反馈回路的核心：用 AI 输出的干净数据（而非噪声大的原始抓取）
        作为跨日去重的参考依据。

        参数:
            scored_articles: list - AI.py 中的 final_list_for_ai
            date_str: str - 日期 (默认今天)

        安全降级: 任何异常只 log 不中断主流程
        """
        try:
            if not scored_articles:
                return

            if not date_str:
                date_str = datetime.now().strftime("%Y-%m-%d")

            records = []
            for item in scored_articles:
                parsed = item.get('parsed', {})
                url = item.get('url', '')

                record = {
                    'title': parsed.get('title', ''),
                    'summary': parsed.get('summary', ''),
                    'tags': parsed.get('tags', []),
                    'keywords': parsed.get('keywords', []),
                    'score': parsed.get('score', 0),
                    'category': parsed.get('category', ''),
                    'url_fp': hashlib.md5(url.encode('utf-8')).hexdigest() if url else '',
                }
                records.append(record)

            self._history.setdefault("scored_articles", {})
            # 追加而非覆盖（支持同日多次运行）
            existing = self._history["scored_articles"].get(date_str, [])
            existing.extend(records)
            self._history["scored_articles"][date_str] = existing

            self._save_history()
            logger.info(f"📝 话题历史已更新: {date_str} 新增 {len(records)} 条记录")

        except Exception as e:
            logger.error(f"记录话题历史失败（不影响主流程）: {e}")
