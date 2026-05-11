"""环境变量配置 — 替代 config.ini，让 write_article.py 的函数能直接用"""
import os


class _ApiConfig(dict):
    """兼容 config_loader.Config.api 的 dict 子类"""

    def get(self, key, default=None):
        return dict.get(self, key, default)


class _SectionConfig(dict):
    """兼容 config_loader.Config 的属性访问"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class WebConfig:
    """从环境变量构建的配置对象，兼容 config_loader.Config 接口"""

    def __init__(self):
        self.api = _ApiConfig({
            "base_url": os.getenv("AI_BASE_URL", "https://yunwu.ai/v1/chat/completions"),
            "api_key_cheap": os.getenv("AI_API_KEY_CHEAP", ""),
            "api_key_premium": os.getenv("AI_API_KEY_PREMIUM", ""),
            "model": os.getenv("AI_MODEL", "gemini-3.1-flash-lite-preview"),
            "model_backup": os.getenv("AI_MODEL_BACKUP", "gpt-5.2"),
            "timeout": int(os.getenv("AI_TIMEOUT", "60")),
            "retries": int(os.getenv("AI_RETRIES", "3")),
            "retry_delay": int(os.getenv("AI_RETRY_DELAY", "5")),
            "batch_size": int(os.getenv("AI_BATCH_SIZE", "10")),
            "batch_call_delay": int(os.getenv("AI_BATCH_CALL_DELAY", "1")),
            "temperature": float(os.getenv("AI_TEMPERATURE", "0.2")),
            "top_p": float(os.getenv("AI_TOP_P", "0.95")),
            "jina_api_key": os.getenv("JINA_API_KEY", ""),
        })

        self.sources = _SectionConfig({
            "tavily_api_key": os.getenv("TAVILY_API_KEY", ""),
            "exa_api_key": os.getenv("EXA_API_KEY", ""),
            "newsapi_key": os.getenv("NEWSAPI_KEY", ""),
            "gnews_api_key": os.getenv("GNEWS_API_KEY", ""),
            "google_cx_id": os.getenv("GOOGLE_CX_ID", ""),
            "google_cx_api_key": os.getenv("GOOGLE_CX_API_KEY", ""),
            "brave_api_key": os.getenv("BRAVE_API_KEY", ""),
            "tavily_daily_limit": int(os.getenv("TAVILY_DAILY_LIMIT", "33")),
            "exa_daily_limit": int(os.getenv("EXA_DAILY_LIMIT", "10")),
            "newsapi_daily_limit": int(os.getenv("NEWSAPI_DAILY_LIMIT", "50")),
            "gnews_daily_limit": int(os.getenv("GNEWS_DAILY_LIMIT", "80")),
            "google_cx_daily_limit": int(os.getenv("GOOGLE_CX_DAILY_LIMIT", "80")),
            "brave_daily_limit": int(os.getenv("BRAVE_DAILY_LIMIT", "33")),
        })

        self.email_ai = _SectionConfig({
            "email_host": os.getenv("EMAIL_HOST", "smtp.exmail.qq.com"),
            "email_port": int(os.getenv("EMAIL_PORT", "465")),
            "email_sender": os.getenv("EMAIL_SENDER", ""),
            "email_password": os.getenv("EMAIL_PASSWORD", ""),
            "email_receiver": os.getenv("EMAIL_RECEIVER", ""),
        })

        self.app_settings = _SectionConfig({
            "writing_pipeline": os.getenv("WRITING_PIPELINE", "v3"),
            "simhash_threshold": int(os.getenv("SIMHASH_THRESHOLD", "3")),
            "history_retention_days": int(os.getenv("HISTORY_RETENTION_DAYS", "90")),
        })


# 单例
_config_instance = None


def get_web_config():
    global _config_instance
    if _config_instance is None:
        _config_instance = WebConfig()
    return _config_instance


def patch_config_loader():
    """Monkey-patch config_loader.load_config，让它返回 WebConfig"""
    import config_loader
    config_loader.load_config = get_web_config
    config_loader.global_config = get_web_config()
