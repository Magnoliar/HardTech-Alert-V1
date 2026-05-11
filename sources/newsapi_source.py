import requests
import logging
from .base_source import BaseSource

logger = logging.getLogger(__name__)

class NewsAPISource(BaseSource):
    """NewsAPI.org — 实时全球新闻快讯"""

    BASE_URL = "https://newsapi.org/v2/everything"

    def search(self, query, time_range="24h", max_results=10):
        from datetime import datetime, timedelta, timezone
        # 计算时间范围
        now = datetime.now(timezone.utc)
        if time_range == "24h":
            from_date = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        else:
            from_date = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

        params = {
            "q": query,
            "from": from_date,
            "sortBy": "publishedAt",
            "pageSize": max_results,
            "language": "en",
            "apiKey": self.api_key,
        }

        resp = requests.get(self.BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for article in data.get("articles", []):
            title = article.get("title", "")
            if title and title != "[Removed]":
                results.append({
                    "title": title,
                    "url": article.get("url", ""),
                    "snippet": ((article.get("description") or "") + " " + (article.get("content") or ""))[:500],
                    "source_platform": "newsapi",
                    "publish_time": article.get("publishedAt"),
                })
        return results
