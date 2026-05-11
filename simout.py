import json
import re
import os
import hashlib
from simhash import Simhash, SimhashIndex
from datetime import datetime, timedelta
import logging
from config_loader import load_config
from topic_tracker import is_blacklisted_url

logger = logging.getLogger(__name__)

HISTORY_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'simhash_history.json')

# --- 辅助函数 (复用 V3 逻辑) ---

def extract_url(text):
    match = re.search(r'<(https?://[^>]+)>', text)
    return match.group(1) if match else None

def get_title_skeleton(text):
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\s*[\-\|]\s*[a-zA-Z0-9\u4e00-\u9fa5\s]+$', '', text)
    text = re.sub(r'[^\w\u4e00-\u9fa5]', ' ', text).lower()
    words = [w for w in text.split() if len(w) > 1]
    words.sort()
    return "".join(words)

def preprocess_text(text):
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('...', ' ')
    text = text.lower()
    text = re.sub(r'[^\w\u4e00-\u9fa5]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def extract_pure_title(article_text):
    lines = article_text.strip().split('\n')
    title = lines[0].strip()
    title = re.sub(r'<https?://[^>]+>', '', title).strip()
    if '...' in title and len(title) > 50:
        parts = title.split('...')
        if len(parts[0]) > 10: title = parts[0].strip()
    title = re.sub(r'\s*[\-\|]\s*[a-zA-Z0-9\u4e00-\u9fa5\s]+$', '', title).strip()
    return title

# --- 历史记录管理 ---

def load_history():
    if not os.path.exists(HISTORY_FILE_PATH):
        return {"hashes": {}, "urls": {}, "skeletons": {}}
    try:
        with open(HISTORY_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and "hashes" not in data:
                return {"hashes": data, "urls": {}, "skeletons": {}}
            if "skeletons" not in data: data["skeletons"] = {}
            return data
    except Exception:
        return {"hashes": {}, "urls": {}, "skeletons": {}}

def save_history(history_data):
    try:
        config = load_config()
        retention_days = config.app_settings.get('history_retention_days', 90)
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        for category in ["hashes", "urls", "skeletons"]:
            history_data[category] = {k: v for k, v in history_data[category].items() if v >= cutoff}
        with open(HISTORY_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存去重历史记录失败: {e}")

# --- 生产环境入口 ---

def deduplicate_news_optimized(input_file_path, output_file_path, threshold=None):
    """V3 三级去重引擎, 完整复用"""
    config = load_config()
    sim_threshold = threshold if threshold is not None else config.app_settings.get('simhash_threshold', 5)
    
    if not os.path.exists(input_file_path):
        logger.error(f"去重输入文件 '{input_file_path}' 不存在。")
        return False

    try:
        with open(input_file_path, 'r', encoding='utf-8') as f:
            original_data = json.load(f)
    except Exception as e:
        logger.error(f"加载去重数据失败: {e}")
        return False

    history = load_history()
    index_objects = []
    max_hash_entries = 5000
    hash_items = list(history["hashes"].items())
    if len(hash_items) > max_hash_entries:
        hash_items.sort(key=lambda x: x[1], reverse=True)
        hash_items = hash_items[:max_hash_entries]
        logger.info(f"📦 Simhash 历史超限，仅加载最近 {max_hash_entries} 条")
    for h_val_str, _ in hash_items:
        try:
            index_objects.append((f"hist:{h_val_str}", Simhash(int(h_val_str))))
        except Exception: continue

    processed_list = []
    article_idx = 0
    blacklisted_count = 0
    for mail_index, mail in enumerate(original_data):
        if 'body' not in mail or not isinstance(mail['body'], dict): continue
        for category, articles in mail['body'].items():
            for text in articles:
                if not text.strip(): continue
                url = extract_url(text)
                # L1: URL 模式黑名单过滤（标签页/聚合页等非文章 URL）
                if is_blacklisted_url(url):
                    blacklisted_count += 1
                    continue
                url_fp = hashlib.md5(url.encode('utf-8')).hexdigest() if url else None
                title = extract_pure_title(text)
                skeleton = get_title_skeleton(title)
                clean_text = preprocess_text(title)
                if not clean_text: continue
                s_hash = Simhash(clean_text)
                processed_list.append({
                    "orig": text, "cat": category, "mail_idx": mail_index,
                    "url_fp": url_fp, "skeleton": skeleton, "s_hash": s_hash,
                    "id": f"curr:{article_idx}"
                })
                index_objects.append((f"curr:{article_idx}", s_hash))
                article_idx += 1
    if blacklisted_count:
        logger.info(f"🚫 L1 URL黑名单过滤: {blacklisted_count} 条非文章URL已移除")

    if not processed_list:
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
        return True

    try:
        index = SimhashIndex(index_objects, k=sim_threshold)
    except Exception as e:
        logger.error(f"构建 Simhash 索引失败: {e}")
        return False

    kept_items = []
    seen_urls_batch = set()
    seen_skeletons_batch = set()
    now_str = datetime.now().strftime("%Y-%m-%d")

    for item in processed_list:
        is_dup = False
        if item["url_fp"] and (item["url_fp"] in history["urls"] or item["url_fp"] in seen_urls_batch):
            is_dup = True
        if not is_dup and item["skeleton"]:
            if item["skeleton"] in history["skeletons"] or item["skeleton"] in seen_skeletons_batch:
                is_dup = True
        if not is_dup:
            near_dups = index.get_near_dups(item["s_hash"])
            for dup_id in near_dups:
                if dup_id == item["id"]: continue
                if dup_id.startswith("hist:"):
                    is_dup = True; break
                if any(k["id"] == dup_id for k in kept_items):
                    is_dup = True; break
        if not is_dup:
            kept_items.append(item)
            if item["url_fp"]: seen_urls_batch.add(item["url_fp"])
            if item["skeleton"]: seen_skeletons_batch.add(item["skeleton"])
            if item["url_fp"]: history["urls"][item["url_fp"]] = now_str
            if item["skeleton"]: history["skeletons"][item["skeleton"]] = now_str
            history["hashes"][str(item["s_hash"].value)] = now_str

    save_history(history)

    new_data = []
    for m_idx, orig_mail in enumerate(original_data):
        mail_kept = [it for it in kept_items if it["mail_idx"] == m_idx]
        if not mail_kept: continue
        new_body = {}
        for it in mail_kept:
            if it["cat"] not in new_body: new_body[it["cat"]] = []
            new_body[it["cat"]].append(it["orig"])
        new_data.append({
            "subject": orig_mail.get("subject"),
            "from": orig_mail.get("from"),
            "date": orig_mail.get("date"),
            "body": new_body
        })

    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, ensure_ascii=False, indent=4)

    logger.info(f"去重完成: 原始 {len(processed_list)} 条 → 保留 {len(kept_items)} 条")
    return True
