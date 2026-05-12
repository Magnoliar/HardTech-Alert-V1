import json
import os
import re
import logging
from datetime import datetime, timedelta
from config_loader import load_config
from llm_client import call_ai_api, extract_json_from_text
from domain_config import DOMAIN

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
ANGLE_HISTORY_FILE = os.path.join(_DIR, "angle_history.json")


class AngleEngineV3:
    """
    V3 论点驱动选题引擎
    核心改变：先发现论点，再构建论证弧；先有故事，再有章节。
    V2 兜底：如果论点发现失败，回退到 V2 的视角选择逻辑。
    """

    def __init__(self):
        self.config = load_config()
        self.angles = DOMAIN["content_angles"]
        self._history = self._load_history()

    def select_and_plan(self, scored_news):
        """
        分析今日高分新闻，发现中心论点，生成论证弧选题包。
        返回 topic dict:
        { thesis, narrative_thread, granularity, title_proposal, outline, counter_argument, selected_angle, news_clusters }
        """
        if not scored_news:
            return None

        # 1. 分析新闻分布特征
        distribution = self._analyze_distribution(scored_news)

        # 1.5 新闻语义聚类：分组 + 剔除离群项
        cluster_result = self._cluster_news(scored_news, distribution)

        # 2. V3: 论点发现 + 论证弧构建（只用 core 新闻）
        topic = self._thesis_driven_plan(scored_news, distribution, cluster_result)

        # B2: 注入密度评估到 topic
        if topic and cluster_result and "density_assessment" in cluster_result:
            da = cluster_result["density_assessment"]
            topic["suggested_chapter_count"] = da["suggested_chapter_count"]
            topic["density"] = da["density"]
            logger.info(f"📐 B2 自适应章节数: {da['suggested_chapter_count']}章 ({da['reason']})")

        # 3. 记录本次选题到历史
        if topic:
            self._record_selection(topic)

        return topic

    def _analyze_distribution(self, news_list):
        """分析分类与标签分布，辅助 AI 决策"""
        cat_counts = {}
        all_tags = []
        entity_mentions = {}

        for item in news_list:
            p = item.get('parsed', {})
            cat = p.get('category', '其他')
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            all_tags.extend(p.get('tags', []))

            for kw in p.get('keywords', []):
                entity_mentions[kw] = entity_mentions.get(kw, 0) + 1

        top_entities = sorted(entity_mentions.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "category_distribution": cat_counts,
            "top_tags": list(set(all_tags))[:15],
            "top_entities": top_entities,
            "total_news": len(news_list),
        }

    def _cluster_news(self, news_list, distribution):
        """语义聚类：将新闻分为 core/support/outlier，为后续章节分配素材"""
        news_summary = []
        for item in news_list[:15]:
            p = item.get('parsed', {})
            news_summary.append(
                f"ID {item.get('final_index')}. [{p.get('category')}] "
                f"{p.get('title')} ({p.get('score')}分) "
                f"标签:{','.join(p.get('tags', []))} "
                f"关键词:{','.join(p.get('keywords', []))}"
            )
        news_str = "\n".join(news_summary)

        prompt = f"""你是硬科技产业新闻分析师。请对以下新闻进行语义聚类分析。

**任务**：
1. 将新闻按主题关联度分为 core（核心）、support（支撑）、outlier（离群）三类
2. core = 可以紧密关联、共同支撑一个深度论点的新闻（3-8条）
3. support = 与核心主题相关、可作为补充证据的新闻
4. outlier = 与其他新闻关联弱，不适合放进同一篇文章
5. 为 core+support 新闻建议分组，每组可对应一个章节

**今日新闻**：
{news_str}

**输出要求（严格JSON格式）**：
{{
    "clusters": [
        {{
            "label": "集群主题",
            "type": "core",
            "item_ids": [1, 3, 5],
            "reason": "关联原因"
        }},
        {{
            "label": "补充证据",
            "type": "support",
            "item_ids": [2, 7],
            "reason": "关联原因"
        }},
        {{
            "label": "离群主题",
            "type": "outlier",
            "item_ids": [8, 12],
            "reason": "与其他新闻关联弱"
        }}
    ],
    "suggested_chapters": [
        {{
            "chapter_theme": "章节方向",
            "item_ids": [1, 3, 5],
            "argument_hint": "这组新闻可以论证什么",
            "recommended_style": "SemiAnalysis"
        }}
    ],
    "filtered_out": {{
        "item_ids": [8, 12],
        "reason": "建议剔除原因"
    }}
}}"""

        response = call_ai_api(
            [{"role": "system", "content": "你是硬科技产业新闻聚类分析师。只输出JSON，不要解释。"},
             {"role": "user", "content": prompt}],
            description="V3 News Clustering"
        )

        if response:
            result = extract_json_from_text(response)
            if result and "clusters" in result:
                # 统计并记录（用 set 防止同一 ID 被多个 cluster 重复计入）
                core_ids = set()
                support_ids = set()
                outlier_ids = set()
                for c in result.get("clusters", []):
                    t = c.get("type", "")
                    ids = c.get("item_ids", [])
                    if t == "core":
                        core_ids.update(ids)
                    elif t == "support":
                        support_ids.update(ids)
                    elif t == "outlier":
                        outlier_ids.update(ids)

                # B2: 素材信息密度检测 → 自适应章节数
                core_count = len(core_ids)
                if core_count <= 3:
                    density, suggested_chapters, density_reason = "low", 3, f"核心新闻仅{core_count}条，建议精炼论述"
                elif core_count <= 6:
                    density, suggested_chapters, density_reason = "medium", 4, f"核心新闻{core_count}条，标准深度版"
                else:
                    density, suggested_chapters, density_reason = "high", 5, f"核心新闻{core_count}条，可展开深度论述"
                result["density_assessment"] = {
                    "density": density,
                    "suggested_chapter_count": suggested_chapters,
                    "reason": density_reason,
                }

                logger.info(
                    f"📊 新闻聚类: core={core_count}条 support={len(support_ids)}条 "
                    f"outlier={len(outlier_ids)}条 | "
                    f"建议章节: {len(result.get('suggested_chapters', []))}个 | "
                    f"密度: {density} → 建议{suggested_chapters}章"
                )
                for ch in result.get("suggested_chapters", []):
                    logger.info(f"  📁 {ch.get('chapter_theme', '?')}: 新闻IDs {ch.get('item_ids', [])} → {ch.get('argument_hint', '')}")

                return result

        logger.warning("新闻聚类失败，回退到全量模式")
        return None

    def _thesis_driven_plan(self, news_list, distribution, cluster_result=None):
        """V3 核心：论点发现 + 论证弧构建"""
        from domain_config import get_prompt
        system_prompt = get_prompt('system_prompt')

        # 如果有聚类结果，只用 core 新闻来发现论点
        if cluster_result:
            core_ids = set()
            support_ids = set()
            for c in cluster_result.get("clusters", []):
                if c.get("type") == "core":
                    core_ids.update(c.get("item_ids", []))
                elif c.get("type") == "support":
                    support_ids.update(c.get("item_ids", []))
            # core 优先，不够时补充 support
            filtered = [n for n in news_list if n.get('final_index') in core_ids]
            if len(filtered) < 3:
                filtered += [n for n in news_list if n.get('final_index') in support_ids]
            planning_news = filtered if filtered else news_list[:15]
            logger.info(f"📐 论点发现使用 {len(planning_news)} 条聚类新闻（core={len(core_ids)} support={len(support_ids)}）")
        else:
            planning_news = news_list[:15]

        # 格式化新闻摘要
        news_summary = []
        for item in planning_news:
            p = item.get('parsed', {})
            news_summary.append(
                f"ID {item.get('final_index')}. [{p.get('category')}] "
                f"{p.get('title')} ({p.get('score')}分) - {p.get('reasoning', '')}"
            )
        news_str = "\n".join(news_summary)

        # 历史去重约束
        history_constraint = self._build_history_constraint()
        # B8: 跨日叙事连续性
        continuity_context = self._build_continuity_context()
        # B4: 历史反馈
        feedback_context = self._build_feedback_context()
        # C9: 自我矛盾检测
        contradiction_context = self._build_contradiction_check()

        user_prompt = f"""基于今日精选的硬科技新闻，请完成以下四个任务：

**任务零：发现今天值得讲的故事（核心论点）**
仔细阅读以下新闻，找到一个有张力的、值得用 4000 字深度论述的中心论点。

好的论点特征：
- 有争议性：不是"行业在发展"这种废话，而是"X正在侵蚀Y的护城河"
- 有证据链：至少 2-3 条新闻能为这个论点提供不同角度的证据
- 有因果关系：不是并列罗列，而是"A导致B，B触发C"
- 有具体实体：必须包含公司名、技术名或数据，禁止空泛的"产业趋势"

好的论点举例：
- "特斯拉自建晶圆厂意味着英伟达对 CoWoS 的议价权正在被分散"
- "出口管制从终端禁运升级到通道封堵，倒逼国产算力集群的交付闭环"
- "比亚迪的垂直整合模式让欧盟关税形同虚设"

差的论点举例：
- "算力基础设施面临物理瓶颈"（没有争议性）
- "半导体产业链正在重构"（太空泛）
- "AI技术持续发展"（废话）

**任务一：确定叙事粒度**
根据今日新闻的实际特征，选择最合适的叙事粒度：
- 宏观（产业大势）：多条新闻指向同一方向的产业趋势
- 中观（技术脉络/跨域碰撞）：聚焦某条技术线或领域交叉
- 微观（公司深描/事件催化）：围绕一家公司或一个事件

**任务二：围绕论点构建论证弧**
基于你发现的论点，生成论证大纲。大纲不是模板化的章节列表，而是论证这个论点的逻辑步骤：
- 开篇：抛出论点（用一个具体事件或数据切入，不要宏大叙述）
- 中间：2-4 个论证角度（每个角度用一条或多条新闻作为证据）
- 可选：反驳/复杂性（市场共识可能认为的反面观点）
- 收束：对创投的含义（具体、可操作，不要空泛的"关注XXX赛道"）

**任务三：生成竞品叙事（用于差异化）**
请生成 2 个"市场共识/普通分析师会怎么写这个话题"的叙事角度，每个 1-2 句话。
这些角度将用于提醒主笔避免同质化。

**任务三B：盲区分析**
基于今日新闻，分析：
1. 市场共识可能关注的角度（大家都在写的）
2. 市场共识可能忽略的角度（但今日新闻中有证据支撑）
3. 推荐的差异化切入点
好的盲区举例："今日有2条散热相关新闻，但市场注意力都在算力芯片上，散热瓶颈可能是被低估的变量"

**任务四：设计情绪曲线（Narrative Arc）**
为文章设计情绪节奏。不要每次都是"分析→展望"的平铺直叙。
弧线池（选择最适合今天论点的，但不要与近期已用弧线重复）：
- 冲突型：悬念→证据→反转→行动
- 发现型：好奇→探索→意外→启示
- 危机型：警告→归因→影响→出路
- 对比型：表象→真相→差异→选择
- 递进型：现象→机制→推演→终局
- 反转型：共识→质疑→新证据→重构

**今日新闻分布特征**：
- 分类分布: {json.dumps(distribution['category_distribution'], ensure_ascii=False)}
- 高频标签: {distribution['top_tags']}
- 高频实体: {distribution['top_entities']}

**今日精选新闻**：
{news_str}
{history_constraint}
{continuity_context}
{feedback_context}
{contradiction_context}

**输出要求（JSON 格式）**：
{{
    "thesis": "中心论点（100字以内，必须有主语、谓语、宾语和冲突/张力）",
    "narrative_thread": "贯穿全文的暗线（一句话，每章都应与此产生关联）",
    "granularity": "宏观/中观/微观",
    "selected_angle": "最匹配的视角ID（megatrend/policy/regional_industry/company_rd/tech_roadmap/product_roundup/event_catalyst/cross_domain）",
    "title_proposal": "建议标题（必须包含具体实体名，有张力）",
    "outline": ["角度1标题（含实体名）", "角度2标题", ...],
    "counter_argument": "可能的反驳观点（可选，如有则增加文章思辨深度）",
    "consensus_angles": ["市场共识叙事角度1（1-2句话）", "市场共识叙事角度2"],
    "blind_spots": [
        {{"angle": "被忽略的角度", "evidence": "今日有X条相关新闻但市场可能忽略", "opportunity": "差异化切入点说明"}}
    ],
    "narrative_arc": [
        {{"chapter": 1, "emotion": "情绪基调", "purpose": "写作目标"}},
        {{"chapter": 2, "emotion": "...", "purpose": "..."}}
    ],
    "sources": ["建议信源类型"]
}}"""

        response = call_ai_api(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            description="V3 Thesis Discovery & Planning"
        )

        if response:
            topic = extract_json_from_text(response)
            if topic and self._validate_topic(topic):
                logger.info(
                    f"📐 V3 论点发现成功: {topic.get('thesis', '')[:60]}... | "
                    f"粒度: {topic.get('granularity')} | 标题: {topic.get('title_proposal')}"
                )
                # 兜底：如果 outline 为空，用对应视角的模板
                angle_id = topic.get('selected_angle', 'megatrend')
                if angle_id in self.angles and not topic.get('outline'):
                    topic['outline'] = self.angles[angle_id]['outline_template']
                # 注入聚类结果供文章写作阶段使用
                if cluster_result:
                    topic['news_clusters'] = cluster_result
                return topic

        # 兜底：回退到 V2 逻辑
        logger.warning("V3 论点发现失败，回退到 V2 视角选择")
        return self._v2_fallback(news_list, distribution)

    def _validate_topic(self, topic):
        """校验论点质量"""
        thesis = topic.get('thesis', '')
        if not thesis or len(thesis) < 15:
            return False

        # 论点必须包含至少一个实体名或数字
        has_entity = bool(re.search(r'[A-Z][a-zA-Z]+|[一-龥]{2,}(?:科技|集团|半导体|电子|能源|智能|电池|芯片)', thesis))
        has_number = bool(re.search(r'\d+', thesis))
        if not has_entity and not has_number:
            logger.warning(f"论点缺少实体或数据: {thesis}")
            return False

        # 论点不能太空泛
        vague_patterns = ["产业发展", "技术进步", "行业趋势", "持续增长", "未来可期"]
        if any(vague in thesis for vague in vague_patterns):
            logger.warning(f"论点太空泛: {thesis}")
            return False

        return True

    def _v2_fallback(self, news_list, distribution):
        """V2 兜底逻辑：使用 V2 的视角选择"""
        try:
            from angle_engine import AngleEngine
            v2_engine = AngleEngine()
            return v2_engine.select_and_plan(news_list)
        except Exception as e:
            logger.error(f"V2 兜底也失败了: {e}")
            return {
                "selected_angle": "megatrend",
                "title_proposal": "硬科技产业动态深度观察",
                "angle": "基于今日多维信号的产业趋势拆解",
                "outline": self.angles["megatrend"]["outline_template"],
                "sources": ["行业研报", "外媒快讯"],
                "thesis": "",
                "narrative_thread": "",
                "granularity": "宏观",
            }

    def _build_history_constraint(self):
        """构建历史去重约束"""
        recent_angles = self._get_recent_angles(days=7)
        recent_titles = self._get_recent_titles(days=7)

        constraint = ""
        if recent_angles:
            angle_counts = {}
            for a in recent_angles:
                angle_counts[a] = angle_counts.get(a, 0) + 1
            overused = [f"{a}({c}次)" for a, c in angle_counts.items() if c >= 2]
            if overused:
                constraint += f"\n\n⚠️ **视角多样性约束**：以下视角最近7天已被频繁使用，请优先选择其他视角：{', '.join(overused)}\n"

        if recent_titles:
            constraint += f"\n⚠️ **标题去重约束**：最近7天的标题如下（请避免相似措辞）：\n"
            for t in recent_titles:
                constraint += f"  - 「{t}」\n"
            constraint += "请确保今天的标题在视角、措辞、核心关键词上都与上述标题明显不同。\n"

        return constraint

    def _build_continuity_context(self):
        """B8: 跨日叙事连续性 — 从知识库中提取近3天的文章标题"""
        import glob
        _dir = os.path.dirname(os.path.abspath(__file__))
        kb_root = os.path.join(_dir, "knowledge_base")
        recent = []
        for days_back in range(1, 4):
            date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            for f in glob.glob(os.path.join(kb_root, "**", f"{date}-*.md"), recursive=True):
                fname = os.path.basename(f).replace('.md', '')
                # 提取标题部分（去掉日期和版本前缀）
                parts = fname.split('-', 3)
                title_part = parts[-1] if len(parts) > 3 else fname
                recent.append(f"  - [{date}] {title_part}")
        if recent:
            return "【近期文章线索】（如有自然关联，可以形成系列叙事）\n" + "\n".join(recent[-5:])
        return ""

    def _build_feedback_context(self):
        """B4: 历史文章反馈闭环 — 从选题历史中提取反馈信号（近7天）"""
        feedback = []
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent_selections = [
            s for s in self._history.get("selections", [])
            if s.get("date", "") >= cutoff
        ]
        # 统计近7天选题角度使用频率
        angle_counts = {}
        for s in recent_selections:
            a = s.get("angle", "")
            if a:
                angle_counts[a] = angle_counts.get(a, 0) + 1
        overused = [a for a, c in angle_counts.items() if c >= 3]
        if overused:
            feedback.append(f"- 近期频繁使用的视角: {', '.join(overused)}，建议切换")
        # 检查近期角度多样性
        if len(angle_counts) <= 2 and len(recent_selections) >= 3:
            feedback.append("- 近期视角过于单一，今天建议选择不同的叙事角度")
        if feedback:
            return "【历史反馈】\n" + "\n".join(feedback)
        return ""

    def _build_contradiction_check(self):
        """C9: 自我矛盾检测 — 从选题历史中提取近7天论点"""
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent = []
        for s in self._history.get("selections", []):
            if s.get("thesis") and s.get("date", "") >= cutoff:
                recent.append(f"  - [{s.get('date')}] {s.get('thesis')}")
        if recent:
            return "【近期论点】（请检查今日论点是否与以下历史论点矛盾，如有矛盾请在输出中说明）\n" + "\n".join(recent[-7:])
        return ""

    # ==================== 选题历史管理 ====================

    def _load_history(self):
        if not os.path.exists(ANGLE_HISTORY_FILE):
            return {"selections": []}
        try:
            with open(ANGLE_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"selections": []}

    def _record_selection(self, topic):
        try:
            record = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "angle": topic.get("selected_angle", "unknown"),
                "title": topic.get("title_proposal", ""),
                "thesis": topic.get("thesis", ""),
            }
            self._history.setdefault("selections", []).append(record)

            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            self._history["selections"] = [
                s for s in self._history["selections"]
                if s.get("date", "") >= cutoff
            ]

            with open(ANGLE_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.warning(f"记录选题历史失败（不影响主流程）: {e}")

    def _get_recent_angles(self, days=7):
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [
            s.get("angle", "")
            for s in self._history.get("selections", [])
            if s.get("date", "") >= cutoff
        ]

    def _get_recent_titles(self, days=7):
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [
            s.get("title", "")
            for s in self._history.get("selections", [])
            if s.get("date", "") >= cutoff and s.get("title")
        ]


if __name__ == "__main__":
    pass
