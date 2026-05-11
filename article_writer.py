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
from fact_purifier import FactPurifier
from domain_config import DOMAIN
from text_utils import surgical_purify

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_DIR, ".task_state.json")
MEMORY_FILE = os.path.join(_DIR, "strategic_memory.json")
KB_ROOT = os.path.join(_DIR, "knowledge_base")
STYLES_FILE = os.path.join(_DIR, "styles_hardtech.json")

class ArticleWriter:
    def __init__(self):
        self.config = load_config()
        self.styles = self._load_styles()
        self.style = self.styles[random.choice(list(self.styles.keys()))]
        self.today_str = datetime.now().strftime("%Y-%m-%d")
        self.memory = self._load_memory()
        self.state = self._load_state()
        logger.info(f"🎬 深度特稿 Agent 启动 | 风格：【{self.style['name']}】 | 日期：{self.today_str}")

    def _load_styles(self):
        if os.path.exists(STYLES_FILE):
            try:
                with open(STYLES_FILE, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e: logger.error(f"加载 styles 失败: {e}")
        return {"Default": {"name": "标准硬科技评论", "persona": "专业、客观、数据驱动", "structure": "【现状】→【分析】→【结论】"}}

    def _load_memory(self):
        if not os.path.exists(MEMORY_FILE): return {"storylines": {}, "mysteries": []}
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception: return {"storylines": {}, "mysteries": []}

    def _save_memory(self, article_text):
        logger.info("📝 正在更新战略手记...")
        prompt = f"你是主编。日期 {self.today_str}。请提炼内容中的故事线：\n{article_text[:1500]}\n返回JSON."
        res = call_ai_api([{"role": "user", "content": prompt}])
        data = extract_json_from_text(res)
        if data:
            self.memory['storylines'].update(data.get('storylines', {}))
            with open(MEMORY_FILE, 'w', encoding='utf-8') as f: json.dump(self.memory, f, ensure_ascii=False, indent=2)

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                    if d.get('date') == self.today_str: return d
            except Exception: pass
        return {"date": self.today_str, "fragments": [], "task_packages": [], "kb_context": ""}

    def _save_state(self):
        with open(STATE_FILE, 'w', encoding='utf-8') as f: json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _retrieve_kb(self, title):
        all_files = []
        for root, _, files in os.walk(KB_ROOT):
            for file in files:
                if file.endswith(".md"): all_files.append(os.path.join(root, file))
        if not all_files: return ""
        prompt = f"你是资料员。今天是 {self.today_str}。选出 2 个与《{title}》相关的往期文章：\n{all_files[:50]}\n返回JSON:{{'related':[]}}"
        res = call_ai_api([{"role": "user", "content": prompt}])
        data = extract_json_from_text(res)
        kb_context = ""
        if data and data.get('related'):
            kb_abs = os.path.abspath(KB_ROOT)
            for path in data['related']:
                try:
                    if not os.path.abspath(path).startswith(kb_abs):
                        logger.warning(f"跳过 KB_ROOT 外的路径: {path}")
                        continue
                    with open(path, 'r', encoding='utf-8') as f: kb_context += f"\n\n[往期参考]\n" + f.read()[:800]
                except Exception: pass
        return kb_context

    def search_with_jina(self, query):
        jina_key = self.config.api.get('jina_api_key')
        try:
            h = {"Accept": "application/json", "Authorization": f"Bearer {jina_key}"}
            r = requests.get(f"https://s.jina.ai/{query}", headers=h, timeout=35)
            if r.status_code == 200:
                d = r.json().get('data', [])
                return "\n".join([f"[{i.get('title')}] {(i.get('content') or '')[:1200]}" for i in d[:5]])
        except Exception: pass
        return ""

    def run(self, topic, news_list):
        if not topic: return
        logger.info(f"📌 选定选题：{topic.get('title_proposal')}")

        # 1. 规划任务包
        if not self.state.get('task_packages'):
            self.state['kb_context'] = self._retrieve_kb(topic.get('title_proposal'))
            search_query = f"{topic.get('title_proposal')} 2026 最新核心产品 厂商 财务数据"
            raw_search = self.search_with_jina(search_query)
            purified_facts = FactPurifier.purify(raw_search, topic.get('title_proposal'), self.today_str)

            full_outline = "\n".join([f"章节{idx+1}：{item}" for idx, item in enumerate(topic.get('outline', []))])

            outline = topic.get('outline', [])
            num_chapters = max(1, len(outline))
            task_packages = []
            for i in range(num_chapters):
                task_packages.append({
                    "chapter_index": i + 1,
                    "full_outline": full_outline,
                    "all_facts": purified_facts,
                    "target_chapter": outline[i] if i < len(outline) else "逻辑总结"
                })
            self.state['task_packages'] = task_packages
            self._save_state()

        # 2. 接力成文
        frags = self.state['fragments']
        pkgs = self.state['task_packages']

        for i in range(len(frags), len(pkgs)):
            logger.info(f"✍️  主笔接力：第 {i+1}/{len(pkgs)} 章节")
            sys_p = f"""
            你是一位顶尖硬科技产业调查记者。今天是 {self.today_str}。

            【核心写作指令】
            1. 证据驱动：必须引用素材库中的具体厂商、型号、数据。
            2. 严禁张冠李戴：每个产品必须准确对应其品牌。
            3. 拒绝黑话：绝对禁止使用'范式'、'维度'、'赋能'等词。
            4. 语气参考：风格《{self.style['name']}》：{self.style['persona']}。
            5. 禁止虚构。
            """
            user_p = f"""
            【全量实体清单】\n{pkgs[i].get('all_facts')}
            【整篇文章大纲】\n{pkgs[i].get('full_outline')}
            【上文回顾】\n{frags[-1][-1500:] if frags else '文章开头'}

            任务：撰写本章正文（约 1000 字）：【{pkgs[i].get('target_chapter')}】
            要求：开头用一句话衔接上文，确保逻辑丝滑。
            """
            res = call_ai_api([{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], description=f"Relay {i+1}", custom_timeout=350)
            if res:
                frags.append(res); self._save_state()
            else:
                logger.warning(f"⚠️ 第 {i+1}/{len(pkgs)} 章节写作失败，跳过")

        # 3. 终极缝合
        raw_full = "\n\n".join(frags)
        logger.info("⚖️  终极缝合 Agent：全篇逻辑升华与二次注实...")

        entities_query = f"{topic.get('title_proposal')} 核心厂商 型号 参数 2026"
        second_search = self.search_with_jina(entities_query)
        second_purified = FactPurifier.purify(second_search, "实体细节补全", self.today_str)

        ultimate_sys = f"""
        你是一位全球顶尖的硬科技产业特稿主笔。基于'接力草稿'和'新增事实库'，创作深度研报。

        【职责】
        1. 逻辑重构：消灭拼凑感，确保起承转合自然流畅。
        2. 事实注实：插值具体型号、工艺节点、参数数据。
        3. 纠正错误：核对产品归属。
        4. 风格对齐：保持《{self.style['name']}》风格。
        5. 增量扩写：目标 4000 字以上。
        """
        ultimate_user = f"""
        【待处理草稿】
        {raw_full}

        【新增硬核事实库】
        {second_purified}

        请开始终极创作（直接输出正文）：
        """
        final_article = call_ai_api(
            [{"role": "system", "content": ultimate_sys}, {"role": "user", "content": ultimate_user}],
            description="Ultimate Polish", custom_timeout=600
        )

        # 4. 物理净化与保存
        pure_article = self._surgical_purify(final_article or raw_full)
        year, month = datetime.now().strftime("%Y"), datetime.now().strftime("%m")
        kb_path = os.path.join(KB_ROOT, year, month)
        os.makedirs(kb_path, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]', '', topic.get('title_proposal', 'Untitled'))[:50]
        full_kb_path = os.path.abspath(os.path.join(kb_path, f"{self.today_str}-{safe_name}.md"))

        final_md = f"# {topic.get('title_proposal')}\n\n" + pure_article
        with open(full_kb_path, 'w', encoding='utf-8') as f: f.write(final_md)
        logger.info(f"🗄️  特稿已存档：{full_kb_path}")

        self._save_memory(pure_article)
        html_article = render_deep_article_to_html(pure_article, topic.get('title_proposal'), self.style['name'], self.today_str)
        brand_emoji = DOMAIN['brand_emoji']
        send_email(f"{brand_emoji} {self.today_str} 深度观察 | {topic.get('title_proposal')}", html_article)
        if os.path.exists(STATE_FILE): os.remove(STATE_FILE)

    def _surgical_purify(self, text):
        return surgical_purify(text)

if __name__ == "__main__": pass
