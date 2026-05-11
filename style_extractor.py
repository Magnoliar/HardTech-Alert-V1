"""
风格提取器 — 从文章 URL 自动提取写作风格特征
用法：python style_extractor.py <文章URL> [风格ID]
"""
import json
import os
import sys
import logging
import requests
from llm_client import call_ai_api, extract_json_from_text

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
STYLES_FILE = os.path.join(_DIR, "styles_hardtech.json")


class StyleExtractor:
    """从文章 URL 提取风格特征，追加到 styles_hardtech.json"""

    @staticmethod
    def fetch_article(url: str) -> str:
        """用 Jina 抓取文章内容"""
        try:
            jina_key = os.environ.get('JINA_API_KEY')
            if not jina_key:
                # 从 config 读取
                from config_loader import load_config
                config = load_config()
                jina_key = config.api.get('jina_api_key')

            if not jina_key:
                logger.error("Jina API key 不可用")
                return ""

            h = {"Accept": "application/json", "Authorization": f"Bearer {jina_key}"}
            r = requests.get(f"https://s.jina.ai/{url}", headers=h, timeout=60)
            if r.status_code == 200:
                data = r.json().get('data', [])
                if data:
                    return data[0].get('content', '')[:15000]
            logger.error(f"Jina 抓取失败: HTTP {r.status_code}")
        except Exception as e:
            logger.error(f"Jina 抓取异常: {e}")
        return ""

    @staticmethod
    def extract_from_url(url: str, style_id: str = None) -> dict:
        """
        从文章 URL 提取风格特征。
        返回 style dict: {name, persona, dna_sample, structure}
        """
        content = StyleExtractor.fetch_article(url)
        if not content:
            logger.error("无法抓取文章内容")
            return None

        return StyleExtractor.extract_from_content(content, url, style_id)

    @staticmethod
    def extract_from_content(content: str, source_url: str = "", style_id: str = None) -> dict:
        """从文章内容提取风格特征"""

        sys_p = """你是一位写作风格分析专家。你的任务是从一篇文章中提取其独特的写作风格特征。

请分析以下维度：
1. 语感特征：句式偏好（长句/短句/混合）、节奏感、段落结构
2. 信息密度：每段平均包含多少数据/实体、引用方式
3. 视角偏好：宏观/中观/微观、分析框架类型
4. 独特表达：作者标志性的句式、比喻方式、转折手法

【输出格式】
{
    "name": "风格名称（基于文章来源或特征命名）",
    "persona": "一句话描述该风格的核心特征（30字以内）",
    "dna_sample": "从原文中选取最能代表该风格的一段话（100-200字，保持原文不动）",
    "structure": "用箭头描述该文章的宏观节奏路径（如【切入点】→【展开分析】→【结论】）"
}

注意：
- dna_sample 必须是原文的直接引用，不要改写
- persona 要精炼，能让人一读就知道这是什么风格
- structure 要反映文章的实际结构，不要套模板"""

        user_p = f"请分析以下文章的写作风格特征：\n\n{content[:12000]}"

        response = call_ai_api([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_p}
        ], description="Style Extraction")

        if not response:
            logger.error("风格提取 LLM 调用失败")
            return None

        style = extract_json_from_text(response)
        if not style or not isinstance(style, dict):
            logger.error("风格提取 JSON 解析失败")
            return None

        # 校验必要字段
        required_fields = ["name", "persona", "dna_sample", "structure"]
        for field in required_fields:
            if not style.get(field):
                logger.warning(f"风格提取缺少字段: {field}")

        # 如果指定了 style_id，直接写入
        if style_id:
            StyleExtractor.add_manual(style_id, style)
        elif style.get("name"):
            # 用风格名作为 ID
            safe_id = style["name"].replace(" ", "_").replace("(", "").replace(")", "")[:30]
            StyleExtractor.add_manual(safe_id, style)

        return style

    @staticmethod
    def add_manual(style_id: str, style_dict: dict):
        """手动添加/更新风格到 styles_hardtech.json"""
        styles = {}
        if os.path.exists(STYLES_FILE):
            try:
                with open(STYLES_FILE, 'r', encoding='utf-8') as f:
                    styles = json.load(f)
            except Exception:
                pass

        styles[style_id] = style_dict

        with open(STYLES_FILE, 'w', encoding='utf-8') as f:
            json.dump(styles, f, ensure_ascii=False, indent=2)

        logger.info(f"✅ 风格已添加: {style_id} ({style_dict.get('name', '未知')})")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    if len(sys.argv) < 2:
        print("用法: python style_extractor.py <文章URL> [风格ID]")
        print("示例: python style_extractor.py https://example.com/article SemiAnalysis_v2")
        sys.exit(1)

    url = sys.argv[1]
    style_id = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"正在从 URL 提取风格: {url}")
    style = StyleExtractor.extract_from_url(url, style_id)

    if style:
        print(f"\n提取成功:")
        print(f"  名称: {style.get('name')}")
        print(f"  特征: {style.get('persona')}")
        print(f"  样例: {style.get('dna_sample', '')[:80]}...")
        print(f"  结构: {style.get('structure')}")
    else:
        print("提取失败")
