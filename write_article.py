#!/usr/bin/env python
# write_article.py — 独立深度文章写作工具 (智能输入版)
# 用法 — 什么都不用记，直接扔：
#   uv run python write_article.py CoWoS 先进封装 台积电
#   uv run python write_article.py https://example.com/some-news-article
#   uv run python write_article.py news_snippets.txt 固态电池
#   uv run python write_article.py                              ← 交互模式
# ============================================================


import json
import os
import re
import sys
import logging
import random
import hashlib
import requests
from datetime import datetime

from config_loader import load_config
from llm_client import call_ai_api, extract_json_from_text, emit_event
from fact_purifier import FactPurifier
from article_renderer import render_deep_article_to_html
from email_generator import send_email
from domain_config import DOMAIN
from text_utils import surgical_purify

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
KB_ROOT = os.path.join(_DIR, "knowledge_base")
STYLES_FILE = os.path.join(_DIR, "styles_hardtech.json")
FACT_ARCHIVE_FILE = os.path.join(_DIR, "fact_archive.json")
FACT_ARCHIVE_MAX = 10
DATA_HASH_FILE = os.path.join(_DIR, ".data_hash.json")

# #16 Prompt 模板注册表
PROMPT_TEMPLATES = {
    "earnings": {
        "trigger": ["财报", "营收", "净利润", "毛利率", "季报", "年报"],
        "inject": "写作重点：必须引用具体财务数字（营收、利润率、YoY增长率），用数据对比驱动分析。"
    },
    "tech_deep_dive": {
        "trigger": ["工艺", "制程", "架构", "封装", "良率", "技术路径"],
        "inject": "写作重点：必须解释技术原理（用类比让非专业读者理解），引用具体技术参数。"
    },
    "market_trend": {
        "trigger": ["市场", "份额", "竞争格局", "供应链", "产业链"],
        "inject": "写作重点：必须用市场份额数据支撑论点，分析竞争者的相对位势变化。"
    },
    "funding_m_and_a": {
        "trigger": ["融资", "收购", "并购", "IPO", "估值", "投资"],
        "inject": "写作重点：必须引用具体金额和估值倍数，分析交易的产业逻辑。"
    },
}


def detect_topic_type(topic):
    text = " ".join([topic.get('title_proposal', ''), topic.get('thesis', ''), " ".join(topic.get('outline', []))])
    scores = {}
    for key, tmpl in PROMPT_TEMPLATES.items():
        score = sum(1 for kw in tmpl["trigger"] if kw in text)
        if score > 0:
            scores[key] = score
    return max(scores, key=scores.get) if scores else None


# #19 变更检测
def compute_data_hash(news_list_or_material):
    if isinstance(news_list_or_material, str):
        return hashlib.md5(news_list_or_material[:2000].encode('utf-8')).hexdigest()[:12]
    key_parts = []
    for item in news_list_or_material[:15]:
        p = item.get('parsed', {})
        key_parts.append(p.get('title', '') + p.get('summary', '')[:50])
    return hashlib.md5("|".join(key_parts).encode('utf-8')).hexdigest()[:12]


def check_data_freshness(data_hash, today_str):
    stored = {}
    if os.path.exists(DATA_HASH_FILE):
        try:
            with open(DATA_HASH_FILE, 'r', encoding='utf-8') as f:
                stored = json.load(f)
        except Exception:
            pass
    last_hash = stored.get("last_hash", "")
    last_date = stored.get("date", "")
    if data_hash == last_hash and last_date == today_str:
        logger.warning(f"⚠️ 数据变更检测: 源数据与上次 ({last_date}) 完全相同")
        emit_event("data_unchanged", "源数据未变化", {"hash": data_hash})
        return False
    tmp = DATA_HASH_FILE + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({"last_hash": data_hash, "date": today_str}, f)
    os.replace(tmp, DATA_HASH_FILE)
    return True

# ===================== 独立情报采集 =====================

def fetch_jina_search(query, config):
    """用 Jina 搜索关键词，返回原始文本"""
    jina_key = config.api.get('jina_api_key')
    if not jina_key:
        logger.warning("未配置 Jina API Key，跳过搜索")
        return ""
    try:
        h = {"Accept": "application/json", "Authorization": f"Bearer {jina_key}"}
        r = requests.get(f"https://s.jina.ai/{query}", headers=h, timeout=40)
        if r.status_code == 200:
            d = r.json().get('data', [])
            return "\n".join([f"[{i.get('title')}] {(i.get('content') or '')[:1500]}" for i in d[:5]])
    except Exception as e:
        logger.warning(f"Jina 搜索异常: {e}")
    return ""


def fetch_jina_reader(url, config):
    """用 Jina Reader 抓取单个 URL 的正文"""
    jina_key = config.api.get('jina_api_key')
    if not jina_key:
        logger.warning("未配置 Jina API Key，跳过 Reader 抓取")
        return ""
    try:
        h = {"Accept": "application/json", "Authorization": f"Bearer {jina_key}"}
        r = requests.get(f"https://r.jina.ai/{url}", headers=h, timeout=40)
        if r.status_code == 200:
            data = r.json().get('data', {})
            title = data.get('title', '')
            content = data.get('content', '')[:3000]
            return f"[{title}]\n{content}"
    except Exception as e:
        logger.warning(f"Jina Reader 异常: {e}")
    return ""


def load_styles():
    if os.path.exists(STYLES_FILE):
        try:
            with open(STYLES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"Default": {"name": "标准硬科技评论", "persona": "专业、客观、数据驱动", "structure": "【现状】→【分析】→【结论】"}}


# ===================== 视角-风格关联 =====================

ANGLE_STYLE_MAP = {
    "宏观": ["ARK_Insight", "Stratechery", "Economist_Tech"],
    "中观": ["SemiAnalysis", "FirstPrinciples", "YoleGroup"],
    "微观": ["CICC_Research", "Bloomberg_Tech", "SemiAnalysis"],
}


# ===================== AI 选题规划 (V2 原版) =====================

def ai_plan_topic(raw_material, config):
    """V2: 根据原始素材，让 AI 规划选题和大纲"""
    logger.info("🧠 [V2] AI 正在基于素材自主规划选题与大纲...")
    sys_p = """你是一位硬科技创投领域的资深主编。
请根据提供的原始素材，策划一篇有深度的专栏文章选题。
标题要有张力和数据感，适合硬科技创投人群阅读。"""

    user_p = f"""【原始素材】
{raw_material[:6000]}

【任务】
请输出 JSON 格式的选题方案：
{{
    "title_proposal": "文章标题（有张力）",
    "outline": ["论述方向1", "论述方向2", "论述方向3", "论述方向4"],
    "core_entities": ["最核心的3-5个实体/公司/技术名词"]
}}"""

    res = call_ai_api([
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_p}
    ], description="Standalone Topic Planning")

    data = extract_json_from_text(res)
    if data and data.get('title_proposal'):
        logger.info(f"📌 AI 选题：{data['title_proposal']}")
        return data

    # 兜底
    logger.warning("AI 规划失败，使用默认框架")
    return {
        "title_proposal": "硬科技产业深度观察",
        "outline": ["产业现状与信号", "技术路径拆解", "竞争格局分析", "投资逻辑与展望"],
        "core_entities": []
    }


# ===================== AI 选题规划 (V3 论点驱动) =====================

def ai_plan_topic_v3(raw_material, config):
    """V3: 论点驱动选题——先发现中心论点，再构建论证弧"""
    logger.info("🧠 [V3] AI 正在发现中心论点与论证弧...")

    sys_p = """你是一位硬科技创投领域的资深主编。
你的首要任务不是"选视角"，而是从素材中发现一个有张力的、值得深度论述的中心论点。"""

    user_p = f"""【原始素材】
{raw_material[:6000]}

【任务零：发现中心论点】
仔细阅读素材，找到一个有张力的中心论点。
好的论点特征：
- 有争议性：不是"行业在发展"这种废话，而是"X正在侵蚀Y的护城河"
- 有因果关系：不是并列罗列，而是"A导致B，B触发C"
- 有具体实体：必须包含公司名、技术名或数据

【任务一：确定叙事粒度】
- 宏观：多条信息指向同一方向
- 中观：聚焦某条技术线或领域交叉
- 微观：围绕一家公司或一个事件

【任务二：构建论证弧】
围绕论点生成论证大纲：
- 开篇：抛出论点（用具体事件或数据切入）
- 中间：2-4 个论证角度
- 可选：反驳/复杂性
- 收束：对创投的含义

【输出格式（JSON）】
{{
    "thesis": "中心论点（100字以内，必须有主语、谓语、宾语和冲突张力）",
    "narrative_thread": "贯穿全文的暗线（一句话）",
    "granularity": "宏观/中观/微观",
    "title_proposal": "建议标题（必须包含具体实体名）",
    "outline": ["角度1标题", "角度2标题", ...],
    "counter_argument": "可能的反驳观点（可选）",
    "core_entities": ["核心实体列表"]
}}"""

    res = call_ai_api([
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_p}
    ], description="V3 Thesis Discovery")

    data = extract_json_from_text(res)
    if data and data.get('thesis') and data.get('title_proposal'):
        logger.info(f"📌 [V3] 论点: {data['thesis'][:60]}...")
        logger.info(f"📌 [V3] 选题: {data['title_proposal']}")
        return data

    # 兜底到 V2
    logger.warning("[V3] 论点发现失败，回退到 V2 选题")
    return ai_plan_topic(raw_material, config)


# ===================== 核心写作引擎 (V2 原版) ====================

def search_for_chapter(title, ch_title, config, today_str):
    """为章节提取精准关键词并搜索"""
    sys_p = "你是一位极简的搜索词提取器。请在JSON中返回提取的搜索词。"
    user_p = f"请从全文标题「{title}」和本章节标题「{ch_title}」中，提取出最核心的专有名词或公司名，作为搜索引擎查询条件。绝不要保留修饰语。控制在2-3个实词，空格隔开。\n输出格式：{{\"keywords\": \"词1 词2\"}}"
    res = call_ai_api([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], description="Extract Keys")
    data = extract_json_from_text(res)
    kw = data.get('keywords', '硬科技 数据') if data else '硬科技 数据'
    
    logger.info(f"🔍 章节专研侦察: {kw}")
    raw = fetch_jina_search(kw, config)
    if raw:
        return FactPurifier.purify(raw, kw, today_str)
    return ""


def summarize_context(current_summary, new_text):
    """压平上下文摘要"""
    sys_p = "你是一位上下文维护专员。请合并过去的文章摘要和刚刚生成的新章节，输出一段约500字的行文核心逻辑线索，不许丢失已经出场的公司名和论点。"
    user_p = f"【当前上下文概要】\n{current_summary}\n\n【最新章节原文】\n{new_text}\n\n请输出更新后的上下文概要："
    res = call_ai_api([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], description="Context Summarizer")
    return res if res else current_summary


def break_monotony(text):
    """随机在句号处插入换行，打破段落等长感"""
    new_text = ""
    segments = text.split('。')
    for i, seg in enumerate(segments):
        new_text += seg
        if i < len(segments) - 1:
            new_text += "。"
            if random.random() < 0.25 and not seg.endswith('\n'):
                new_text += "\n\n"
    return re.sub(r'\n{3,}', '\n\n', new_text)


def native_chinese_polish(text):
    """随机抽取段落进行中文母语化改写"""
    logger.info("🇨🇳 抽取段落进行纯正中文语感修正...")
    paragraphs = text.split('\n\n')
    valid_indices = [i for i, p in enumerate(paragraphs) if len(p) > 100 and not p.startswith(('#', '-', '>'))]
    if not valid_indices:
        return text

    sample_count = min(len(valid_indices), random.randint(2, 4))
    target_indices = random.sample(valid_indices, sample_count)

    for idx in target_indices:
        original_p = paragraphs[idx]
        sys_p = "你是一位极高水准的中文专栏文字编辑。你的工作是消除原稿中的「机器生成味」与「西式翻译语法」。"
        user_p = f"【当前段落如下】\n{original_p}\n\n【改写任务】\n请将这段文字改写为纯正的中文母语表达习惯：\n1. 斩断长句：将繁冗的「从句、长定语、被动语态」拆解为有主次的多个干净利落的短句！\n2. 动词主导：多用动词推动语意，拒绝「过度名词化」的英文表达模板。\n3. 保留干货：原文里的专有名词和数据必须100%保留！\n只需直接输出改写后且更为干练的一段话，不要有任何总结性或礼貌性的废话。"
        res = call_ai_api([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], description="Native Polish", custom_timeout=60)
        if res:
            paragraphs[idx] = res.strip()

    return "\n\n".join(paragraphs)


# ===================== 主写作流程 =====================

def write_article(topic, raw_material, style, config, send_mail=True):
    """
    核心写作流程：接收选题和原始素材，输出完整文章。
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    title = topic.get('title_proposal', '深度观察')
    outline = topic.get('outline', ["产业背景", "核心逻辑", "标的分析", "总结展望"])
    total = len(outline)

    logger.info(f"📌 选题：{title} | 章节数: {total} | 风格: {style['name']}")

    chapters_done = []
    context_summary = ""

    for i in range(total):
        ch_title = outline[i]
        logger.info(f"✍️ 写作第 {i+1}/{total} 段: {ch_title}")

        # 章节定向搜索
        chapter_facts = search_for_chapter(title, ch_title, config, today_str)

        sys_p = f"""你是一位拥有十几年经验的顶尖硬科技产业特稿主笔。今天是 {today_str}。
【核心纪律：消除废话与分块感】
1. 你的文章必须**100%基于下发的【原始素材】和【专属事实库】**进行分析，**严禁凭空生造观点或盲目垫字数**。
2. 凡涉及任何分析，必须引用给定的财报数字、工艺节点、融资金额或厂商动作作为证据！字字珠玑！
3. 绝对禁止使用宏大叙述词汇（如：赋能、底层逻辑、护城河、范式转移等空壳词）。
4. **格式禁令：绝对不允许写任何小标题、副标题，或者"第X章"！** 这是一篇整体行云流水的专栏文章，你只负责写出其中的几个核心段落。
5. 行文风格：《{style['name']}》：{style['persona']}
6. 每次只输出一连串的正文段落（字数不限，有多少干货就写多深），不需要开头寒暄，必须与上文极度自然地融为一体。"""

        last_words = chapters_done[-1][-300:] if chapters_done else "这是文章的开篇。"

        user_p = f"""【全文思路参考】{', '.join(outline)}
【当前写作焦点】请重点围绕这个方向深入论述：{ch_title}

【前文全文核心摘要】（用来理解整体逻辑，不要重复）：
{context_summary if context_summary else "文章开篇。"}

【你必须承接的上一段结尾原文】：
{last_words}

【用户提供的原始素材】
{raw_material[:4000]}

【本节独家硬核事实库】
{chapter_facts}

【任务】
立刻往下续写段落，直接输出正文。**没有任何小标题、粗体小标题或章节号！**用最平滑的过渡承接上一段结尾，用事实说话。"""

        chapter_content = call_ai_api([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_p}
        ], description=f"Write Part {i+1}", custom_timeout=350)

        if chapter_content:
            chapters_done.append(chapter_content)
            context_summary = summarize_context(context_summary, chapter_content)
        else:
            logger.error(f"第 {i+1} 段写作失败。")
            if not chapters_done:
                return None
            break

    # 组装
    logger.info("⚖️ 组装全文...")
    raw_full = f"# {title}\n\n"
    for c in chapters_done:
        raw_full += c.strip() + "\n\n"

    # 后处理
    raw_full = break_monotony(raw_full)
    raw_full = native_chinese_polish(raw_full)
    pure_article = surgical_purify(raw_full)

    # 存档
    year, month = datetime.now().strftime("%Y"), datetime.now().strftime("%m")
    kb_path = os.path.join(KB_ROOT, year, month)
    os.makedirs(kb_path, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', '', title)[:50]
    full_kb_path = os.path.abspath(os.path.join(kb_path, f"{today_str}-Standalone-{safe_name}.md"))

    with open(full_kb_path, 'w', encoding='utf-8') as f:
        f.write(pure_article)
    logger.info(f"🗄️ 文章已归档：{full_kb_path}")

    # 邮件（可选）
    if send_mail:
        html_article = render_deep_article_to_html(pure_article, title, style['name'], today_str)
        brand_emoji = DOMAIN['brand_emoji']
        send_email(f"{brand_emoji} [独立写作] {today_str} | {title}", html_article)
        logger.info("📧 邮件已发送。")

    return full_kb_path


# ===================== #14 跨篇事实归档 =====================

def load_fact_archive():
    if not os.path.exists(FACT_ARCHIVE_FILE):
        return []
    try:
        with open(FACT_ARCHIVE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_fact_archive(archive):
    tmp = FACT_ARCHIVE_FILE + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FACT_ARCHIVE_FILE)


def archive_article_facts(title, thesis, context_entities, today_str):
    archive = load_fact_archive()
    snapshot = {
        "date": today_str,
        "title": title,
        "thesis": thesis,
        "companies": list(context_entities.get("companies", []))[:20],
        "data_points": list(context_entities.get("data_points", []))[:15],
        "claims": list(context_entities.get("claims", []))[:10],
    }
    archive.append(snapshot)
    if len(archive) > FACT_ARCHIVE_MAX:
        archive = archive[-FACT_ARCHIVE_MAX:]
    save_fact_archive(archive)
    logger.info(f"📚 事实归档完成: {title[:30]}")


def build_archive_context():
    archive = load_fact_archive()
    if not archive:
        return ""
    recent = archive[-3:]
    lines = []
    for snap in recent:
        lines.append(f"【{snap['date']} {snap['title'][:30]}】论点: {snap['thesis'][:60]} | 公司: {', '.join(snap['companies'][:5])}")
    return "【近期文章事实背景】\n" + "\n".join(lines)


# ===================== #15 正则快速提取 =====================

def regex_extract_state(chapter_text):
    """正则快速提取：当 STATE JSON 解析失败时的兜底方案"""
    entities = {"companies": [], "data_points": [], "tech_terms": []}
    entities["companies"] = list(set(re.findall(
        r'[一-龥]{2,6}(?:科技|集团|半导体|电子|能源|智能|机器人|电池|芯片|通讯|光学|材料|仪器)',
        chapter_text
    )))
    entities["data_points"] = list(set(re.findall(
        r'\d+[\.\d]*\s*[%BWGbpsTB人民币€]|[＄$]\s*\d+[\.\d]*\s*[BMK]?|\d+[\.\d]*\s*[纳米nmμm]',
        chapter_text
    )))
    entities["tech_terms"] = list(set(re.findall(
        r'\b(?:CoWoS|HBM[0-9e]*|TSV|GAA|EUV|CUDA|RISC-V|LiDAR|SiC|GaN|Chiplet|UCIe|NVLink|PUE|SOC|ASIC|FPGA|GPU|TPU|NAND|DRAM|SRAM|FinFET|CFET)\b',
        chapter_text
    )))
    claims = []
    for sent in re.split(r'(?<=[。！？])', chapter_text):
        if 20 < len(sent) < 100 and re.search(r'(?:意味着|表明|说明|推动|导致|促使|标志着|预示)', sent):
            claims.append(sent.strip())
    entities["claims"] = claims[:5]
    if entities["companies"] or entities["data_points"] or entities["tech_terms"]:
        logger.info(f"📋 正则提取兜底: {len(entities['companies'])} 家公司, {len(entities['data_points'])} 数据")
        return {"logic": "", "entities": entities}
    return None


# ===================== V3 论点驱动写作引擎 =====================

def write_article_v3(topic, raw_material, styles, config, send_mail=True):
    """V3 论点驱动写作：风格DNA注入 + 双层摘要 + 跨章事实池 + 主编重写"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    title = topic.get('title_proposal', '深度观察')
    outline = topic.get('outline', ["产业背景", "核心逻辑", "标的分析", "总结展望"])
    thesis = topic.get('thesis', '')
    narrative_thread = topic.get('narrative_thread', '')
    counter_argument = topic.get('counter_argument', '')
    total = len(outline)

    # 根据粒度选择风格
    granularity = topic.get('granularity', '中观')
    style_pool = ANGLE_STYLE_MAP.get(granularity, ["SemiAnalysis"])
    chosen_id = random.choice(style_pool)
    style = styles.get(chosen_id, list(styles.values())[0])

    logger.info(f"📌 [V3] 选题: {title} | 章节: {total} | 风格: {style['name']}")
    logger.info(f"📌 [V3] 论点: {thesis[:60]}...")
    logger.info(f"📌 [V3] 暗线: {narrative_thread}")
    emit_event("pipeline_start", f"V3 写作启动: {title[:40]}")

    # #19 变更检测
    data_hash = compute_data_hash(raw_material)
    check_data_freshness(data_hash, today_str)

    # #16 模板注入
    topic_type = detect_topic_type(topic)
    template_inject = ""
    if topic_type:
        template_inject = f"\n5. {PROMPT_TEMPLATES[topic_type]['inject']}"
        logger.info(f"📝 选题类型: {topic_type}，已注入模板指令")

    chapters_done = []
    context_summary = {"logic": "", "entities": {"companies": [], "data_points": [], "claims": []}}
    chapter_facts_pool = []

    # #14 加载跨篇事实归档背景
    archive_context = build_archive_context()

    for i in range(total):
        ch_title = outline[i]
        logger.info(f"✍️ [V3] 写作第 {i+1}/{total} 段: {ch_title}")

        # 章节定向搜索
        chapter_facts = search_for_chapter(title, ch_title, config, today_str)
        chapter_facts_pool.append(chapter_facts)

        # 前序事实
        prev_facts = ""
        if len(chapter_facts_pool) >= 2:
            prev_facts = f"【前序章节核心事实】\n{chapter_facts_pool[-2]}\n{chapter_facts_pool[-1]}\n\n"
        elif chapter_facts_pool:
            prev_facts = f"【前序章节核心事实】\n{chapter_facts_pool[-1]}\n\n"

        # 反驳观点注入
        counter_prompt = ""
        if counter_argument and i == total - 1:
            counter_prompt = f"\n- 在论述中适当回应这个可能的反驳观点：{counter_argument}"

        logic = context_summary.get("logic", "") if isinstance(context_summary, dict) else str(context_summary)

        # #6 XML 分层 prompt + #7 尾部清单 + #8 标准名
        standard_entities = []
        for item in raw_material[:4000].split('\n'):
            if item.strip().startswith('-'):
                standard_entities.append(item.strip()[:30])
        entity_registry = ""
        if standard_entities:
            entity_registry = f"""
<entity_registry role="reference" priority="high">
引用以下实体时，只能使用标准名称，不得使用别名或自造名称。
</entity_registry>"""

        sys_p = f"""你是一位拥有十几年经验的顶尖硬科技产业特稿主笔。今天是 {today_str}。

<fact_ledger immutable="true" role="hard_constraint" priority="highest">
中心论点：{thesis}
叙事暗线：{narrative_thread}
当前章节：第{i+1}章/共{total}章
论证目标：{ch_title}
前文论证基础：{logic[-200:] if logic else "文章开篇。"}
{counter_prompt}
</fact_ledger>
{entity_registry}
<style_guide role="style_constraint" priority="highest">
行文风格：《{style['name']}》：{style['persona']}
风格DNA锚点（严格模仿此段的语感、句式和信息密度）：
{style.get('dna_sample', '')}
结构节奏指引（全篇宏观节奏应遵循此路径，但不要写出这些标签）：
{style.get('structure', '')}
</style_guide>

<task_context role="primary_directive" priority="high">
核心纪律：
1. 你的文章必须**100%基于下发的【原始素材】和【专属事实库】**进行分析，**严禁凭空生造观点或盲目垫字数**。
2. 凡涉及任何分析，必须引用给定的财报数字、工艺节点、融资金额或厂商动作作为证据！字字珠玑！
3. 绝对禁止使用宏大叙述词汇（如：赋能、底层逻辑、护城河、范式转移等空壳词）。
4. 每次只输出一连串的正文段落（字数不限，有多少干货就写多深），不需要开头寒暄，必须与上文极度自然地融为一体。
{template_inject}
</task_context>

<format_reminder mandatory="true">
**格式禁令：绝对不允许写任何小标题、副标题，或者"第X章"！** 这是一篇整体行云流水的专栏文章。

- 在你输出的正文段落之后，必须另起一行，输出如下结构化状态标记（不会出现在最终文章中，仅用于系统追踪）：

---STATE---
然后输出一个JSON代码块，用```json和```包裹，格式示例：
```json
{{"entities": {{"companies": ["本章新提到的公司名"], "data_points": ["本章新出现的关键数据"], "tech_terms": ["本章新出现的技术术语"]}}, "claims": ["本章做出的核心论断"], "narrative_position": "本章推进了什么", "unresolved_threads": ["未解答的问题"]}}
```
这个标记必须输出，否则系统无法追踪进度。
</format_reminder>

<tail_checklist role="entity_baseline" priority="high">
已出场实体和论断（后续章节必须保持一致）：
公司名：{', '.join(context_summary.get('entities', {}).get('companies', [])[:15]) if isinstance(context_summary, dict) else '无'}
关键数据：{', '.join(context_summary.get('entities', {}).get('data_points', [])[:10]) if isinstance(context_summary, dict) else '无'}
</tail_checklist>"""

        last_words = chapters_done[-1][-300:] if chapters_done else "这是文章的开篇。"

        user_p = f"""{archive_context + chr(10) if archive_context else ''}【全文思路参考】{', '.join(outline)}
【当前写作焦点】请重点围绕这个方向深入论述：{ch_title}

【前文全文核心摘要】（用来理解整体逻辑，不要重复）：
{logic if logic else "文章开篇。"}

【你必须承接的上一段结尾原文】：
{last_words}

{prev_facts}【用户提供的原始素材】
{raw_material[:4000]}

【本节独家硬核事实库】
{chapter_facts}

【任务】
立刻往下续写段落，直接输出正文。**没有任何小标题、粗体小标题或章节号！**
用最平滑的过渡承接上一段结尾，用事实说话。
每一段论述都必须服务于中心论点：{thesis}"""

        chapter_content = call_ai_api([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_p}
        ], description=f"V3 Write Part {i+1}", custom_timeout=350)

        if chapter_content:
            # 解析 ---STATE--- 结构化输出
            clean_text, chapter_state = parse_chapter_state(chapter_content)
            chapters_done.append(clean_text)

            if chapter_state:
                # 合并结构化状态（纯本地操作，0 LLM）
                context_summary = merge_chapter_state(context_summary, chapter_state)
            else:
                # #15 双通道兜底：先正则提取，再 LLM 摘要
                regex_result = regex_extract_state(clean_text)
                if regex_result:
                    logger.info("📋 正则提取成功，跳过 LLM 摘要")
                    regex_entities = regex_result.get("entities", {})
                    chapter_state_from_regex = {
                        "entities": {k: v for k, v in regex_entities.items() if k != "claims"},
                        "claims": regex_entities.get("claims", []),
                    }
                    context_summary = merge_chapter_state(context_summary, chapter_state_from_regex)
                else:
                    logger.info("📋 正则提取无结果，回退到 LLM 上下文摘要")
                    context_summary = summarize_context_v3(context_summary, chapter_content)

            # 优先级压缩
            context_summary = compress_context(context_summary, i, total)
            emit_event("chapter_done", f"第 {i+1}/{total} 章完成", {"chars": len(clean_text)})
        else:
            logger.error(f"第 {i+1} 段写作失败。")
            emit_event("chapter_failed", f"第 {i+1} 章写作失败")
            if not chapters_done:
                return None
            break

    # 组装
    logger.info("⚖️ [V3] 组装全文...")
    raw_full = f"# {title}\n\n"
    for c in chapters_done:
        raw_full += c.strip() + "\n\n"

    # 主编重写 + 硬核校验
    logger.info("✍️ [V3] 主编重写+硬核校验...")
    emit_event("editorial_start", "主编重写启动")
    entities = context_summary.get("entities", {}) if isinstance(context_summary, dict) else {}
    polished = editorial_rewrite_v3(raw_full, thesis, narrative_thread, entities, style)

    # 后处理
    polished = break_monotony(polished)
    polished = native_chinese_polish(polished)
    pure_article = surgical_purify(polished)

    # 存档
    year, month = datetime.now().strftime("%Y"), datetime.now().strftime("%m")
    kb_path = os.path.join(KB_ROOT, year, month)
    os.makedirs(kb_path, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', '', title)[:50]
    full_kb_path = os.path.abspath(os.path.join(kb_path, f"{today_str}-V3-{safe_name}.md"))

    # #10 原子写入
    tmp = full_kb_path + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(pure_article)
    os.replace(tmp, full_kb_path)
    logger.info(f"🗄️ [V3] 文章已归档：{full_kb_path}")
    emit_event("article_saved", f"文章已归档: {safe_name}", {"path": full_kb_path, "chars": len(pure_article)})

    # #14 归档事实快照
    entities = context_summary.get("entities", {}) if isinstance(context_summary, dict) else {}
    archive_article_facts(title, thesis, entities, today_str)

    if send_mail:
        html_article = render_deep_article_to_html(pure_article, title, style['name'], today_str)
        brand_emoji = DOMAIN['brand_emoji']
        send_email(f"{brand_emoji} [V3独立写作] {today_str} | {title}", html_article)
        logger.info("📧 邮件已发送。")

    return full_kb_path


def summarize_context_v3(current_summary, new_text):
    """V3 双层上下文摘要：实体追踪 + 逻辑线索"""
    logic = current_summary.get("logic", "") if isinstance(current_summary, dict) else str(current_summary)
    entities = current_summary.get("entities", {}) if isinstance(current_summary, dict) else {}

    sys_p = """你是一位上下文维护专员。请合并过去的文章摘要和刚刚生成的新章节，输出两部分：

【实体追踪】（JSON格式）
{"companies": ["已出场的公司名，去重"], "data_points": ["关键数据点"], "claims": ["已做出的核心论断"]}

【逻辑线索】（500字以内）
前文的核心论证逻辑和行文脉络"""

    user_p = f"【当前上下文概要】\n{logic}\n\n【已出场实体】\n{json.dumps(entities, ensure_ascii=False)}\n\n【最新章节原文】\n{new_text}\n\n请输出更新后的两部分内容："

    res = call_ai_api([
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_p}
    ], description="V3 Context Summarizer")

    if res:
        try:
            json_match = re.search(r'\{[^{}]*"companies"[^{}]*\}', res, re.DOTALL)
            if json_match:
                new_entities = json.loads(json_match.group())
                for key in ["companies", "data_points", "claims"]:
                    existing = set(entities.get(key, []))
                    existing.update(new_entities.get(key, []))
                    entities[key] = list(existing)
            logic_match = re.search(r'【逻辑线索】[：:]\s*(.*)', res, re.DOTALL)
            new_logic = logic_match.group(1).strip() if logic_match else res
            if len(new_logic) > 100:
                logic = new_logic
        except Exception:
            logic = res

    return {"logic": logic, "entities": entities}


def parse_chapter_state(chapter_text):
    """从章节文本末尾解析 ---STATE--- 结构化状态。返回 (clean_text, state_dict|None)"""
    match = re.search(r'---STATE---\s*```(?:json)?\s*(\{.*?\})\s*```', chapter_text, re.DOTALL)
    if match:
        try:
            state = json.loads(match.group(1))
            clean_text = chapter_text[:match.start()].rstrip()
            logger.info(f"📋 STATE 解析成功: {len(state.get('claims', []))} 条论断")
            return clean_text, state
        except json.JSONDecodeError:
            pass
    match2 = re.search(r'---STATE---\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', chapter_text, re.DOTALL)
    if match2:
        try:
            state = json.loads(match2.group(1))
            clean_text = chapter_text[:match2.start()].rstrip()
            logger.info(f"📋 STATE 解析成功(无代码块): {len(state.get('claims', []))} 条论断")
            return clean_text, state
        except json.JSONDecodeError:
            pass
    # 兜底：strip 任何 ---STATE--- 残留（防止 JSON 畸形时污染正则提取）
    stripped = re.split(r'---STATE---', chapter_text, maxsplit=1)[0].rstrip()
    return stripped, None


def merge_chapter_state(context_summary, chapter_state):
    """纯本地操作：将章节 STATE 合并到全局 context_summary（0 LLM 调用）"""
    entities = context_summary.get("entities", {"companies": [], "data_points": [], "claims": []})
    new_entities = chapter_state.get("entities", {})

    for key in ["companies", "data_points", "tech_terms"]:
        existing = set(entities.get(key, []))
        existing.update(new_entities.get(key, []))
        entities[key] = list(existing)

    existing_claims = set(entities.get("claims", []))
    existing_claims.update(chapter_state.get("claims", []))
    entities["claims"] = list(existing_claims)

    logic = context_summary.get("logic", "")
    position = chapter_state.get("narrative_position", "")
    if position:
        logic = f"{logic}\n{position}".strip()

    unresolved = entities.get("unresolved_threads", [])
    unresolved.extend(chapter_state.get("unresolved_threads", []))
    entities["unresolved_threads"] = unresolved[-10:]

    return {"logic": logic, "entities": entities}


def compress_context(context_summary, current_chapter_idx, total_chapters):
    """优先级压缩：当上下文过长时，按优先级丢弃低价值信息"""
    if current_chapter_idx < 2:
        return context_summary

    entities = context_summary.get("entities", {})
    logic = context_summary.get("logic", "")

    claims = entities.get("claims", [])
    if len(claims) > 15:
        entities["claims"] = claims[-15:]

    if len(logic) > 800:
        context_summary["logic"] = "..." + logic[-800:]

    unresolved = entities.get("unresolved_threads", [])
    if len(unresolved) > 5:
        entities["unresolved_threads"] = unresolved[-5:]

    tech_terms = entities.get("tech_terms", [])
    if len(tech_terms) > 30:
        entities["tech_terms"] = tech_terms[-30:]

    return context_summary


def _get_missing_items_v3(original_text, rewritten_text, manifest):
    """快速检测丢失项（不补回，仅返回丢失列表）"""
    missing = []
    for item in manifest["companies"] | manifest["tech_terms"]:
        if item and item not in rewritten_text:
            missing.append(("entity", item))
    for item in manifest["data_points"]:
        if item and item not in rewritten_text:
            missing.append(("data", item))
    for claim in manifest.get("core_claims", []):
        keywords = [w for w in re.findall(r'[一-龥]{2,}|[a-zA-Z]+|\d+', claim) if len(w) > 1]
        if keywords:
            ratio = sum(1 for kw in keywords if kw in rewritten_text) / len(keywords)
            if ratio < 0.6:
                missing.append(("claim", claim))
    return missing


def _build_editorial_prompt_v3(thesis, narrative_thread, manifest, style, previous_failures=None):
    """构建主编重写 prompt，后续轮次注入失败反馈"""
    base = f"""你是一位资深主编，负责为一篇已完稿的硬科技特稿做深度润色重写。

【你的任务】
在保持全文完整性和所有硬核数据的前提下，重写这篇文章：
1. 消除重复句式（尤其是"X不再是Y，而是Z"的反复使用）
2. 打破机器排版的规律感（长短句错落，段落节奏变化）
3. 加强章节之间的过渡，确保论点线索贯穿全文
4. 按风格DNA锚点校准全文语感
5. 消除明显的拼凑感

【风格DNA锚点】
{style.get('dna_sample', '')}

【中心论点】
{thesis}

【叙事暗线】
{narrative_thread}

【硬核清单——以下每一项都必须在你的输出中出现】
公司名：{', '.join(manifest['companies'])}
关键数据：{', '.join(manifest['data_points'])}
技术术语：{', '.join(manifest['tech_terms'])}
核心论断：
{chr(10).join('- ' + c for c in manifest['core_claims'])}"""

    if previous_failures:
        feedback_lines = []
        for i, (item_type, item) in enumerate(previous_failures[:5], 1):
            type_label = {"entity": "实体/术语", "data": "数据", "claim": "论断"}.get(item_type, "内容")
            feedback_lines.append(f"  {i}. [{type_label}] 「{item}」在你的输出中丢失了，必须保留")
        base += f"""

【上次重写失败项——你必须在本次输出中确保这些内容出现】
{chr(10).join(feedback_lines)}"""

    base += """

【输出要求】
输出完整的重写后全文。不要添加小标题或章节号。
硬核清单中的每一项都必须保留，否则视为任务失败。"""
    return base


def editorial_rewrite_v3(full_text, thesis, narrative_thread, context_entities, style):
    """V3 主编重写 + Gate 驱动重写循环 + 润色后校验"""
    logger.info("✍️ [V3] 主编重写启动...")

    manifest = {
        "companies": set(),
        "data_points": set(re.findall(r'\d+[\.\d]*\s*[%BWGbpsTB人民币€]|[＄$]\s*\d+[\.\d]*\s*[BMK]?|\d+[\.\d]*\s*[纳米nmμm]', full_text)),
        "tech_terms": set(re.findall(r'\b(?:CoWoS|HBM[0-9e]*|TSV|GAA|EUV|CUDA|RISC-V|LiDAR|SiC|GaN|Chiplet|UCIe|NVLink|PUE|SOC|ASIC|FPGA|GPU|TPU)\b', full_text)),
        "core_claims": context_entities.get("claims", []) if isinstance(context_entities, dict) else [],
        "thesis": thesis,
    }
    manifest["companies"].update(re.findall(r'[一-龥]{2,6}(?:科技|集团|半导体|电子|能源|智能|机器人|电池|芯片)', full_text))

    max_rewrites = 2
    best_result = full_text
    previous_failures = []

    for attempt in range(max_rewrites + 1):
        logger.info(f"  📝 主编重写 第 {attempt+1} 次{' (失败反馈: ' + str(len(previous_failures)) + ' 项)' if previous_failures else ''}...")

        sys_p = _build_editorial_prompt_v3(thesis, narrative_thread, manifest, style, previous_failures)

        rewritten = call_ai_api([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": f"请重写以下全文：\n\n{full_text}"}
        ], description=f"V3 Editorial Rewrite (attempt {attempt+1})", custom_timeout=600)

        if not rewritten:
            logger.warning(f"  主编重写第 {attempt+1} 次返回空")
            continue

        missing = _get_missing_items_v3(full_text, rewritten, manifest)

        if not missing:
            logger.info(f"  ✅ 第 {attempt+1} 次重写通过全部 Gate")
            best_result = rewritten
            break
        else:
            previous_failures = missing
            logger.warning(f"  第 {attempt+1} 次重写丢失 {len(missing)} 项")
            # 补回作为兜底
            sentences = re.split(r'(?<=[。！？])', full_text)
            patches = []
            for item_type, item in missing:
                for sent in sentences:
                    if item in sent and sent.strip():
                        patches.append(sent.strip())
                        break
                else:
                    if item_type == "claim":
                        patches.append(item + "。")
            if patches:
                patch_text = "\n".join(patches)
                last_period = rewritten.rfind("。")
                if last_period > len(rewritten) - 200:
                    best_result = rewritten[:last_period+1] + "\n\n" + patch_text + "\n" + rewritten[last_period+1:]
                else:
                    best_result = rewritten.rstrip() + "\n\n" + patch_text
            else:
                best_result = rewritten

    return best_result


# ===================== 智能输入分类器 =====================

def classify_input(token):
    """
    自动识别一个输入片段是什么类型：
    - 'url'     : 以 http:// 或 https:// 开头
    - 'file'    : 本地存在的文件路径
    - 'keyword' : 其他所有情况
    """
    token = token.strip()
    if re.match(r'^https?://', token):
        return 'url'
    if os.path.exists(token):
        return 'file'
    return 'keyword'


def smart_collect(tokens, config):
    """
    接收一组混合的输入 tokens，自动分类并采集素材。
    返回 (raw_material, summary_log)
    """
    urls = []
    files = []
    keywords = []

    for token in tokens:
        t = classify_input(token)
        if t == 'url':
            urls.append(token)
        elif t == 'file':
            files.append(token)
        else:
            keywords.append(token)

    raw_parts = []

    # 合并所有关键词为一个搜索查询
    if keywords:
        query = " ".join(keywords)
        logger.info(f"🔍 识别为关键词搜索: {query}")
        result = fetch_jina_search(query, config)
        if result:
            raw_parts.append(result)
            logger.info(f"   采集到 {len(result)} 字符素材")

    for url in urls:
        logger.info(f"🌐 识别为 URL，正在抓取: {url}")
        result = fetch_jina_reader(url, config)
        if result:
            raw_parts.append(result)
            logger.info(f"   采集到 {len(result)} 字符素材")

    for fp in files:
        logger.info(f"📄 识别为本地文件: {fp}")
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                content = f.read()
            raw_parts.append(content)
            logger.info(f"   加载了 {len(content)} 字符")
        except Exception as e:
            logger.warning(f"   文件读取失败: {e}")

    return "\n\n---\n\n".join(raw_parts) if raw_parts else ""


# ===================== CLI 入口 =====================

def main():
    # 收集命令行中脚本名之后的所有参数
    raw_args = sys.argv[1:]

    # 如果什么都没传，进入交互模式
    if not raw_args:
        print("=" * 60)
        print("  📝 独立深度文章写作工具 (智能输入)")
        print("=" * 60)
        print("  直接输入任何内容，系统自动识别：")
        print("    · URL      → 自动抓取网页正文")
        print("    · 文件路径  → 自动读取本地文件")
        print("    · 其他文字  → 当作关键词搜索")
        print("  多个输入用空格隔开，也可以混合使用。")
        print("-" * 60)
        user_input = input("请输入> ").strip()
        if not user_input:
            print("❌ 未输入任何内容，退出。")
            sys.exit(0)
        raw_args = user_input.split()

    config = load_config()
    styles = load_styles()
    pipeline = config.app_settings.get('writing_pipeline', 'v2')

    if pipeline == 'v3':
        logger.info(f"🚀 独立写作引擎启动 | V3 论点驱动模式")
    else:
        style = styles[random.choice(list(styles.keys()))]
        logger.info(f"🚀 独立写作引擎启动 | V2 模式 | 风格: {style['name']}")

    # 自动采集
    raw_material = smart_collect(raw_args, config)

    if not raw_material:
        logger.error("❌ 未采集到任何有效素材，无法写作。")
        sys.exit(1)

    logger.info(f"📊 素材汇总: {len(raw_material)} 字符")

    if pipeline == 'v3':
        # V3: 论点驱动选题 + 论点驱动写作
        topic = ai_plan_topic_v3(raw_material, config)
        output_path = write_article_v3(
            topic=topic,
            raw_material=raw_material,
            styles=styles,
            config=config,
            send_mail=True
        )
    else:
        # V2: 原版选题 + 原版写作
        topic = ai_plan_topic(raw_material, config)
        output_path = write_article(
            topic=topic,
            raw_material=raw_material,
            style=style,
            config=config,
            send_mail=True
        )

    if output_path:
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ 文章生成完毕！")
        logger.info(f"📁 文件路径: {output_path}")
        logger.info(f"{'='*60}")
    else:
        logger.error("文章生成失败。")
        sys.exit(1)


if __name__ == "__main__":
    main()
