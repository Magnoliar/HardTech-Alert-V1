import json
import os
import re
import time
import logging
from collections import defaultdict
from difflib import SequenceMatcher
from datetime import datetime

from config_loader import load_config
from llm_client import call_ai_api, extract_json_from_text
from email_generator import build_html_email, send_email
from domain_config import DOMAIN, get_prompt
from topic_tracker import TopicTracker

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
ALERT_SENT_STATE = os.path.join(_DIR, ".alert_sent_today.json")


def _check_alert_sent_today():
    """检查今天是否已发送过 alert 邮件"""
    today = datetime.now().strftime("%Y-%m-%d")
    if not os.path.exists(ALERT_SENT_STATE):
        return False
    try:
        with open(ALERT_SENT_STATE, 'r', encoding='utf-8') as f:
            state = json.load(f)
        return state.get("date") == today
    except Exception:
        return False


def _mark_alert_sent():
    """标记今天已发送 alert 邮件"""
    now = datetime.now()
    state = {"date": now.strftime("%Y-%m-%d"), "sent_at": now.strftime("%H:%M:%S")}
    try:
        with open(ALERT_SENT_STATE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"写入 alert 状态文件失败: {e}")


# --- 功能函数 ---

def fetch_batch_data(batch_items):
    """发送一批新闻进行筛选、打分和分类"""
    if not batch_items: return []

    system_prompt = get_prompt('system_prompt')
    filter_prompt = get_prompt('filter_prompt')

    user_content_parts = []
    for i, item in enumerate(batch_items):
        content = item.get("content", "")
        user_content_parts.append(f"新闻项 {i + 1}:\n内容：{content}")

    user_content_full = "\n\n".join(user_content_parts)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{filter_prompt}\n\n待分析新闻列表：\n{user_content_full}"}
    ]

    response_text = call_ai_api(messages, description="Batch Filter")
    if response_text:
        return extract_json_from_text(response_text)
    return []

def fetch_email_summary(news_list):
    """生成邮件导读 — 带情绪分级"""
    email_summary_prompt = get_prompt('email_summary_prompt')
    system_prompt = get_prompt('system_prompt')

    if not news_list or not email_summary_prompt: return None

    max_score = max([item.get('parsed', {}).get('score', 0) for item in news_list])

    if max_score >= 90:
        mood = "【文风：重磅警报】今日有足以重塑产业格局的重磅消息，允许使用强烈的紧迫感。"
    elif max_score >= 80:
        mood = "【文风：冷静观察】今日有重要技术进展但未到巨变程度。保持理性、专业的分析口吻。"
    else:
        mood = "【文风：日常简报】今日多为常规动态。语气平和简洁，像资深分析师在同步近况。"

    formatted = []
    for item in news_list:
        p = item.get('parsed', {})
        formatted.append(f"- [{p.get('score', 0)}分] {p.get('title', '无标题')}: {p.get('summary', '')}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{mood}\n\n{email_summary_prompt}\n\n今日精选新闻：\n" + "\n".join(formatted)}
    ]

    response_text = call_ai_api(messages, description="Email Summary")

    try:
        json_obj = extract_json_from_text(response_text)
        if isinstance(json_obj, dict) and 'content' in json_obj:
            return json_obj['content']
    except Exception: pass

    if response_text:
        response_text = re.sub(r'```json\s*', '', response_text)
        response_text = re.sub(r'```', '', response_text)
    return response_text

def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()

def deduplicate_final_results(results):
    """AI 输出后的二次去重"""
    results.sort(key=lambda x: x.get('parsed', {}).get('score', 0), reverse=True)

    unique_results = []
    seen_titles = []
    seen_summaries = []

    for item in results:
        parsed = item.get('parsed', {})
        title = parsed.get('title', '')
        summary = parsed.get('summary', '')
        current_keywords = set(parsed.get('keywords', []))
        current_tags = set(parsed.get('tags', []))

        if not title:
            unique_results.append(item)
            continue

        is_duplicate = False
        for seen_title in seen_titles:
            if similar(title, seen_title) > 0.8:
                is_duplicate = True; break

        if not is_duplicate and summary:
            for seen_summary in seen_summaries:
                if similar(summary, seen_summary) > 0.7:
                    is_duplicate = True; break

        if not is_duplicate:
            for existing in unique_results:
                ep = existing.get('parsed', {})
                existing_kw = set(ep.get('keywords', []))
                existing_tags = set(ep.get('tags', []))
                title_sim = similar(title, ep.get('title', ''))
                if title_sim > 0.4 and (len(current_keywords & existing_kw) >= 2 or len(current_tags & existing_tags) >= 2):
                    is_duplicate = True; break

        if not is_duplicate:
            unique_results.append(item)
            seen_titles.append(title)
            seen_summaries.append(summary)

    return unique_results

def process_and_send_alerts(json_filename, progress_callback=None):
    """主处理逻辑"""
    if not os.path.exists(json_filename):
        return None

    config = load_config()
    api_config = config.api
    batch_size = api_config.get('batch_size', 10)
    batch_call_delay = api_config.get('batch_call_delay', 1)

    # 初始化话题追踪器 (安全: 失败时所有方法返回安全默认值)
    try:
        tracker = TopicTracker()
    except Exception as e:
        logger.warning(f"话题追踪器初始化失败，跳过话题去重: {e}")
        tracker = None

    with open(json_filename, 'r', encoding='utf-8') as f:
        try: raw_data = json.load(f)
        except Exception as e:
            logger.error(f"JSON 解析失败: {e}")
            return None

    all_articles = []
    for item in raw_data:
        body = item.get("body", {})
        for category_key, contents in body.items():
            for content in contents:
                url_match = re.search(r'<?(https?://[^\s>]+)>?', content)
                url = url_match.group(1) if url_match else ""
                content_clean = re.sub(r'<?https?://[^\s>]+>?', '', content).strip()
                if content_clean:
                    all_articles.append({"content": content_clean, "url": url, "raw_category": category_key})

    # 多级重试评分
    processed_results = []
    total_batches = (len(all_articles) + batch_size - 1) // batch_size
    try:
        for i in range(0, len(all_articles), batch_size):
            batch = all_articles[i : i + batch_size]
            batch_num = i // batch_size + 1
            try:
                ai_results = fetch_batch_data(batch)
                if isinstance(ai_results, list):
                    for res in ai_results:
                        orig_id = res.get('original_id')
                        try:
                            idx = int(orig_id) - 1
                            if 0 <= idx < len(batch) and res.get('included') and res.get('score', 0) >= 60:
                                processed_results.append({"parsed": res, "url": batch[idx]['url']})
                        except Exception: pass
            except Exception as e:
                logger.error(f"批次处理异常: {e}")
            if progress_callback:
                progress_callback(batch_num, total_batches, len(processed_results))
            time.sleep(batch_call_delay)
    except Exception as ee:
        logger.error(f"评分主循环崩溃: {ee}")

    # 终极兜底
    if not processed_results:
        logger.warning("🚨 触发终极兜底：AI 评分失效，提取原始列表...")
        for idx, item in enumerate(all_articles[:20]):
            processed_results.append({
                "final_index": idx + 1,
                "url": item.get('url', ''),
                "parsed": {
                    "title": item.get('content', '')[:60].strip() + "...",
                    "summary": item.get('content', '')[:250].strip() + " (注：此条目因AI服务波动未评分)",
                    "score": 70,
                    "category": item.get('raw_category', '其他'),
                    "reasoning": "由于 API 连通性限制，此内容由物理系统自动提取分发。",
                    "tags": ["自动提取"],
                    "keywords": []
                }
            })

    if not processed_results: return None

    # --- L2: 新鲜度衰减 (话题追踪器可用时) ---
    if tracker:
        try:
            penalty_applied = 0
            removed_by_penalty = 0
            for item in processed_results:
                p = item['parsed']
                original_score = p.get('score', 0)

                # 突发保护: 90+ 分豁免一切惩罚
                if tracker.is_breakout(original_score):
                    continue

                tags = p.get('tags', [])
                keywords = p.get('keywords', [])
                penalty = tracker.calc_freshness_penalty(tags, keywords)

                if penalty > 0:
                    p['score'] = max(0, original_score - penalty)
                    p['_freshness_penalty'] = penalty
                    p['_original_score'] = original_score
                    penalty_applied += 1

            # 移除衰减后低于 60 分的文章
            before_count = len(processed_results)
            processed_results = [
                item for item in processed_results
                if item['parsed'].get('score', 0) >= 60
            ]
            removed_by_penalty = before_count - len(processed_results)

            if penalty_applied > 0:
                logger.info(
                    f"📉 L2 新鲜度衰减: {penalty_applied} 条文章被扣分, "
                    f"{removed_by_penalty} 条因低于60分被移除"
                )
        except Exception as e:
            logger.warning(f"L2 新鲜度衰减执行失败，安全跳过: {e}")

    # --- 原有 AI 输出去重 + L3 话题每日上限 ---
    processed_results = deduplicate_final_results(processed_results)

    if tracker:
        try:
            capped_results = []
            capped_count = 0
            for item in processed_results:
                p = item['parsed']
                tags = p.get('tags', [])

                # 突发保护: 90+ 分豁免话题上限
                if tracker.is_breakout(p.get('score', 0)):
                    capped_results.append(item)
                    continue

                remaining_cap = tracker.get_topic_cap(tags)
                if remaining_cap > 0:
                    capped_results.append(item)
                    # 临时记录以便后续条目计算时看到
                    tracker.record_scored_articles([item])
                else:
                    capped_count += 1

            if capped_count > 0:
                logger.info(f"🔒 L3 话题每日上限: {capped_count} 条因话题饱和被移除")
            processed_results = capped_results
        except Exception as e:
            logger.warning(f"L3 话题上限执行失败，安全跳过: {e}")

    final_data_by_category = defaultdict(list)
    final_list_for_ai = []

    for idx, item in enumerate(processed_results):
        item['final_index'] = idx + 1
        final_data_by_category[item['parsed'].get('category', '其他')].append(item)
        final_list_for_ai.append(item)

    top_news = final_list_for_ai[:15]

    # 保存 top_news 供 --skip-score / --article-only 复用
    try:
        top_news_file = os.path.join(_DIR, f"{datetime.now().strftime('%Y-%m-%d')}-top_news.json")
        with open(top_news_file, 'w', encoding='utf-8') as f:
            json.dump(top_news, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 top_news 已缓存: {os.path.basename(top_news_file)}")
    except Exception as e:
        logger.warning(f"缓存 top_news 失败（不影响主流程）: {e}")

    # Alert 去重：今天已发送过则跳过，但仍返回评分结果
    if _check_alert_sent_today():
        logger.info("📧 今日已发送过 alert 邮件，跳过邮件发送")
    else:
        logger.info(f"📧 正在生成邮件摘要...")
        email_summary = fetch_email_summary(top_news)

        logger.info(f"📧 正在构建 HTML 邮件...")
        html_content = build_html_email(final_data_by_category, email_summary)
        current_date = datetime.now().strftime("%Y-%m-%d")
        brand = DOMAIN['brand']
        brand_emoji = DOMAIN['brand_emoji']
        logger.info(f"📧 正在发送每日精选邮件...")
        send_email(f"{brand_emoji} {brand} {current_date} - 深度精选", html_content)
        _mark_alert_sent()
        logger.info(f"📧 每日精选邮件发送完成")

    # --- 记录最终输出到话题历史 (反馈回路) ---
    if tracker:
        try:
            tracker.record_scored_articles(final_list_for_ai)
        except Exception as e:
            logger.warning(f"记录话题历史失败（不影响主流程）: {e}")

    # --- 方案C: AI 动态关键词建议 (为明天的搜索优化) ---
    try:
        from keyword_scheduler import KeywordScheduler
        scheduler = KeywordScheduler(tracker=tracker)
        scheduler.suggest_dynamic_keywords(final_list_for_ai[:15])
    except Exception as e:
        logger.warning(f"动态关键词建议失败（不影响主流程）: {e}")

    return final_list_for_ai

if __name__ == "__main__":
    pass
