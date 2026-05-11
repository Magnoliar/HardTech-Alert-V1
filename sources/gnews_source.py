import requests
import logging
from .base_source import BaseSource

logger = logging.getLogger(__name__)

class GNewsSource(BaseSource):
    """GNews.io — 中文新闻补位，适合国内厂商动态"""

    BASE_URL = "https://gnews.io/api/v4/search"

    def search(self, query, time_range="24h", max_results=10):
        params = {
            "q": query,
            "lang": "zh",
            "max": max_results,
            "sortby": "publishedAt",
            "token": self.api_key,
        }
        if time_range == "24h":
            from datetime import datetime, timedelta, timezone
            params["from"] = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

        resp = requests.get(self.BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for article in data.get("articles", []):
            results.append({
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "snippet": article.get("description", "")[:500],
                "source_platform": "gnews",
                "publish_time": article.get("publishedAt"),
            })
        return results
