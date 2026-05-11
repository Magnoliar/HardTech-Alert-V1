import json
import os
import re
import logging
import random
import requests
from datetime import datetime
from config_loader import load_config
from llm_client import call_ai_api, extract_json_from_text
from email_generator import send_email
from article_renderer import render_deep_article_to_html
from domain_config import DOMAIN
from text_utils import surgical_purify

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE_V2 = os.path.join(_DIR, ".task_state_v2.json")
MEMORY_FILE = os.path.join(_DIR, "strategic_memory.json")
KB_ROOT = os.path.join(_DIR, "knowledge_base")
STYLES_FILE = os.path.join(_DIR, "styles_hardtech.json")

class ArticleWriterV2:
    """
    全新长文写作系统 V2 (Dynamic Multi-Agent Streaming)
    1. 动态章节数量适配
    2. 本章节专研定向搜索 (Chapter-level Research)
    3. 流式上下文摘要传递 (Stateful Context)
    4. 流式组装取代全局重写，防截断
    """
    def __init__(self):
        self.config = load_config()
        self.today_str = datetime.now().strftime("%Y-%m-%d")
        
        # 加载随机/设定风格
        self.styles = self._load_styles()
        self.style = self.styles[random.choice(list(self.styles.keys()))]
        
        # 持久化跨篇记忆
        self.memory = self._load_memory()
        # V2 独立临时状态
        self.state = self._load_state()

        self._llm_call_count = 0
        logger.info(f"🚀 V2 写作管线启动 | 动态章节拆解 & 增量组装 | 风格：【{self.style['name']}】")

    def _load_styles(self):
        if os.path.exists(STYLES_FILE):
            try:
                with open(STYLES_FILE, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e: logger.error(f"V2加载styles失败: {e}")
        return {"Default": {"name": "标准硬科技评论", "persona": "专业、客观、数据驱动", "structure": "【现状】→【分析】→【结论】"}}

    def _load_memory(self):
        if not os.path.exists(MEMORY_FILE): return {"storylines": {}}
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception: return {"storylines": {}}

    def _load_state(self):
        if os.path.exists(STATE_FILE_V2):
            try:
                with open(STATE_FILE_V2, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                    if d.get('date') == self.today_str: return d
            except Exception: pass
        return {"date": self.today_str, "chapters_done": [], "context_summary": "", "final_text": ""}

    def _save_state(self):
        with open(STATE_FILE_V2, 'w', encoding='utf-8') as f: json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _search_jina_for_chapter(self, search_query):
        """为特定章节做定向侦察，并调用净化器"""
        logger.info(f"🔍 V2章节专研侦察: {search_query}")
        jina_key = self.config.api.get('jina_api_key')
        try:
            h = {"Accept": "application/json", "Authorization": f"Bearer {jina_key}"}
            r = requests.get(f"https://s.jina.ai/{search_query}", headers=h, timeout=40)
            if r.status_code == 200:
                d = r.json().get('data', [])
                raw_text = "\n".join([f"[{i.get('title')}] {(i.get('content') or '')[:1200]}" for i in d[:4]])
                
                # 调用 V1 证明非常好用的 FactPurifier 来提纯
                from fact_purifier import FactPurifier
                return FactPurifier.purify(raw_text, search_query, self.today_str)
        except Exception as e:
            logger.warning(f"Jina章节专研异常: {e}")
        return ""

    def _get_chapter_keywords(self, title, ch_title):
        sys_p = "你是一位极简的搜索词提取器小助手。请在JSON中返回提取的搜索词。"
        user_p = f"请从全文标题「{title}」和本章节标题「{ch_title}」中，提取出最核心的专有名词或公司名，作为搜索引擎查询条件。绝不要保留修饰语。控制在2-3个实词，空格隔开。\n输出格式：{{\"keywords\": \"词1 词2\"}}"
        res = call_ai_api([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], description="Extract Keys")
        self._llm_call_count += 1
        data = extract_json_from_text(res)
        if data and data.get('keywords'):
            return data.get('keywords')
        return "硬科技 财报 数据"

    def _summarize_context(self, current_summary, new_chapter_text):
        """
        Stateful Context 传递核心：
        不直接传递巨大原文，而是让模型提取核心结论压平为摘要，供下一章使用。
        """
        sys_p = "你是一位上下文维护专员。请合并过去的文章摘要和刚刚生成的新章节，输出一段约500字的行文核心逻辑线索，不许丢失已经出场的公司名和论点。"
        user_p = f"【当前上下文概要】\n{current_summary}\n\n【最新章节原文】\n{new_chapter_text}\n\n请输出更新后的上下文概要："
        res = call_ai_api([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_p}
        ], description="Context Summarizer")
        self._llm_call_count += 1
        return res if res else current_summary

    def _surgical_purify(self, text):
        return surgical_purify(text)

    def _break_monotony(self, text):
        logger.info("🔪 打破机器排版规律：沿随机句号插入换行切碎段落...")
        new_text = ""
        segments = text.split('。')
        for i, seg in enumerate(segments):
            new_text += seg
            if i < len(segments) - 1:
                new_text += "。"
                # 25% 的概率把原本连续的两个句子硬切行，打破结构对齐
                if random.random() < 0.25 and not seg.endswith('\n'):
                    new_text += "\n\n"
        return re.sub(r'\n{3,}', '\n\n', new_text)

    def _native_chinese_polish(self, text):
        logger.info("🇨🇳 抽取段落进行纯正中文语感修正（短句代换长文）...")
        paragraphs = text.split('\n\n')
        # 选择那些长一点的纯文字段落，避开标题、代码
        valid_indices = [i for i, p in enumerate(paragraphs) if len(p) > 100 and not p.startswith(('#', '-', '>'))]
        if not valid_indices: 
            return text
        
        # 随机抽取 2 到 4 段进行中文化短句处理
        sample_count = min(len(valid_indices), random.randint(2, 4))
        target_indices = random.sample(valid_indices, sample_count)
        
        for idx in target_indices:
            original_p = paragraphs[idx]
            sys_p = "你是一位极高水准的中文专栏文字编辑。你的工作是消除原稿中的「机器生成味」与「西式翻译语法」。"
            user_p = f"【当前段落如下】\n{original_p}\n\n【改写任务】\n请将这段文字改写为纯正的中文母语表达习惯：\n1. 斩断长句：将繁冗的「从句、长定语、被动语态」拆解为有主次的多个干净利落的短句！\n2. 动词主导：多用动词推动语意，拒绝「过度名词化」的英文表达模板。\n3. 保留干货：原文里的专有名词和数据必须100%保留！\n只需直接输出改写后且更为干练的一段话，不要有任何总结性或礼貌性的废话。"
            
            res = call_ai_api([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], description="Native Polish", custom_timeout=60)
            self._llm_call_count += 1
            if res:
                paragraphs[idx] = res.strip()
                
        return "\n\n".join(paragraphs)

    def run(self, topic, news_list):
        if not topic: 
            logger.error("V2 收到空选题。")
            return

        title = topic.get('title_proposal', '未命名深度研报')
        outline = topic.get('outline', [])
        if not outline:
            outline = ["产业背景", "核心逻辑", "标的分析", "总结展望"]
            
        logger.info(f"📌 V2选定选题：{title} | 动态章节数: {len(outline)}")

        # 循环大纲长度
        total_chapters = len(outline)
        chapters_done = self.state.get('chapters_done', [])
        context_summary = self.state.get('context_summary', "")
        
        # 全局新闻素材池 (深度加工，加入更多干货原片段)
        news_bullets = []
        for n in news_list[:15]:
            p = n.get('parsed',{})
            raw_snippet = n.get('snippet', '无原片段').replace('\n', ' ')
            news_bullets.append(f"- 📰 【{p.get('title')}】\n  AI摘要: {p.get('summary')}\n  原始信源片段(极重要): {raw_snippet} (平台:{n.get('source_platform')})")
        global_news = "\n".join(news_bullets)

        # 1. 增量章节流式写作
        for i in range(len(chapters_done), total_chapters):
            ch_title = outline[i]
            logger.info(f"✍️ V2 主笔组装: 第 {i+1}/{total_chapters} 章节: {ch_title}")
            
            # 1.1 章节定向搜索：AI智能摘取关键词
            search_query = self._get_chapter_keywords(title, ch_title)
            chapter_facts = self._search_jina_for_chapter(search_query)

            # 1.2 主笔写作本章
            sys_p = f"""你是一位拥有十几年经验的顶尖硬科技产业特稿主笔。今天是 {self.today_str}。
【核心纪律：消除废话与分块感】
1. 你的文章必须**100%基于下发的【近期快讯】和【专属事实库】**进行分析，**严禁凭空生造观点或盲目垫字数**。
2. 凡涉及任何分析，必须引用给定的财报数字、工艺节点、融资金额或厂商动作作为证据！字字珠玑！
3. 绝对禁止使用宏大叙述词汇（如：赋能、底层逻辑、护城河、范式转移等空壳词）。
4. **格式禁令：绝对不允许写任何小标题、副标题，或者“第X章”！** 这是一篇整体行云流水的专栏文章，你只负责写出其中的几个核心段落。
5. 行文风格：《{self.style['name']}》：{self.style['persona']}
6. 每次只输出一连串的正文段落（字数不限，有多少干货就写多深），不需要开头寒暄，必须与上文极度自然地融为一体。"""

            # 抓取上文最后的原话，辅助无缝粘合
            last_words = chapters_done[-1][-300:] if chapters_done else "这是文章的开篇。"

            user_p = f"""【全文思路参考】{', '.join(outline)}
【当前写作焦点】请重点围绕这个方向深入论述：{ch_title}

【前文全文核心摘要】（用来理解整体逻辑，不要重复）：
{context_summary if context_summary else "文章开篇。"}

【你必须承接的上一段结尾原文】：
{last_words}

【全量近期快讯池】
{global_news}

【本节独家硬核事实库】
{chapter_facts}

【任务】
立刻往下续写段落，直接输出正文。**没有任何小标题、粗体小标题或章节号！**用最平滑的过渡承接上一段结尾，用事实说话。"""

            chapter_content = call_ai_api([
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_p}
            ], description=f"V2 Write Ch{i+1}", custom_timeout=350)
            self._llm_call_count += 1

            if chapter_content:
                chapters_done.append(chapter_content)
                self.state['chapters_done'] = chapters_done
                # 状态摘要更新
                context_summary = self._summarize_context(context_summary, chapter_content)
                self.state['context_summary'] = context_summary
                self._save_state()
            else:
                logger.error(f"第 {i+1} 章节写作失败，中止 V2 流程以供重试。")
                return

        # 2. 最终组装与缓冲段过渡
        logger.info("⚖️ V2 终极流水线：无缝缝合与净化...")
        # 取消引言字样，取消粗暴的空白
        raw_full_article = f"# {title}\n\n"
        for idx, content in enumerate(chapters_done):
            # 将生成的文字去除两端换行后，仅以自然的一个空行分开
            raw_full_article += content.strip() + "\n\n"
            
        # 人工干预仿真：打乱规整的段落间距并选取部分修正翻译腔
        raw_full_article = self._break_monotony(raw_full_article)
        raw_full_article = self._native_chinese_polish(raw_full_article)
            
        # 过渡最后的物理词条净化
        pure_article = self._surgical_purify(raw_full_article)

        # 3. 存档与分发
        year, month = datetime.now().strftime("%Y"), datetime.now().strftime("%m")
        kb_path = os.path.join(KB_ROOT, year, month)
        os.makedirs(kb_path, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]', '', title)[:50]
        full_kb_path = os.path.abspath(os.path.join(kb_path, f"{self.today_str}-V2-{safe_name}.md"))

        with open(full_kb_path, 'w', encoding='utf-8') as f: 
            f.write(pure_article)
        logger.info(f"🗄️ V2 特稿已归档：{full_kb_path}")

        html_article = render_deep_article_to_html(pure_article, title, self.style['name'], self.today_str)
        brand_emoji = DOMAIN['brand_emoji']
        send_email(f"{brand_emoji} [深度文章] {self.today_str} | {title}", html_article)
        
        logger.info(f"📊 V2 本次 LLM 调用总计: {self._llm_call_count} 次")

        # 测试完毕清空状态
        if os.path.exists(STATE_FILE_V2): os.remove(STATE_FILE_V2)

if __name__ == "__main__":
    pass
