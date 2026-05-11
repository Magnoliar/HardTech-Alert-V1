import requests
import logging
from .base_source import BaseSource

logger = logging.getLogger(__name__)

class ExaSource(BaseSource):
    """Exa.ai — 神经搜索引擎，擅长研报和公司公告的精准溯源"""

    BASE_URL = "https://api.exa.ai/search"

    def search(self, query, time_range="24h", max_results=5):
        from datetime import datetime, timedelta, timezone
        if time_range == "24h":
            start_date = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            start_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "numResults": max_results,
            "startPublishedDate": start_date,
            "useAutoprompt": True,
            "type": "auto",
            "contents": {
                "text": {"maxCharacters": 500}
            }
        }

        resp = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("text", "")[:500],
                "source_platform": "exa",
                "publish_time": item.get("publishedDate"),
            })
        return results
