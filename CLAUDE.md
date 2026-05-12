# HardTech-Alert-V1 — Claude Code Context

## Project Overview

多源硬科技产业情报采集 + AI 深度分析 + 自动化内容生成系统。每日自动运行：采集 → 去重 → AI 评分 → 选题 → 写作 → 邮件分发。

## Key Architecture

- **V2 管线**: `angle_engine.py` → `article_writer_v2.py` (4章接力)
- **V3 管线**: `angle_engine_v3.py` → `article_writer_v3.py` (论点驱动，骨架审稿，20+ 创意特性)
- **Standalone**: `write_article.py` (CLI/Web 独立入口，与 V3 同步)
- **领域解耦**: `domain_config.py` + `styles_hardtech.json` 是唯一需要替换的文件

## CLI Usage

```bash
python main.py                  # 完整流程：采集→去重→评分→选题→写作→邮件
python main.py --skip-score     # 跳过评分，用缓存的 top_news
python main.py --article-only   # 跳过采集+评分，直接用缓存写特稿
python write_article.py         # 独立写作入口（Web/CLI）
```

## V3 Pipeline Flow

1. **新闻聚类** (`_cluster_news`): core/support/outlier 分类，密度评估 → 自适应章节数
2. **论点发现** (`_thesis_driven_plan`): 中心论点 + 论证弧 + 情绪曲线 + 盲区分析
3. **预建事实库** (B3): 按章节目标批量预搜索，keyed by chapter title
4. **开篇钩子** (C8): 3 种风格 A/B 生成，选张力最高
5. **章节写作循环** (while loop): B1 骨架审稿 + B6 反驳预演 + C1 情绪注入
6. **后处理**: C2 论点评分 → C3 反向大纲 → C5 事实核查 → C14 数据故事化 → C6 互动钩子 → B11 往期引用
7. **归档分发**: 原子写入 KB + HTML 邮件

## Key Design Decisions

- **While loop for chapters** (not for-range): B1 may shorten outline mid-loop
- **fact_library keyed by title** (not index): survives B1 outline adjustment
- **C7 competitor overlap**: currently disabled (no data source), placeholder left
- **Alert dedup**: `.alert_sent_today.json` state file, date-based check
- **B1 outline resume**: `self.state['outline']` preferred over `topic.get('outline')` on resume

## File Map

| File | Role |
|------|------|
| `AI.py` | 评分 + alert 去重 + top_news 缓存 |
| `angle_engine_v3.py` | V3 选题：聚类 + 论点 + 情绪曲线 + 盲区 |
| `article_writer_v3.py` | V3 写作：~1800 行，20+ 特性 |
| `write_article.py` | Standalone 写作（与 V3 同步） |
| `main.py` | 主入口，5 阶段流水线 |
| `domain_config.py` | 领域配置（关键词/分类/评分/prompt） |
| `llm_client.py` | LLM 调用（4 级优先级） |
| `simout.py` | 三级去重（URL/标题/Simhash） |
| `source_manager.py` | 多源采集调度 |
| `article_renderer.py` | Markdown → HTML |
| `email_generator.py` | SMTP 邮件发送 |
| `humanizer_plugin.py` | 去 AI 化 |
| `fact_purifier.py` | 实体事实审计 |
| `periodic_summarizer.py` | 半月谈/月谈 |

## Runtime Files

| File | Purpose |
|------|---------|
| `{date}-Raw.json` | 当日原始采集 |
| `{date}-Clean.json` | 去重后数据 |
| `{date}-top_news.json` | 评分后 top 新闻缓存 |
| `.alert_sent_today.json` | Alert 邮件去重状态 |
| `.task_state.json` | V3 断点续传状态 |
| `angle_history.json` | 选题历史（30天滚动） |
| `simhash_history.json` | Simhash 去重指纹库 |
| `fact_archive.json` | 跨篇事实归档 |

## Known Issues / TODO

- C7 (竞品相似度检查): 数据源未接入，当前为占位注释
- `write_article.py` 需要与 `article_writer_v3.py` 保持同步（已同步至 2026-05-12）
- AI.py `record_scored_articles` 可能非幂等，需验证 topic_tracker.py

## Conventions

- 所有文件 UTF-8 编码
- 日志用 `logging` 模块，终端只显示 WARNING+
- 文件写入用原子操作（tmp + os.replace）
- LLM 调用通过 `llm_client.call_ai_api()` 统一封装
- 事件通知通过 `emit_event()` 发送
