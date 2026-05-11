import requests
import logging
from .base_source import BaseSource

logger = logging.getLogger(__name__)


class BraveSource(BaseSource):
    """
    Brave Search API — 独立索引的通用网页 + 新闻搜索
    免费额度: 1000 次/月 (~33 次/天)
    优势: 独立于 Google 的索引、支持 freshness 过滤、同时返回 web + news 结果
    """

    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def search(self, query, time_range="24h", max_results=10):
        # 映射时间范围到 Brave freshness 参数
        freshness_map = {
            "24h": "pd",    # past day
            "7d": "pw",     # past week
            "30d": "pm",    # past month
            "365d": "py",   # past year
        }
        freshness = freshness_map.get(time_range, "pw")

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }
        params = {
            "q": query,
            "count": min(max_results, 20),  # Brave 最大 20 条/次
            "freshness": freshness,
            "text_decorations": "false",
            "search_lang": "zh-hans",       # 优先中文结果
            "result_filter": "web,news",    # 同时获取网页和新闻
            "extra_snippets": "true",       # 获取更多摘要片段
        }

        resp = requests.get(self.BASE_URL, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results = []

        # 解析 web 结果
        for item in data.get("web", {}).get("results", []):
            snippet = item.get("description", "")
            # 拼接 extra_snippets 获取更丰富的内容
            extras = item.get("extra_snippets", [])
            if extras:
                snippet = snippet + " " + " ".join(extras[:2])

            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": snippet[:500],
                "source_platform": "brave",
                "publish_time": item.get("page_age"),
            })

        # 解析 news 结果 (去重 URL)
        seen_urls = {r["url"] for r in results}
        for item in data.get("news", {}).get("results", []):
            url = item.get("url", "")
            if url in seen_urls:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("description", "")[:500],
                "source_platform": "brave_news",
                "publish_time": item.get("page_age") or item.get("age"),
            })

        return results[:max_results]
