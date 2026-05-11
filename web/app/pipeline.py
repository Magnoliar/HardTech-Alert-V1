"""写作管线包装器 — 调用 write_article.py 的函数，收集素材来源"""
import json
import os
import time
import logging
import queue
from datetime import datetime

from app.sse import SSELogHandler, TaskLogBuffer
from app.models import WriteRequest

logger = logging.getLogger(__name__)

# 引擎目录（上层项目根目录）
_ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STYLES_FILE = os.path.join(_ENGINE_DIR, "styles_hardtech.json")
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class SourceCollector:
    """拦截 Jina API 响应，收集素材来源链接"""

    def __init__(self):
        self.sources: list[dict] = []

    def add(self, title: str, url: str, platform: str = "Jina"):
        if url and not any(s["url"] == url for s in self.sources):
            self.sources.append({"title": title, "url": url, "platform": platform})

    def add_from_jina_response(self, data: list, platform: str = "Jina Search"):
        """从 Jina API 响应中提取来源"""
        for item in data[:5]:
            title = item.get("title", "")
            url = item.get("url", "")
            if url:
                self.add(title, url, platform)


def _load_styles():
    if os.path.exists(_STYLES_FILE):
        try:
            with open(_STYLES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"Default": {"name": "标准硬科技评论", "persona": "专业、客观、数据驱动", "structure": "【现状】→【分析】→【结论】"}}


def _build_tokens(params: WriteRequest) -> list[str]:
    """从请求参数构建 tokens 列表"""
    tokens = []
    if params.keywords:
        tokens.extend(params.keywords.split())
    if params.outline:
        tokens.extend(params.outline)
    if params.links:
        tokens.extend(params.links)
    return tokens


def _patch_model_config(params: WriteRequest):
    """临时覆盖模型配置"""
    from app.config import get_web_config
    config = get_web_config()

    if params.model_override:
        config.api["model"] = params.model_override

    if params.api_config:
        if params.api_config.base_url:
            config.api["base_url"] = params.api_config.base_url
        if params.api_config.api_key:
            config.api["api_key_cheap"] = params.api_config.api_key
            config.api["api_key_premium"] = params.api_config.api_key
        if params.api_config.model:
            config.api["model"] = params.api_config.model


def _restore_model_config(original: dict):
    """恢复原始模型配置"""
    from app.config import get_web_config
    config = get_web_config()
    config.api.update(original)


def run_writing_pipeline(params: WriteRequest, log_buf: TaskLogBuffer) -> dict:
    """
    包装 write_article.py 的完整流程。
    在后台线程中运行，通过 log_buf 推送日志。

    返回 {article, sources, stats, topic}
    """
    # 设置日志 handler
    sse_handler = SSELogHandler(log_buf.queue)
    sse_handler.setFormatter(logging.Formatter('%(message)s'))

    root_logger = logging.getLogger()
    root_logger.addHandler(sse_handler)
    root_logger.setLevel(logging.INFO)

    try:
        import write_article as wa
        from app.config import get_web_config

        config = get_web_config()
        styles = _load_styles()

        # 保存原始配置用于恢复
        original_config = dict(config.api)
        _patch_model_config(params)

        try:
            log_buf.put_log("📡 写作管线启动...", "info")
            start_time = time.time()

            # 1. 构建 tokens
            tokens = _build_tokens(params)
            if not tokens:
                log_buf.put_error("至少需要提供一个输入项")
                return {"article": "", "sources": [], "stats": {}, "topic": {}}

            # 2. 素材采集
            log_buf.put_log("🔍 素材采集中...", "info")
            source_collector = SourceCollector()

            # 包装 Jina 搜索以收集来源
            _original_fetch_search = wa.fetch_jina_search
            def _patched_fetch_search(query, cfg):
                result = _original_fetch_search(query, cfg)
                # 尝试从原始 Jina 响应中提取 URL（这里只能从 result 中推断）
                return result
            wa.fetch_jina_search = _patched_fetch_search

            raw_material = wa.smart_collect(tokens, config)
            if not raw_material:
                log_buf.put_error("未采集到有效素材")
                return {"article": "", "sources": [], "stats": {}, "topic": {}}

            log_buf.put_log(f"✓ 素材采集完成 ({len(raw_material)} 字符)", "info")

            # 3. 选题规划
            log_buf.put_log("🧠 选题规划中...", "info")
            topic = wa.ai_plan_topic_v3(raw_material, config)
            title = topic.get('title_proposal', '未命名')
            log_buf.put_log(f"✓ 选题: {title}", "info")

            # 4. 写作
            log_buf.put_log("✍️ 写作中...", "info")

            # 根据 style_mode 处理风格
            if params.style_mode == "text" and params.style_text:
                from style_extractor import StyleExtractor
                custom_style = StyleExtractor.extract_from_content(params.style_text)
                if custom_style:
                    styles["custom"] = custom_style
                    topic["granularity"] = "中观"  # 默认
            elif params.style_mode == "link" and params.style_link:
                from style_extractor import StyleExtractor
                custom_style = StyleExtractor.extract_from_url(params.style_link)
                if custom_style:
                    styles["custom"] = custom_style
            elif params.style_id and params.style_id in styles:
                pass  # 直接使用已有风格

            article_text = wa.write_article_v3(topic, raw_material, styles, config, send_mail=False)

            if not article_text:
                log_buf.put_error("文章生成失败")
                return {"article": "", "sources": [], "stats": {}, "topic": {}}

            # 5. 读取生成的文件内容
            with open(article_text, 'r', encoding='utf-8') as f:
                article_content = f.read()

            duration = int(time.time() - start_time)
            stats = {
                "chars": len(article_content),
                "chapters": len(topic.get("outline", [])),
                "duration_sec": duration,
            }

            log_buf.put_log(f"✓ 写作完成 ({stats['chars']} 字, {duration}s)", "info")

            # 6. 保存到历史
            _save_history(title, article_content, stats, topic)

            # 7. 收集素材来源（从 raw_material 中提取 URL）
            import re
            urls_in_material = re.findall(r'https?://[^\s\)\]]+', raw_material)
            for url in urls_in_material[:10]:
                source_collector.add("", url, "原始素材")

            result = {
                "article": article_content,
                "sources": source_collector.sources,
                "stats": stats,
                "topic": {
                    "title": title,
                    "thesis": topic.get("thesis", ""),
                    "outline": topic.get("outline", []),
                },
            }

            log_buf.put_done(result)
            return result

        finally:
            _restore_model_config(original_config)
            wa.fetch_jina_search = _original_fetch_search

    except Exception as e:
        logger.exception("写作管线异常")
        log_buf.put_error(str(e))
        return {"article": "", "sources": [], "stats": {}, "topic": {}}
    finally:
        root_logger.removeHandler(sse_handler)


def _save_history(title: str, article: str, stats: dict, topic: dict):
    """保存到历史记录"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    history_file = os.path.join(_DATA_DIR, "history.json")

    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            pass

    entry = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "title": title[:60],
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "chars": stats.get("chars", 0),
        "article": article,
        "topic": topic,
    }

    history.insert(0, entry)
    history = history[:20]  # 保留最近 20 篇

    tmp = history_file + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    os.replace(tmp, history_file)
