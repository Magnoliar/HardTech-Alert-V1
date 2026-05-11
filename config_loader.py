import configparser
import os
import logging
import sys

logger = logging.getLogger(__name__)

CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')


def _parse_email_port(env_val, config):
    """安全解析 EMAIL_PORT，失败时 fallback 到 config.ini 或默认 465"""
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            logger.warning(f"⚠️ EMAIL_PORT 环境变量非数字: '{env_val}'，使用 config.ini 值")
    try:
        return config['EMAIL_AI'].getint('email_port', 465)
    except Exception:
        return 465


class Config:
    """纯基础设施配置 — API Key / 邮箱 / 阈值等。领域 Prompt 已移至 domain_config.py"""

    def __init__(self):
        self.api = {}
        self.sources = {}
        self.email_ai = {}
        self.app_settings = {}
        self._loaded = False

    def load(self):
        if not os.path.exists(CONFIG_FILE_PATH):
            logger.critical(f"配置文件 {CONFIG_FILE_PATH} 未找到。请运行 setup_wizard.py 创建。")
            sys.exit(1)

        config = configparser.ConfigParser(interpolation=None)
        try:
            config.read(CONFIG_FILE_PATH, encoding='utf-8')

            if 'API' in config:
                self.api = {
                    'base_url': os.getenv('AI_BASE_URL') or config['API'].get('base_url'),
                    'api_key_cheap': os.getenv('AI_API_KEY_CHEAP') or config['API'].get('api_key_cheap'),
                    'api_key_premium': os.getenv('AI_API_KEY_PREMIUM') or config['API'].get('api_key_premium'),
                    'model': os.getenv('AI_MODEL') or config['API'].get('api_model', 'gemini-3.1-flash-lite-preview'),
                    'model_backup': os.getenv('AI_MODEL_BACKUP') or config['API'].get('api_model_backup', 'gpt-5.2'),
                    'timeout': config['API'].getint('api_timeout', 60),
                    'retries': config['API'].getint('api_retries', 3),
                    'retry_delay': config['API'].getint('api_retry_delay', 5),
                    'batch_size': config['API'].getint('api_batch_size', 10),
                    'batch_call_delay': config['API'].getint('api_batch_call_delay', 1),
                    'temperature': config['API'].getfloat('api_temperature', 0.2),
                    'top_p': config['API'].getfloat('api_top_p', 0.95),
                    'jina_api_key': os.getenv('JINA_API_KEY') or config['API'].get('jina_api_key'),
                }

            if 'SOURCES' in config:
                self.sources = {
                    'tavily_api_key': os.getenv('TAVILY_API_KEY') or config['SOURCES'].get('tavily_api_key'),
                    'exa_api_key': os.getenv('EXA_API_KEY') or config['SOURCES'].get('exa_api_key'),
                    'newsapi_key': os.getenv('NEWSAPI_KEY') or config['SOURCES'].get('newsapi_key'),
                    'gnews_api_key': os.getenv('GNEWS_API_KEY') or config['SOURCES'].get('gnews_api_key'),
                    'google_cx_id': os.getenv('GOOGLE_CX_ID') or config['SOURCES'].get('google_cx_id'),
                    'google_cx_api_key': os.getenv('GOOGLE_CX_API_KEY') or config['SOURCES'].get('google_cx_api_key'),
                    'tavily_daily_limit': config['SOURCES'].getint('tavily_daily_limit', 33),
                    'exa_daily_limit': config['SOURCES'].getint('exa_daily_limit', 33),
                    'newsapi_daily_limit': config['SOURCES'].getint('newsapi_daily_limit', 80),
                    'gnews_daily_limit': config['SOURCES'].getint('gnews_daily_limit', 80),
                    'google_cx_daily_limit': config['SOURCES'].getint('google_cx_daily_limit', 80),
                    'brave_api_key': os.getenv('BRAVE_API_KEY') or config['SOURCES'].get('brave_api_key'),
                    'brave_daily_limit': config['SOURCES'].getint('brave_daily_limit', 33),
                }

            if 'EMAIL_AI' in config:
                self.email_ai = {
                    'host': os.getenv('EMAIL_HOST') or config['EMAIL_AI'].get('email_host'),
                    'port': _parse_email_port(os.getenv('EMAIL_PORT'), config),
                    'sender': os.getenv('EMAIL_SENDER') or config['EMAIL_AI'].get('email_sender'),
                    'password': os.getenv('EMAIL_PASSWORD') or config['EMAIL_AI'].get('email_password'),
                    'receiver': os.getenv('EMAIL_RECEIVER') or config['EMAIL_AI'].get('email_receiver'),
                }

            if 'APP_SETTINGS' in config:
                self.app_settings = {
                    'simhash_threshold': config['APP_SETTINGS'].getint('simhash_threshold', 3),
                    'history_retention_days': config['APP_SETTINGS'].getint('history_retention_days', 90),
                    # 话题新鲜度控制
                    'topic_lookback_days': config['APP_SETTINGS'].getint('topic_lookback_days', 7),
                    'freshness_penalty_per_day': config['APP_SETTINGS'].getint('freshness_penalty_per_day', 5),
                    'freshness_penalty_max': config['APP_SETTINGS'].getint('freshness_penalty_max', 20),
                    'topic_daily_cap': config['APP_SETTINGS'].getint('topic_daily_cap', 3),
                    'breakout_score_threshold': config['APP_SETTINGS'].getint('breakout_score_threshold', 90),
                    'writing_pipeline': config['APP_SETTINGS'].get('writing_pipeline', 'v2'),
                }

            self._loaded = True
            self._validate()

        except Exception as e:
            logger.critical(f"加载配置文件失败: {e}")
            sys.exit(1)

    def _validate(self):
        """配置校验 — 检查必填项、占位符、格式"""
        warnings = []

        # 检查 AI API 必填项
        for key in ('base_url', 'api_key_cheap', 'model'):
            val = self.api.get(key)
            if not val:
                warnings.append(f"AI 配置缺失: {key}")
            elif str(val).startswith('YOUR_'):
                warnings.append(f"AI 配置为占位符: {key} = {val}")

        # 检查搜索源占位符
        placeholder_keys = {
            'tavily_api_key': 'YOUR_TAVILY_API_KEY',
            'exa_api_key': 'YOUR_EXA_API_KEY',
            'newsapi_key': 'YOUR_NEWSAPI_KEY',
            'gnews_api_key': 'YOUR_GNEWS_API_KEY',
            'google_cx_api_key': 'YOUR_GOOGLE_CX_API_KEY',
            'brave_api_key': 'YOUR_BRAVE_API_KEY',
        }
        for key, placeholder in placeholder_keys.items():
            val = self.sources.get(key)
            if val == placeholder:
                warnings.append(f"搜索源为占位符: {key}")

        # 检查邮箱端口
        port = self.email_ai.get('port')
        if port is not None and (not isinstance(port, int) or port < 1 or port > 65535):
            warnings.append(f"邮箱端口无效: {port}")

        if warnings:
            for w in warnings:
                logger.warning(f"⚠️ 配置诊断: {w}")
        else:
            logger.info("✅ 配置校验通过")

# Singleton
global_config = Config()

def load_config():
    if not global_config._loaded:
        global_config.load()
    return global_config
