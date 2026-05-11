import json
import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from config_loader import load_config
from domain_config import DOMAIN

logger = logging.getLogger(__name__)

class SourceManager:
    """
    多源采集调度器 — 替代 V3 的 GoogleAlert.py
    按领域关键词矩阵 + 信源偏好进行智能调度
    """

    def __init__(self):
        self.config = load_config()
        self.sources = self._init_sources()
        self.today_str = datetime.now().strftime("%Y-%m-%d")
        # L4: 初始化话题追踪器 (安全: 失败时 tracker=None → 使用默认配额)
        try:
            from topic_tracker import TopicTracker
            self.tracker = TopicTracker()
        except Exception as e:
            logger.warning(f"话题追踪器不可用，使用全量搜索: {e}")
            self.tracker = None

    def _init_sources(self):
        """初始化所有可用的信息源插件"""
        src_cfg = self.config.sources
        api_cfg = self.config.api
        available = {}

        # Tavily
        from sources.tavily_source import TavilySource
        s = TavilySource(src_cfg.get('tavily_api_key'), src_cfg.get('tavily_daily_limit', 33))
        if s.is_available: available['tavily'] = s

        # NewsAPI
        from sources.newsapi_source import NewsAPISource
        s = NewsAPISource(src_cfg.get('newsapi_key'), src_cfg.get('newsapi_daily_limit', 80))
        if s.is_available: available['newsapi'] = s

        # Exa.ai
        from sources.exa_source import ExaSource
        s = ExaSource(src_cfg.get('exa_api_key'), src_cfg.get('exa_daily_limit', 33))
        if s.is_available: available['exa'] = s

        # GNews
        from sources.gnews_source import GNewsSource
        s = GNewsSource(src_cfg.get('gnews_api_key'), src_cfg.get('gnews_daily_limit', 80))
        if s.is_available: available['gnews'] = s

        # Jina (使用 AI 配置中的 jina key)
        from sources.jina_source import JinaSource
        s = JinaSource(api_cfg.get('jina_api_key'), daily_limit=50)
        if s.is_available: available['jina'] = s

        # Google CX
        from sources.google_cx_source import GoogleCXSource
        s = GoogleCXSource(
            src_cfg.get('google_cx_api_key'),
            src_cfg.get('google_cx_id'),
            src_cfg.get('google_cx_daily_limit', 80)
        )
        if s.is_available: available['google_cx'] = s

        # Brave Search
        from sources.brave_source import BraveSource
        s = BraveSource(src_cfg.get('brave_api_key'), src_cfg.get('brave_daily_limit', 33))
        if s.is_available: available['brave'] = s

        logger.info(f"📡 信息源初始化完成，可用通道: {list(available.keys())}")
        return available

    def collect_all(self):
        """
        按智能调度计划执行采集。
        使用 KeywordScheduler 替代硬编码的全量关键词遍历。
        输出与 V3 simout.py 兼容的 JSON 格式。
        返回输出文件路径。
        """
        _dir = os.path.dirname(os.path.abspath(__file__))
        raw_file = os.path.join(_dir, f"{self.today_str}-Raw.json")

        # 如果今天已采集过，直接返回
        if os.path.exists(raw_file):
            logger.info(f"发现今日已采集数据: {raw_file}")
            return raw_file

        # 初始化智能调度器 (失败时回退到旧逻辑)
        try:
            from keyword_scheduler import KeywordScheduler
            scheduler = KeywordScheduler(tracker=self.tracker)
            search_plan = scheduler.get_todays_search_plan()
        except Exception as e:
            logger.warning(f"关键词调度器初始化失败，回退到全量搜索: {e}")
            search_plan = None

        all_results = []
        source_prefs = DOMAIN["source_preferences"]

        def _search_domain(domain_key, plan):
            """单个领域的搜索逻辑（线程安全：每个 source 对象独立，文件操作有 _health_lock）"""
            domain_results = []
            preferred = source_prefs.get(domain_key, list(self.sources.keys()))
            logger.info(f"🔍 采集领域: {domain_key} | 优先信源: {preferred}")

            # 关键词搜索 (含轮转 + 动态词)
            for query, max_results in plan.get("queries", []):
                for source_name in preferred:
                    source = self.sources.get(source_name)
                    if not source or not source.is_available:
                        continue
                    results = source.safe_search(query, max_results=max_results)
                    for r in results:
                        r["domain_key"] = domain_key
                        r["search_query"] = query
                    domain_results.extend(results)
                    if results:
                        break

            # 实体搜索 (调度器已做轮转选择)
            for entity in plan.get("entities", []):
                for source_name in preferred:
                    source = self.sources.get(source_name)
                    if not source or not source.is_available:
                        continue
                    results = source.safe_search(f"{entity} latest news", max_results=3)
                    for r in results:
                        r["domain_key"] = domain_key
                        r["search_query"] = entity
                    domain_results.extend(results)
                    break

            return domain_results

        if search_plan:
            # === 并行搜索: 不同领域同时执行 ===
            try:
                with ThreadPoolExecutor(max_workers=3) as executor:
                    futures = {
                        executor.submit(_search_domain, dk, plan): dk
                        for dk, plan in search_plan.items()
                    }
                    for future in as_completed(futures):
                        dk = futures[future]
                        try:
                            domain_results = future.result(timeout=120)
                            all_results.extend(domain_results)
                        except Exception as e:
                            logger.warning(f"领域 {dk} 并行采集异常: {e}")
            except Exception as e:
                logger.warning(f"并行采集框架异常，回退到串行: {e}")
                for dk, plan in search_plan.items():
                    all_results.extend(_search_domain(dk, plan))

        else:
            # === 回退逻辑: 原始全量搜索 (调度器不可用时) ===
            keyword_matrix = DOMAIN["keyword_matrix"]
            for domain_key, keywords in keyword_matrix.items():
                preferred = source_prefs.get(domain_key, list(self.sources.keys()))
                logger.info(f"🔍 [回退] 采集领域: {domain_key} | 优先信源: {preferred}")

                for query in keywords.get("core", []):
                    for source_name in preferred:
                        source = self.sources.get(source_name)
                        if not source or not source.is_available:
                            continue
                        results = source.safe_search(query, max_results=5)
                        for r in results:
                            r["domain_key"] = domain_key
                            r["search_query"] = query
                        all_results.extend(results)
                        if results:
                            break

                for entity in keywords.get("entities", [])[:3]:
                    for source_name in preferred:
                        source = self.sources.get(source_name)
                        if not source or not source.is_available:
                            continue
                        results = source.safe_search(f"{entity} latest news", max_results=3)
                        for r in results:
                            r["domain_key"] = domain_key
                            r["search_query"] = entity
                        all_results.extend(results)
                        break

        logger.info(f"📊 采集完成: 共 {len(all_results)} 条原始结果")

        # 转换为 V3 兼容格式并保存
        v3_format = self._to_v3_format(all_results)
        with open(raw_file, 'w', encoding='utf-8') as f:
            json.dump(v3_format, f, ensure_ascii=False, indent=4)

        logger.info(f"💾 原始数据已保存: {raw_file}")
        self._log_quota_status()
        return raw_file

    def _to_v3_format(self, flat_results):
        """
        将扁平结果列表转换为 V3 mail-style JSON 格式
        以便 simout.py 可以直接消费。
        格式: [{"subject": ..., "body": {"domain_key": ["content <url>", ...]}}]
        """
        # 按 domain_key 分组
        grouped = {}
        for r in flat_results:
            dk = r.get("domain_key", "其他")
            if dk not in grouped:
                grouped[dk] = []
            # 构造 V3 格式的文本条目: "标题 摘要 <url>"
            text = f"{r['title']} {r['snippet']} <{r['url']}>"
            grouped[dk].append(text)

        return [{
            "subject": f"{DOMAIN['brand']} 多源采集 {self.today_str}",
            "from": "source_manager",
            "date": datetime.now().isoformat(),
            "body": grouped,
        }]

    def _log_quota_status(self):
        """输出各源剩余额度"""
        for name, source in self.sources.items():
            logger.info(f"  📉 {name}: 剩余 {source.remaining_quota}/{source.daily_limit}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    sm = SourceManager()
    result_file = sm.collect_all()
    print(f"采集完成: {result_file}")
