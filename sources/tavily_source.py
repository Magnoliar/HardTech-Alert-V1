import requests
import logging
from .base_source import BaseSource

logger = logging.getLogger(__name__)

class TavilySource(BaseSource):
    """Tavily Search API — 深度搜索模式，适合获取长文本摘要"""

    BASE_URL = "https://api.tavily.com/search"

    def search(self, query, time_range="24h", max_results=5):
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False,
        }
        # Tavily 不直接支持 time_range 参数，通过 query 附加时间限定
        if time_range == "24h":
            payload["query"] = f"{query} latest today"

        resp = requests.post(self.BASE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")[:500],
                "source_platform": "tavily",
                "publish_time": item.get("published_date"),
            })
        return results
