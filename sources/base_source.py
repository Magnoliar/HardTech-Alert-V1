from abc import ABC, abstractmethod
import json
import os
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# 源健康度持久化文件
_HEALTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'source_health.json')
# 连续失败 N 次后自动禁用该源（当天内）
_AUTO_DISABLE_THRESHOLD = 3
# 文件读写锁，防止多线程并发损坏 source_health.json
_health_lock = threading.Lock()


class BaseSource(ABC):
    """所有信息源插件的抽象基类"""

    def __init__(self, api_key, daily_limit=100):
        self.api_key = api_key
        self.daily_limit = daily_limit
        self._call_count = 0
        self._consecutive_failures = 0
        self._disabled_today = False
        self.name = self.__class__.__name__
        self._load_health()

    @property
    def is_available(self):
        """API Key 是否已配置、未超额、且未被健康度降级"""
        if self._disabled_today:
            return False
        return (
            self.api_key
            and self.api_key not in ('', 'YOUR_TAVILY_API_KEY', 'YOUR_EXA_API_KEY',
                                      'YOUR_NEWSAPI_KEY', 'YOUR_GNEWS_API_KEY',
                                      'YOUR_GOOGLE_CX_ID', 'YOUR_GOOGLE_CX_API_KEY',
                                      'YOUR_BRAVE_API_KEY')
            and self._call_count < self.daily_limit
        )

    @property
    def remaining_quota(self):
        return max(0, self.daily_limit - self._call_count)

    def _increment_count(self):
        self._call_count += 1

    @abstractmethod
    def search(self, query, time_range="24h", max_results=10):
        """
        执行搜索，返回标准化结果列表。
        每个结果必须包含:
        {
            "title": str,
            "url": str,
            "snippet": str,
            "source_platform": str,
            "publish_time": str or None,
        }
        """
        pass

    def safe_search(self, query, **kwargs):
        """带额度检查和健康度自动降级的安全搜索封装"""
        if not self.is_available:
            return []
        try:
            results = self.search(query, **kwargs)
            self._increment_count()
            # 搜索成功 → 重置失败计数
            if self._consecutive_failures > 0:
                self._consecutive_failures = 0
                self._save_health()
            return results
        except Exception as e:
            logger.warning(f"[{self.name}] 搜索失败 ({query[:30]}...): {e}")
            self._increment_count()
            self._consecutive_failures += 1

            # 连续失败达阈值 → 自动禁用
            if self._consecutive_failures >= _AUTO_DISABLE_THRESHOLD:
                self._disabled_today = True
                logger.warning(
                    f"⛔ [{self.name}] 连续失败 {self._consecutive_failures} 次，"
                    f"今日自动禁用（明天自动恢复）"
                )
            self._save_health()
            return []

    # ==================== 健康度持久化 ====================

    def _load_health(self):
        """从 source_health.json 加载健康状态（仅当天的数据有效）"""
        with _health_lock:
            try:
                if not os.path.exists(_HEALTH_FILE):
                    return
                with open(_HEALTH_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                today = datetime.now().strftime("%Y-%m-%d")
                source_data = data.get(self.name, {})

                # 不同天 → 自动重置（给源"第二次机会"）
                if source_data.get('date') != today:
                    return

                self._consecutive_failures = source_data.get('consecutive_failures', 0)
                if self._consecutive_failures >= _AUTO_DISABLE_THRESHOLD:
                    self._disabled_today = True
                    logger.info(f"⏭️ [{self.name}] 今日已被健康度降级，跳过")

            except Exception:
                pass  # 文件损坏时安全忽略

    def _save_health(self):
        """保存健康状态到 source_health.json"""
        with _health_lock:
            try:
                data = {}
                if os.path.exists(_HEALTH_FILE):
                    try:
                        with open(_HEALTH_FILE, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                    except Exception:
                        data = {}

                data[self.name] = {
                    'date': datetime.now().strftime("%Y-%m-%d"),
                    'consecutive_failures': self._consecutive_failures,
                    'disabled': self._disabled_today,
                }

                with open(_HEALTH_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            except Exception:
                pass  # 保存失败不影响主流程
