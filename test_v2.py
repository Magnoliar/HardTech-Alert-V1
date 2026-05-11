import json
import logging
import sys
import os

from angle_engine import AngleEngine
from article_writer_v2 import ArticleWriterV2

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_v2_test():
    # 1. 尝试找到今天最新抓取清洗的数据
    # 为了测试，硬编码指向当前最近的数据源：
    target_json = "2026-04-09-Clean.json"
    if not os.path.exists(target_json):
        logger.error(f"没有找到今日的情报数据：{target_json}")
        sys.exit(1)
        
    with open(target_json, 'r', encoding='utf-8') as f:
        news_list = json.load(f)
        
    # 只取最靠前的 40 条作分析即可防止超时
    scored_news = news_list[:40]

    # 2. 调用原版的选题引擎
    logger.info("⚙️ 引擎启动：生成 V2 测试选题包...")
    engine = AngleEngine()
    topic = engine.select_and_plan(scored_news)
    
    if not topic:
        logger.error("选题生成失败。")
        sys.exit(1)
        
    # 3. 交由全新的 V2 独立管道写作
    logger.info("🚀 转交大稿写作 V2 引擎...")
    writer = ArticleWriterV2()
    writer.run(topic, news_list)

if __name__ == "__main__":
    run_v2_test()
