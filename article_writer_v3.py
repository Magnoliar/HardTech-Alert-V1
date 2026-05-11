import json
import os
import re
import logging
import random
import hashlib
import requests
from datetime import datetime
from config_loader import load_config
from llm_client import call_ai_api, extract_json_from_text, emit_event
from email_generator import send_email
from article_renderer import render_deep_article_to_html
from domain_config import DOMAIN
from text_utils import surgical_purify

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE_V3 = os.path.join(_DIR, ".task_state_v3.json")
MEMORY_FILE = os.path.join(_DIR, "strategic_memory.json")
KB_ROOT = os.path.join(_DIR, "knowledge_base")
STYLES_FILE = os.path.join(_DIR, "styles_hardtech.json")
FACT_ARCHIVE_FILE = os.path.join(_DIR, "fact_archive.json")
FACT_ARCHIVE_MAX = 10  # 保留最近 10 篇文章的事实快照
DATA_HASH_FILE = os.path.join(_DIR, ".data_hash.json")

# 视角-风格关联表：根据叙事粒度匹配最合适的风格池
ANGLE_STYLE_MAP = {
    "宏观": ["ARK_Insight", "Stratechery", "Economist_Tech"],
    "中观": ["SemiAnalysis", "FirstPrinciples", "YoleGroup"],
    "微观": ["CICC_Research", "Bloomberg_Tech", "SemiAnalysis"],
}

# #16 Prompt 模板注册表：根据选题类型注入额外的写作指令
PROMPT_TEMPLATES = {
    "earnings": {
        "trigger": ["财报", "营收", "净利润", "毛利率", "季报", "年报"],
        "inject": "写作重点：必须引用具体财务数字（营收、利润率、YoY增长率），用数据对比驱动分析，避免空洞的趋势描述。"
    },
    "tech_deep_dive": {
        "trigger": ["工艺", "制程", "架构", "封装", "良率", "技术路径"],
        "inject": "写作重点：必须解释技术原理（用类比让非专业读者理解），引用具体技术参数（nm、层叠数、带宽），对比不同技术路径的优劣。"
    },
    "market_trend": {
        "trigger": ["市场", "份额", "竞争格局", "供应链", "产业链"],
        "inject": "写作重点：必须用市场份额数据支撑论点，分析竞争者的相对位势变化，指出产业链上下游的联动效应。"
    },
    "funding_m_and_a": {
        "trigger": ["融资", "收购", "并购", "IPO", "估值", "投资"],
        "inject": "写作重点：必须引用具体金额和估值倍数，分析交易的产业逻辑（不只是财务逻辑），指出对竞争格局的影响。"
    },
}


def _detect_topic_type(topic):
    """根据选题内容检测文章类型，返回匹配的模板 key"""
    text = " ".join([
        topic.get('title_proposal', ''),
        topic.get('thesis', ''),
        " ".join(topic.get('outline', []))
    ])
    scores = {}
    for key, tmpl in PROMPT_TEMPLATES.items():
        score = sum(1 for kw in tmpl["trigger"] if kw in text)
        if score > 0:
            scores[key] = score
    if scores:
        return max(scores, key=scores.get)
    return None


class ArticleWriterV3:
    """
    V3 论点驱动写作系统
    核心改变：
    1. 论点贯穿：每章写作都围绕同一个中心论点
    2. 风格DNA注入：dna_sample 和 structure 实际参与 prompt
    3. 双层上下文摘要：实体追踪 + 逻辑线索
    4. 跨章事实池：前序章节事实注入后续章节
    5. 主编重写+硬核校验：允许自由重写，程序化保证内容不丢失
    """

    def __init__(self):
        self.config = load_config()
        self.today_str = datetime.now().strftime("%Y-%m-%d")

        # 风格延迟到 run() 中选择
        self.styles = self._load_styles()
        self.style = None

        # 持久化跨篇记忆
        self.memory = self._load_memory()
        # V3 独立临时状态
        self.state = self._load_state()

        self._llm_call_count = 0
        self.chapter_facts_pool = []
        self.news_list = []

    def _load_styles(self):
        if os.path.exists(STYLES_FILE):
            try:
                with open(STYLES_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"V3加载styles失败: {e}")
        return {"Default": {"name": "标准硬科技评论", "persona": "专业、客观、数据驱动", "dna_sample": "", "structure": "【现状】→【分析】→【结论】"}}

    def _load_memory(self):
        if not os.path.exists(MEMORY_FILE):
            return {"storylines": {}}
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"storylines": {}}

    def _load_state(self):
        if os.path.exists(STATE_FILE_V3):
            try:
                with open(STATE_FILE_V3, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                    if d.get('date') == self.today_str:
                        return d
            except Exception:
                pass
        return {"date": self.today_str, "chapters_done": [], "context_summary": {"logic": "", "entities": {"companies": [], "data_points": [], "claims": []}}, "final_text": ""}

    def _save_state(self):
        """#10 原子写入：先写 tmp 再 rename，防止崩溃损坏"""
        tmp = STATE_FILE_V3 + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE_V3)

    def _search_jina_for_chapter(self, search_query):
        """为特定章节做定向侦察"""
        logger.info(f"🔍 V3 章节专研侦察: {search_query}")
        jina_key = self.config.api.get('jina_api_key')
        try:
            h = {"Accept": "application/json", "Authorization": f"Bearer {jina_key}"}
            r = requests.get(f"https://s.jina.ai/{search_query}", headers=h, timeout=40)
            if r.status_code == 200:
                d = r.json().get('data', [])
                raw_text = "\n".join([f"[{i.get('title')}] {(i.get('content') or '')[:1200]}" for i in d[:4]])
                from fact_purifier import FactPurifier
                return FactPurifier.purify(raw_text, search_query, self.today_str)
        except Exception as e:
            logger.warning(f"Jina 章节专研异常: {e}")
        return ""

    def _get_chapter_keywords(self, title, ch_title):
        sys_p = "你是一位极简的搜索词提取器小助手。请在JSON中返回提取的搜索词。"
        user_p = f"请从全文标题「{title}」和本章节标题「{ch_title}」中，提取出最核心的专有名词或公司名，作为搜索引擎查询条件。绝不要保留修饰语。控制在2-3个实词，空格隔开。\n输出格式：{{\"keywords\": \"词1 词2\"}}"
        res = call_ai_api([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], description="V3 Extract Keys")
        self._llm_call_count += 1
        data = extract_json_from_text(res)
        if data and data.get('keywords'):
            return data.get('keywords')
        return "硬科技 财报 数据"

    def _summarize_context(self, current_summary, new_chapter_text):
        """双层上下文摘要：实体追踪 + 逻辑线索"""
        logic = current_summary.get("logic", "") if isinstance(current_summary, dict) else str(current_summary)
        entities = current_summary.get("entities", {}) if isinstance(current_summary, dict) else {}

        sys_p = """你是一位上下文维护专员。请合并过去的文章摘要和刚刚生成的新章节，输出两部分：

【实体追踪】（JSON格式）
{"companies": ["已出场的公司名，去重"], "data_points": ["关键数据点"], "claims": ["已做出的核心论断（保留原话的关键逻辑）"]}

【逻辑线索】（500字以内）
前文的核心论证逻辑和行文脉络

请严格按此格式输出，实体追踪部分必须是合法JSON。"""

        user_p = f"【当前上下文概要】\n{logic}\n\n【已出场实体】\n{json.dumps(entities, ensure_ascii=False)}\n\n【最新章节原文】\n{new_chapter_text}\n\n请输出更新后的两部分内容："

        res = call_ai_api([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_p}
        ], description="V3 Context Summarizer")
        self._llm_call_count += 1

        if res:
            # 尝试解析双层结构
            try:
                # 提取 JSON 实体追踪
                json_match = re.search(r'\{[^{}]*"companies"[^{}]*\}', res, re.DOTALL)
                if json_match:
                    new_entities = json.loads(json_match.group())
                    # 合并实体（追加去重）
                    for key in ["companies", "data_points", "claims"]:
                        existing = set(entities.get(key, []))
                        existing.update(new_entities.get(key, []))
                        entities[key] = list(existing)

                # 提取逻辑线索（JSON 之后的文本）
                logic_match = re.search(r'【逻辑线索】[：:]\s*(.*)', res, re.DOTALL)
                new_logic = logic_match.group(1).strip() if logic_match else res
                if len(new_logic) > 100:
                    logic = new_logic

            except Exception:
                logic = res

        return {"logic": logic, "entities": entities}

    def _parse_chapter_state(self, chapter_text):
        """从章节文本末尾解析 ---STATE--- 结构化状态。返回 (clean_text, state_dict|None)"""
        match = re.search(r'---STATE---\s*```(?:json)?\s*(\{.*?\})\s*```', chapter_text, re.DOTALL)
        if match:
            try:
                state = json.loads(match.group(1))
                clean_text = chapter_text[:match.start()].rstrip()
                logger.info(f"📋 STATE 解析成功: {len(state.get('claims', []))} 条论断, {len(state.get('entities', {}).get('companies', []))} 家公司")
                return clean_text, state
            except json.JSONDecodeError as e:
                logger.warning(f"STATE JSON 解析失败: {e}")
        # 尝试不带 ``` 包裹的格式
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
        if stripped != chapter_text.rstrip():
            logger.info("📋 STATE 块已剥离（JSON 畸形），返回清理后文本")
        return stripped, None

    def _merge_chapter_state(self, context_summary, chapter_state):
        """纯本地操作：将章节 STATE 合并到全局 context_summary（0 LLM 调用）"""
        entities = context_summary.get("entities", {"companies": [], "data_points": [], "claims": []})
        new_entities = chapter_state.get("entities", {})

        for key in ["companies", "data_points", "tech_terms"]:
            existing = set(entities.get(key, []))
            existing.update(new_entities.get(key, []))
            entities[key] = list(existing)

        # claims 追加（保留原文，去重）
        existing_claims = set(entities.get("claims", []))
        existing_claims.update(chapter_state.get("claims", []))
        entities["claims"] = list(existing_claims)

        # 更新 logic（用 narrative_position 追加）
        logic = context_summary.get("logic", "")
        position = chapter_state.get("narrative_position", "")
        if position:
            logic = f"{logic}\n{position}".strip()

        # 追踪 unresolved_threads
        unresolved = entities.get("unresolved_threads", [])
        unresolved.extend(chapter_state.get("unresolved_threads", []))
        entities["unresolved_threads"] = unresolved[-10:]  # 保留最近 10 条

        return {"logic": logic, "entities": entities}

    def _compress_context(self, context_summary, current_chapter_idx, total_chapters):
        """优先级压缩：当上下文过长时，按优先级丢弃低价值信息"""
        entities = context_summary.get("entities", {})
        logic = context_summary.get("logic", "")

        # 仅在第 3 章之后开始压缩
        if current_chapter_idx < 2:
            return context_summary

        # 压缩 claims：最多保留 15 条最近的
        claims = entities.get("claims", [])
        if len(claims) > 15:
            entities["claims"] = claims[-15:]

        # 压缩 logic：截断到最近 800 字
        if len(logic) > 800:
            context_summary["logic"] = "..." + logic[-800:]

        # 压缩 unresolved_threads：保留最近 5 条
        unresolved = entities.get("unresolved_threads", [])
        if len(unresolved) > 5:
            entities["unresolved_threads"] = unresolved[-5:]

        # 压缩 tech_terms：保留最近 30 个
        tech_terms = entities.get("tech_terms", [])
        if len(tech_terms) > 30:
            entities["tech_terms"] = tech_terms[-30:]

        return context_summary

    # #19 变更检测门控
    def _compute_data_hash(self, news_list):
        """计算新闻素材的哈希值，用于检测数据新鲜度"""
        key_parts = []
        for item in news_list[:15]:
            p = item.get('parsed', {})
            key_parts.append(p.get('title', '') + p.get('summary', '')[:50])
        raw = "|".join(key_parts)
        return hashlib.md5(raw.encode('utf-8')).hexdigest()[:12]

    def _check_data_freshness(self, news_list):
        """检测源数据是否自上次以来有变化"""
        current_hash = self._compute_data_hash(news_list)
        stored = {}
        if os.path.exists(DATA_HASH_FILE):
            try:
                with open(DATA_HASH_FILE, 'r', encoding='utf-8') as f:
                    stored = json.load(f)
            except Exception:
                pass

        last_hash = stored.get("last_hash", "")
        last_date = stored.get("date", "")

        if current_hash == last_hash and last_date == self.today_str:
            logger.warning(f"⚠️ 数据变更检测: 源数据与上次 ({last_date}) 完全相同，可能生成重复内容")
            emit_event("data_unchanged", "源数据未变化", {"hash": current_hash})
            return False

        # 更新哈希（原子写入）
        tmp = DATA_HASH_FILE + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({"last_hash": current_hash, "date": self.today_str}, f)
        os.replace(tmp, DATA_HASH_FILE)
        emit_event("data_changed", "源数据已更新", {"old_hash": last_hash, "new_hash": current_hash})
        return True

    # #14 跨篇事实归档
    def _load_fact_archive(self):
        """加载历史文章事实快照"""
        if not os.path.exists(FACT_ARCHIVE_FILE):
            return []
        try:
            with open(FACT_ARCHIVE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def _save_fact_archive(self, archive):
        """原子写入事实归档"""
        tmp = FACT_ARCHIVE_FILE + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(archive, f, ensure_ascii=False, indent=2)
        os.replace(tmp, FACT_ARCHIVE_FILE)

    def _archive_article_facts(self, title, thesis, context_entities):
        """文章完成后，归档核心事实快照"""
        archive = self._load_fact_archive()
        snapshot = {
            "date": self.today_str,
            "title": title,
            "thesis": thesis,
            "companies": list(context_entities.get("companies", []))[:20],
            "data_points": list(context_entities.get("data_points", []))[:15],
            "claims": list(context_entities.get("claims", []))[:10],
        }
        archive.append(snapshot)
        # 保留最近 N 篇
        if len(archive) > FACT_ARCHIVE_MAX:
            archive = archive[-FACT_ARCHIVE_MAX:]
        self._save_fact_archive(archive)
        logger.info(f"📚 事实归档完成: {title[:30]} ({len(snapshot['companies'])} 家公司, {len(snapshot['claims'])} 条论断)")

    def _build_archive_context(self):
        """从归档中构建背景上下文，注入后续文章"""
        archive = self._load_fact_archive()
        if not archive:
            return ""
        recent = archive[-3:]  # 最近 3 篇
        lines = []
        for snap in recent:
            lines.append(f"【{snap['date']} {snap['title'][:30]}】论点: {snap['thesis'][:60]} | 公司: {', '.join(snap['companies'][:5])}")
        return "【近期文章事实背景】\n" + "\n".join(lines)

    # #15 双通道记忆提取：正则快速提取
    def _regex_extract_state(self, chapter_text):
        """正则快速提取：当 STATE JSON 解析失败时的兜底方案"""
        entities = {"companies": [], "data_points": [], "tech_terms": []}

        # 公司名：中文公司名模式
        entities["companies"] = list(set(re.findall(
            r'[一-龥]{2,6}(?:科技|集团|半导体|电子|能源|智能|机器人|电池|芯片|通讯|光学|材料|仪器)',
            chapter_text
        )))

        # 数据点：数字+单位
        entities["data_points"] = list(set(re.findall(
            r'\d+[\.\d]*\s*[%BWGbpsTB人民币€]|[＄$]\s*\d+[\.\d]*\s*[BMK]?|\d+[\.\d]*\s*[纳米nmμm]',
            chapter_text
        )))

        # 技术术语
        entities["tech_terms"] = list(set(re.findall(
            r'\b(?:CoWoS|HBM[0-9e]*|TSV|GAA|EUV|CUDA|RISC-V|LiDAR|SiC|GaN|Chiplet|UCIe|NVLink|PUE|SOC|ASIC|FPGA|GPU|TPU|NAND|DRAM|SRAM|FinFET|GAA|CFET)\b',
            chapter_text
        )))

        # 论断：提取包含因果关系的句子
        claims = []
        for sent in re.split(r'(?<=[。！？])', chapter_text):
            if len(sent) > 20 and len(sent) < 100:
                if re.search(r'(?:意味着|表明|说明|推动|导致|促使|标志着|预示)', sent):
                    claims.append(sent.strip())
        entities["claims"] = claims[:5]

        if entities["companies"] or entities["data_points"] or entities["tech_terms"]:
            logger.info(f"📋 正则提取兜底: {len(entities['companies'])} 家公司, {len(entities['data_points'])} 数据, {len(entities['tech_terms'])} 术语")
            return {"logic": "", "entities": entities}
        return None

    def _build_standard_entity_list(self):
        """#8 从 news_list 构建标准实体名列表，防止 AI 使用不一致的名称"""
        entities = set()
        for item in self.news_list:
            p = item.get('parsed', {})
            entities.update(p.get('keywords', []))
            entities.update(p.get('tags', []))
        # 也从当前 context_summary 的 entities 中取
        ctx_entities = self.state.get('context_summary', {}).get('entities', {})
        entities.update(ctx_entities.get('companies', []))
        # 去空、去短
        return sorted([e for e in entities if e and len(e) > 1])

    def _build_chapter_sys_prompt(self, i, total_chapters, context_summary, thesis, narrative_thread, outline, template_inject=""):
        """#6 分层 prompt 架构 + #7 尾部清单 + #8 标准名 + #9 偏离警告 + #16 模板注入"""
        logic = context_summary.get("logic", "") if isinstance(context_summary, dict) else str(context_summary)
        last_words = self.state.get('chapters_done', [])
        last_words = last_words[-1][-300:] if last_words else "这是文章的开篇。"

        # 前序章节事实
        prev_facts = ""
        if len(self.chapter_facts_pool) >= 2:
            prev_facts = f"【前序章节核心事实】\n{self.chapter_facts_pool[-2]}\n{self.chapter_facts_pool[-1]}\n\n"
        elif self.chapter_facts_pool:
            prev_facts = f"【前序章节核心事实】\n{self.chapter_facts_pool[-1]}\n\n"

        # 反驳观点
        counter = self.state.get('counter_argument', '')
        counter_prompt = ""
        if counter and i == total_chapters - 1:
            counter_prompt = f"\n- 在论述中适当回应这个可能的反驳观点：{counter}"

        # #8 标准实体名列表
        standard_entities = self._build_standard_entity_list()
        entity_registry = ""
        if standard_entities:
            entity_list = "、".join(standard_entities[:30])
            entity_registry = f"""
<entity_registry role="reference" priority="high">
引用以下实体时，只能使用下列标准名称，不得使用别名、缩写或自造名称：
{entity_list}
</entity_registry>"""

        # #9 状态偏离警告（检查 context_summary 中的 unresolved_threads）
        warnings_section = ""
        unresolved = context_summary.get("entities", {}).get("unresolved_threads", []) if isinstance(context_summary, dict) else []
        if unresolved:
            warning_items = "\n".join(f"  - {t}" for t in unresolved[-3:])
            warnings_section = f"""
<consistency_warnings role="alert" priority="high">
以下问题在前文提出但尚未解答，请在本章中适当回应或推进：
{warning_items}
</consistency_warnings>"""

        # ---STATE--- 输出指令
        state_instruction = """
- 在你输出的正文段落之后，必须另起一行，输出如下结构化状态标记（不会出现在最终文章中，仅用于系统追踪）：

---STATE---
然后输出一个JSON代码块，用```json和```包裹，格式示例：
```json
{
  "entities": {
    "companies": ["本章新提到的公司名"],
    "data_points": ["本章新出现的关键数据"],
    "tech_terms": ["本章新出现的技术术语"]
  },
  "claims": ["本章做出的核心论断（原话摘录，每条30-80字）"],
  "narrative_position": "本章在论证弧中推进了什么（一句话）",
  "unresolved_threads": ["本章提出但未完全解答的问题"]
}
```
这个标记必须输出，否则系统无法追踪进度。"""

        # #6 XML 分层 prompt 架构
        sys_p = f"""你是一位拥有十几年经验的顶尖硬科技产业特稿主笔。今天是 {self.today_str}。

<fact_ledger immutable="true" role="hard_constraint" priority="highest">
中心论点：{thesis}
叙事暗线：{narrative_thread}
当前章节：第{i+1}章/共{total_chapters}章
论证目标：{outline[i] if i < len(outline) else "深入论述"}
前文论证基础：{logic[-200:] if logic else "文章开篇。"}
{counter_prompt}
</fact_ledger>
{entity_registry}
{warnings_section}
<style_guide role="style_constraint" priority="highest">
行文风格：《{self.style['name']}》：{self.style['persona']}
风格DNA锚点（严格模仿此段的语感、句式和信息密度）：
{self.style.get('dna_sample', '')}
结构节奏指引（全篇宏观节奏应遵循此路径，但不要写出这些标签）：
{self.style.get('structure', '')}
</style_guide>

<task_context role="primary_directive" priority="high">
核心纪律：
1. 你的文章必须**100%基于下发的【近期快讯】和【专属事实库】**进行分析，**严禁凭空生造观点或盲目垫字数**。
2. 凡涉及任何分析，必须引用给定的财报数字、工艺节点、融资金额或厂商动作作为证据！字字珠玑！
3. 绝对禁止使用宏大叙述词汇（如：赋能、底层逻辑、护城河、范式转移等空壳词）。
4. 每次只输出一连串的正文段落（字数不限，有多少干货就写多深），不需要开头寒暄，必须与上文极度自然地融为一体。
{template_inject}
</task_context>

<format_reminder mandatory="true">
**格式禁令：绝对不允许写任何小标题、副标题，或者"第X章"！** 这是一篇整体行云流水的专栏文章。
{state_instruction}
</format_reminder>

<tail_checklist role="entity_baseline" priority="high">
以下实体/数据/论断在前文中已出现，后续章节必须保持一致性和连贯性：
公司名：{', '.join(list(context_summary.get('entities', {}).get('companies', []))[:15]) if isinstance(context_summary, dict) else '无'}
关键数据：{', '.join(list(context_summary.get('entities', {}).get('data_points', []))[:10]) if isinstance(context_summary, dict) else '无'}
核心论断：{'; '.join(list(context_summary.get('entities', {}).get('claims', []))[:5]) if isinstance(context_summary, dict) else '无'}
</tail_checklist>"""

        return sys_p, prev_facts, last_words

    def _editorial_rewrite(self, full_text, thesis, narrative_thread, context_entities):
        """主编重写 + Gate 驱动重写循环 + 润色后校验"""
        logger.info("✍️ V3 主编重写启动...")

        # Step 1: 提取硬核清单
        manifest = self._extract_hardcore_manifest(full_text, context_entities, thesis)
        max_rewrites = 2

        best_result = full_text
        previous_failures = []

        for attempt in range(max_rewrites + 1):
            if attempt == 0:
                logger.info(f"  📝 主编重写 第 {attempt+1} 次...")
            else:
                logger.info(f"  📝 主编重写 第 {attempt+1} 次 (上次失败: {len(previous_failures)} 项)...")

            # 构建重写 prompt（后续轮次注入失败反馈）
            sys_p = self._build_editorial_prompt(thesis, narrative_thread, manifest, previous_failures)

            rewritten = call_ai_api([
                {"role": "system", "content": sys_p},
                {"role": "user", "content": f"请重写以下全文：\n\n{full_text}"}
            ], description=f"V3 Editorial Rewrite (attempt {attempt+1})", custom_timeout=600)
            self._llm_call_count += 1

            if not rewritten:
                logger.warning(f"  主编重写第 {attempt+1} 次返回空")
                continue

            # Gate 校验
            gate_result = self._verify_and_patch(full_text, rewritten, manifest)
            missing = self._get_missing_items(full_text, rewritten, manifest)

            if not missing:
                logger.info(f"  ✅ 第 {attempt+1} 次重写通过全部 Gate")
                best_result = rewritten

                # 润色后二次校验（#5）
                polished = self._light_polish(rewritten, thesis, style_anchor=self.style.get('dna_sample', ''))
                if polished:
                    polish_missing = self._get_missing_items(full_text, polished, manifest)
                    if not polish_missing:
                        logger.info("  ✅ 润色后二次校验通过")
                        best_result = polished
                    else:
                        logger.warning(f"  ⚠️ 润色导致 {len(polish_missing)} 项丢失，回退到润色前版本")
                break
            else:
                # 构建失败反馈供下轮使用
                previous_failures = missing
                logger.warning(f"  第 {attempt+1} 次重写丢失 {len(missing)} 项: {[m[1][:20] for m in missing[:3]]}")
                # 用补回版本作为 best_result 兜底
                best_result = gate_result

        logger.info(f"📊 主编重写完成，共 {min(attempt+1, max_rewrites+1)} 轮")
        return best_result

    def _build_editorial_prompt(self, thesis, narrative_thread, manifest, previous_failures=None):
        """构建主编重写 prompt，后续轮次注入失败反馈"""
        base = f"""你是一位资深主编，负责为一篇已完稿的硬科技特稿做深度润色重写。

【你的任务】
在保持全文完整性和所有硬核数据的前提下，重写这篇文章：
1. 消除重复句式（尤其是"X不再是Y，而是Z"的反复使用）
2. 打破机器排版的规律感（长短句错落，段落节奏变化）
3. 加强章节之间的过渡，确保论点线索贯穿全文
4. 按风格DNA锚点校准全文语感
5. 消除明显的拼凑感
6. 检查论点是否贯穿全文——如果某段与论点无关，重新组织使其服务于论点

【风格DNA锚点】
{self.style.get('dna_sample', '')}

【中心论点】
{thesis}

【叙事暗线】
{narrative_thread}

【硬核清单——以下每一项都必须在你的输出中出现，不可丢弃】
公司名：{', '.join(manifest['companies'])}
关键数据：{', '.join(manifest['data_points'])}
技术术语：{', '.join(manifest['tech_terms'])}
核心论断（每条论断的核心逻辑必须保留，可用不同措辞表达，但逻辑不能丢）：
{chr(10).join('- ' + c for c in manifest['core_claims'])}"""

        # 注入失败反馈（#4 Gate 驱动重写）
        if previous_failures:
            feedback_lines = []
            for i, (item_type, item) in enumerate(previous_failures[:5], 1):
                type_label = {"entity": "实体/术语", "data": "数据", "claim": "论断"}.get(item_type, "内容")
                feedback_lines.append(f"  {i}. [{type_label}] 「{item}」在你的输出中丢失了，必须保留")
            base += f"""

【上次重写失败项——你必须在本次输出中确保这些内容出现】
{chr(10).join(feedback_lines)}

这些是上次校验发现的具体丢失项。请特别注意保留它们。"""

        base += """

【输出要求】
输出完整的重写后全文。不要添加小标题或章节号。
硬核清单中的每一项都必须保留，否则视为任务失败。"""

        return base

    def _light_polish(self, text, thesis, style_anchor=""):
        """轻量润色：仅做语感微调，不改变内容结构"""
        sys_p = f"""你是一位文字润色师。对以下文章做极轻量的语感微调：

【规则】
1. 只调整句式节奏和用词，不改变任何事实、数据、公司名或论断
2. 不要重写整段，只做局部微调
3. 不要添加或删除任何实质性内容
4. 保持原有段落结构不变

【风格锚点】
{style_anchor}

【中心论点】
{thesis}

直接输出润色后的全文，不要解释。"""

        result = call_ai_api([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": text}
        ], description="V3 Light Polish", custom_timeout=300)
        self._llm_call_count += 1
        return result

    def _get_missing_items(self, original_text, rewritten_text, manifest):
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

    def _blueprint_compliance_check(self, original_text, polished_text, topic):
        """#12 蓝图合规检测：检查选题要求的核心实体是否在文章中出现"""
        core_entities = topic.get('core_entities', [])
        if not core_entities:
            return polished_text

        missing = [e for e in core_entities if e and e not in polished_text]
        total = len(core_entities)
        threshold = max(2, total // 3)  # 至少 2 个，或总数的 1/3

        if len(missing) <= threshold:
            if missing:
                logger.warning(f"  ⚠️ 蓝图合规: {len(missing)}/{total} 核心实体缺失（低于阈值 {threshold}，不触发重写）: {missing}")
            else:
                logger.info(f"  ✅ 蓝图合规: 全部 {total} 个核心实体已出现")
            return polished_text

        # 超过阈值，检查是否润色导致的回归
        original_missing = [e for e in core_entities if e and e not in original_text]
        if len(missing) > len(original_missing):
            logger.warning(f"  ⚠️ 蓝图合规回归: 润色前缺失 {len(original_missing)}，润色后缺失 {len(missing)}，回退到润色前")
            return original_text

        # 注入缺失实体的提示到重写 feedback（已在 gate-driven loop 中处理）
        logger.warning(f"  ⚠️ 蓝图合规: {len(missing)}/{total} 核心实体缺失: {missing[:5]}")
        return polished_text

    def _extract_hardcore_manifest(self, text, context_entities, thesis=""):
        """提取重写时不可丢失的硬核清单"""
        manifest = {
            "companies": set(),
            "data_points": set(),
            "tech_terms": set(),
            "core_claims": [],
            "thesis": thesis,
        }

        # 数字+单位模式
        manifest["data_points"] = set(re.findall(
            r'\d+[\.\d]*\s*[%BWGbpsTB人民币€]|[＄$]\s*\d+[\.\d]*\s*[BMK]?|\d+[\.\d]*\s*[纳米nmμm]',
            text
        ))

        # 从 news_list 提取已知实体
        for item in self.news_list:
            p = item.get('parsed', {})
            manifest["companies"].update(p.get('keywords', []))
            manifest["companies"].update(p.get('tags', []))

        # 正则提取中文公司名
        manifest["companies"].update(re.findall(
            r'[一-龥]{2,6}(?:科技|集团|半导体|电子|能源|智能|机器人|电池|芯片)',
            text
        ))

        # 英文公司名/技术术语
        manifest["tech_terms"].update(re.findall(
            r'\b(?:CoWoS|HBM[0-9e]*|TSV|GAA|EUV|CUDA|RISC-V|LiDAR|SiC|GaN|Chiplet|UCIe|NVLink|PUE|SOC|ASIC|FPGA|GPU|TPU)\b',
            text
        ))

        # 核心论断：从上下文摘要的 entities.claims 中获取
        entities = context_entities if isinstance(context_entities, dict) else {}
        manifest["core_claims"] = entities.get("claims", [])

        return manifest

    def _verify_and_patch(self, original_text, rewritten_text, manifest):
        """4 关卡校验管线：实体完整性 → 数据精度 → 论断逻辑 → 论点贯穿"""
        gate_results = []

        # Gate 1: 实体完整性（公司名 + 技术术语精确匹配）
        missing_entities = self._gate_entity_check(rewritten_text, manifest)
        gate_results.append(("实体完整性", len(missing_entities) == 0, missing_entities))

        # Gate 2: 数据精度（数字+单位精确匹配，防止 "3nm" 变 "先进制程"）
        missing_data = self._gate_data_precision(rewritten_text, manifest)
        gate_results.append(("数据精度", len(missing_data) == 0, missing_data))

        # Gate 3: 论断逻辑（关键词覆盖率 ≥ 60%）
        missing_claims = self._gate_claim_logic(rewritten_text, manifest)
        gate_results.append(("论断逻辑", len(missing_claims) == 0, missing_claims))

        # Gate 4: 论点偏离检测
        thesis_drift = self._gate_thesis_drift(rewritten_text, manifest.get("thesis", ""))
        gate_results.append(("论点贯穿", not thesis_drift, thesis_drift))

        # 汇总
        all_missing = missing_entities + missing_data + missing_claims
        gates_passed = sum(1 for _, ok, _ in gate_results if ok)

        for name, ok, detail in gate_results:
            if ok:
                logger.info(f"  ✅ Gate [{name}] 通过")
            else:
                count = len(detail) if isinstance(detail, list) else "偏离"
                logger.warning(f"  ⚠️ Gate [{name}] 失败: {count}")

        if not all_missing and not thesis_drift:
            logger.info(f"✅ 全部 4 关卡通过 ({gates_passed}/4)")
            return rewritten_text

        # 补回
        logger.warning(f"⚠️ {4 - gates_passed} 关卡未通过，执行补回...")
        patched = self._patch_missing(original_text, rewritten_text, all_missing)
        return patched

    def _gate_entity_check(self, text, manifest):
        """Gate 1: 公司名 + 技术术语精确匹配"""
        missing = []
        for item in manifest["companies"] | manifest["tech_terms"]:
            if item and item not in text:
                missing.append(("entity", item))
        return missing

    def _gate_data_precision(self, text, manifest):
        """Gate 2: 数字+单位精确匹配"""
        missing = []
        for item in manifest["data_points"]:
            if item and item not in text:
                missing.append(("data", item))
        return missing

    def _gate_claim_logic(self, text, manifest):
        """Gate 3: 核心论断关键词覆盖率 ≥ 60%"""
        missing = []
        for claim in manifest.get("core_claims", []):
            keywords = [w for w in re.findall(r'[一-龥]{2,}|[a-zA-Z]+|\d+', claim) if len(w) > 1]
            if keywords:
                ratio = sum(1 for kw in keywords if kw in text) / len(keywords)
                if ratio < 0.6:
                    missing.append(("claim", claim))
        return missing

    def _gate_thesis_drift(self, text, thesis):
        """Gate 4: 检测论点关键词是否仍贯穿全文。返回 True 表示偏离。"""
        if not thesis:
            return False
        thesis_keywords = [w for w in re.findall(r'[一-龥]{2,}|[A-Z][a-zA-Z]+', thesis) if len(w) > 1]
        if not thesis_keywords:
            return False
        found = sum(1 for kw in thesis_keywords if kw in text)
        drift = found / len(thesis_keywords) < 0.5
        if drift:
            logger.warning(f"  论点偏离: 关键词 {thesis_keywords}, 仅命中 {found}/{len(thesis_keywords)}")
        return drift

    def _patch_missing(self, original_text, rewritten_text, missing_items):
        """智能补回：从原文找到丢失项所在句子，插入到重写文末句号前"""
        if not missing_items:
            return rewritten_text

        sentences = re.split(r'(?<=[。！？])', original_text)
        patches = []

        for item_type, item in missing_items:
            for sent in sentences:
                if item in sent and sent.strip():
                    patches.append(sent.strip())
                    break
            else:
                if item_type == "claim":
                    patches.append(item + "。")

        if patches:
            patch_text = "\n".join(patches)
            # 插入到文末最后一个句号之前，而非追加到文末
            last_period = rewritten_text.rfind("。")
            if last_period > len(rewritten_text) - 200:
                rewritten_text = rewritten_text[:last_period+1] + "\n\n" + patch_text + "\n" + rewritten_text[last_period+1:]
            else:
                rewritten_text = rewritten_text.rstrip() + "\n\n" + patch_text
            logger.info(f"🔧 已补回 {len(patches)} 条丢失内容")

        return rewritten_text

    def _surgical_purify(self, text):
        return surgical_purify(text)

    def _break_monotony(self, text):
        """语义感知的段落打散"""
        logger.info("🔪 打破机器排版规律...")
        new_text = ""
        segments = text.split('。')
        for i, seg in enumerate(segments):
            new_text += seg
            if i < len(segments) - 1:
                new_text += "。"
                # 仅在长段落中触发，概率 20%
                if random.random() < 0.20 and len(seg) > 50 and not seg.endswith('\n'):
                    new_text += "\n\n"
        return re.sub(r'\n{3,}', '\n\n', new_text)

    def _native_chinese_polish(self, text):
        """抽取段落进行纯正中文语感修正"""
        logger.info("🇨🇳 纯正中文语感修正...")
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

            res = call_ai_api([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], description="V3 Native Polish", custom_timeout=60)
            self._llm_call_count += 1
            if res:
                paragraphs[idx] = res.strip()

        return "\n\n".join(paragraphs)

    def run(self, topic, news_list):
        if not topic:
            logger.error("V3 收到空选题。")
            return

        self.news_list = news_list
        emit_event("pipeline_start", f"V3 写作启动: {topic.get('title_proposal', '未命名')[:40]}")

        # 根据粒度选择风格
        granularity = topic.get('granularity', '中观')
        style_pool = ANGLE_STYLE_MAP.get(granularity, ["SemiAnalysis"])
        chosen_style_id = random.choice(style_pool)
        self.style = self.styles.get(chosen_style_id, list(self.styles.values())[0])
        logger.info(f"🚀 V3 写作管线启动 | 论点驱动 | 粒度: {granularity} | 风格: 【{self.style['name']}】")

        title = topic.get('title_proposal', '未命名深度研报')
        outline = topic.get('outline', [])
        thesis = topic.get('thesis', '')
        narrative_thread = topic.get('narrative_thread', '')
        counter_argument = topic.get('counter_argument', '')

        if not outline:
            outline = ["产业背景", "核心逻辑", "标的分析", "总结展望"]

        logger.info(f"📌 V3 论点: {thesis[:60]}...")
        logger.info(f"📌 V3 暗线: {narrative_thread}")
        logger.info(f"📌 V3 大纲 ({len(outline)} 章): {outline}")

        # #16 检测选题类型，注入模板指令
        topic_type = _detect_topic_type(topic)
        template_inject = ""
        if topic_type:
            template_inject = f"\n5. {PROMPT_TEMPLATES[topic_type]['inject']}"
            logger.info(f"📝 选题类型: {topic_type}，已注入模板指令")

        # 保存到状态
        self.state['counter_argument'] = counter_argument

        total_chapters = len(outline)
        chapters_done = self.state.get('chapters_done', [])
        context_summary = self.state.get('context_summary', {"logic": "", "entities": {"companies": [], "data_points": [], "claims": []}})

        # 全局新闻素材池
        news_bullets = []
        for n in news_list[:15]:
            p = n.get('parsed', {})
            raw_snippet = n.get('snippet', '无原片段').replace('\n', ' ')
            news_bullets.append(f"- 📰 【{p.get('title')}】\n  AI摘要: {p.get('summary')}\n  原始信源片段(极重要): {raw_snippet} (平台:{n.get('source_platform')})")
        global_news = "\n".join(news_bullets)

        # #19 变更检测门控
        self._check_data_freshness(news_list)

        # #14 加载跨篇事实归档背景
        archive_context = self._build_archive_context()

        # 1. 增量章节流式写作
        for i in range(len(chapters_done), total_chapters):
            ch_title = outline[i]
            logger.info(f"✍️ V3 主笔组装: 第 {i+1}/{total_chapters} 章节: {ch_title}")

            # 章节定向搜索
            search_query = self._get_chapter_keywords(title, ch_title)
            chapter_facts = self._search_jina_for_chapter(search_query)
            self.chapter_facts_pool.append(chapter_facts)

            # 构建 prompt
            sys_p, prev_facts, last_words = self._build_chapter_sys_prompt(
                i, total_chapters, context_summary, thesis, narrative_thread, outline, template_inject
            )

            user_p = f"""{archive_context + chr(10) if archive_context else ''}【全文思路参考】{', '.join(outline)}
【当前写作焦点】请重点围绕这个方向深入论述：{ch_title}

【前文全文核心摘要】（用来理解整体逻辑，不要重复）：
{context_summary.get('logic', '') if isinstance(context_summary, dict) else str(context_summary)}

【你必须承接的上一段结尾原文】：
{last_words}

{prev_facts}【全量近期快讯池】
{global_news}

【本节独家硬核事实库】
{chapter_facts}

【任务】
立刻往下续写段落，直接输出正文。**没有任何小标题、粗体小标题或章节号！**
用最平滑的过渡承接上一段结尾，用事实说话。
每一段论述都必须服务于中心论点：{thesis}"""

            chapter_content = call_ai_api([
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_p}
            ], description=f"V3 Write Ch{i+1}", custom_timeout=350)
            self._llm_call_count += 1

            if chapter_content:
                # 解析 ---STATE--- 结构化输出
                clean_text, chapter_state = self._parse_chapter_state(chapter_content)
                chapters_done.append(clean_text)
                self.state['chapters_done'] = chapters_done

                if chapter_state:
                    # 合并结构化状态到 context_summary（纯本地操作，0 LLM）
                    context_summary = self._merge_chapter_state(context_summary, chapter_state)
                else:
                    # #15 双通道兜底：先正则提取，再 LLM 摘要
                    # 注意：clean_text 此时等于 chapter_text（无 STATE 块），直接用
                    regex_result = self._regex_extract_state(clean_text)
                    if regex_result:
                        logger.info("📋 正则提取成功，跳过 LLM 摘要")
                        # regex_result 格式 {"logic": "", "entities": {"companies": [...], "claims": [...]}}
                        # 需要转换为 _merge_chapter_state 期望的 chapter_state 格式
                        regex_entities = regex_result.get("entities", {})
                        chapter_state_from_regex = {
                            "entities": {k: v for k, v in regex_entities.items() if k != "claims"},
                            "claims": regex_entities.get("claims", []),
                        }
                        context_summary = self._merge_chapter_state(context_summary, chapter_state_from_regex)
                    else:
                        logger.info("📋 正则提取无结果，回退到 LLM 上下文摘要")
                        context_summary = self._summarize_context(context_summary, chapter_content)

                # 优先级压缩
                context_summary = self._compress_context(context_summary, i, total_chapters)
                self.state['context_summary'] = context_summary
                self._save_state()
                emit_event("chapter_done", f"第 {i+1}/{total_chapters} 章完成", {"chars": len(clean_text)})
            else:
                logger.error(f"第 {i+1} 章节写作失败，中止 V3 流程。")
                emit_event("chapter_failed", f"第 {i+1} 章写作失败")
                return

        # 2. 组装全文
        logger.info("⚖️ V3 组装全文...")
        raw_full_article = f"# {title}\n\n"
        for content in chapters_done:
            raw_full_article += content.strip() + "\n\n"

        # 3. 主编重写 + 硬核校验
        logger.info("✍️ V3 主编重写+硬核校验...")
        emit_event("editorial_start", "主编重写启动")
        entities = context_summary.get("entities", {}) if isinstance(context_summary, dict) else {}
        polished_article = self._editorial_rewrite(raw_full_article, thesis, narrative_thread, entities)

        # 3.5 #12 蓝图合规检测
        polished_article = self._blueprint_compliance_check(raw_full_article, polished_article, topic)

        # 4. 后处理
        polished_article = self._break_monotony(polished_article)
        polished_article = self._native_chinese_polish(polished_article)
        pure_article = self._surgical_purify(polished_article)

        # 5. 存档与分发
        year, month = datetime.now().strftime("%Y"), datetime.now().strftime("%m")
        kb_path = os.path.join(KB_ROOT, year, month)
        os.makedirs(kb_path, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]', '', title)[:50]
        full_kb_path = os.path.abspath(os.path.join(kb_path, f"{self.today_str}-V3-{safe_name}.md"))

        # #10 原子写入
        tmp = full_kb_path + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(pure_article)
        os.replace(tmp, full_kb_path)
        logger.info(f"🗄️ V3 特稿已归档：{full_kb_path}")
        emit_event("article_saved", f"文章已归档: {safe_name}", {"path": full_kb_path, "chars": len(pure_article)})

        html_article = render_deep_article_to_html(pure_article, title, self.style['name'], self.today_str)
        brand_emoji = DOMAIN['brand_emoji']
        send_email(f"{brand_emoji} [V3深度文章] {self.today_str} | {title}", html_article)

        logger.info(f"📊 V3 本次 LLM 调用总计: {self._llm_call_count} 次")

        # #14 归档事实快照
        entities = context_summary.get("entities", {}) if isinstance(context_summary, dict) else {}
        self._archive_article_facts(title, thesis, entities)

        # 清空状态
        if os.path.exists(STATE_FILE_V3):
            os.remove(STATE_FILE_V3)


if __name__ == "__main__":
    pass
