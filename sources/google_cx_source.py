import requests
import logging
from .base_source import BaseSource

logger = logging.getLogger(__name__)

class GoogleCXSource(BaseSource):
    """Google Custom Search — 定向爆破特定网站（政府/垂直门户）"""

    BASE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key, cx_id, daily_limit=100):
        super().__init__(api_key, daily_limit)
        self.cx_id = cx_id

    @property
    def is_available(self):
        return (
            self.api_key and self.cx_id
            and self.api_key not in ('', 'YOUR_GOOGLE_CX_API_KEY')
            and self.cx_id not in ('', 'YOUR_GOOGLE_CX_ID')
            and self._call_count < self.daily_limit
        )

    def search(self, query, time_range="24h", max_results=10):
        params = {
            "key": self.api_key,
            "cx": self.cx_id,
            "q": query,
            "num": min(max_results, 10),
            "sort": "date",
        }
        if time_range == "24h":
            params["dateRestrict"] = "d1"

        resp = requests.get(self.BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("items", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", "")[:500],
                "source_platform": "google_cx",
                "publish_time": None,
            })
        return results
