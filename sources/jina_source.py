import requests
import logging
from .base_source import BaseSource

logger = logging.getLogger(__name__)

class JinaSource(BaseSource):
    """Jina Search API — 全网搜索，已在 V3 验证"""

    def search(self, query, time_range="24h", max_results=5):
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        resp = requests.get(f"https://s.jina.ai/{query}", headers=headers, timeout=35)
        if resp.status_code != 200:
            return []

        data = resp.json().get("data", [])
        results = []
        for item in data[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")[:500],
                "source_platform": "jina",
                "publish_time": None,
            })
        return results
