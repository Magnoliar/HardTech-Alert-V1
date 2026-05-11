FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制引擎模块
COPY config_loader.py llm_client.py fact_purifier.py \
     article_renderer.py email_generator.py domain_config.py \
     text_utils.py humanizer_plugin.py style_extractor.py \
     write_article.py article_writer_v3.py angle_engine_v3.py \
     keyword_scheduler.py source_manager.py \
     ./

# 复制 sources 模块
COPY sources/ ./sources/

# 复制风格配置
COPY styles_hardtech.json ./

# 复制 web 应用
COPY web/ ./web/

# 创建数据目录
RUN mkdir -p /app/web/data

WORKDIR /app/web

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
