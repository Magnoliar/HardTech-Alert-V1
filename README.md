# ⚡ HardTech Alert System

> 多源硬科技产业情报采集 × AI 深度分析 × 自动化内容生成系统

基于 [出海Alert V3](../出海Alert%20-V3/) 的成熟架构，为**硬科技创投观察号**设计的全自动资讯情报 + 内容生成系统。

---

## ✨ 核心特性

| 特性 | 说明 |
|:--|:--|
| **🔌 7 源并行采集** | Tavily / Exa.ai / NewsAPI / GNews / Jina / Google CX / Brave 按领域智能调度 |
| **🧠 AI 多维评分** | 技术创新度 30% + 行业影响力 30% + 信息稀缺性 20% + 创投相关度 20% |
| **📐 6 大选题视角** | Megatrend / 政策解读 / 地域分析 / 公司研发 / 技术路线 / 产品合集 |
| **✍️ V3 论点驱动写作** | 论点发现 → 论证弧 → 骨架审稿 → 反驳预演 → 20+ 创意特性，4000 字+深度特稿 |
| **🛡️ 三级去重引擎** | URL 指纹 → 标题骨架 → Simhash 模糊匹配 (90 天历史持久化) |
| **🎭 去 AI 化外挂** | 静态规则 + 双重 AI 审计，消除 AI 翻译腔 |
| **🔀 Fork-Ready 架构** | 切换领域只需替换 `domain_config.py` + `styles_xxx.json` |
| **🖥️ 可视化配置** | 内置 Web 配置向导，浏览器管理 API Key |
| **📦 UV 包管理** | 使用 [uv](https://docs.astral.sh/uv/) 管理依赖和虚拟环境，秒级安装 |

---

## 🚀 5 分钟快速开始

### 1. 安装 UV (如果尚未安装)

```powershell
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 创建虚拟环境并安装依赖

```bash
cd HardTech-Alert-V1

# 一键创建虚拟环境 + 安装依赖 + 锁定版本
uv venv
uv pip install requests simhash
uv lock
```

### 3. 配置 API Key（二选一）

**方式 A：可视化向导（推荐）**
```bash
uv run python setup_wizard.py
# 自动打开浏览器 → 填入 API Key → 保存
```

**方式 B：直接编辑 config.ini**
```ini
[SOURCES]
tavily_api_key = tvly-xxxxx
newsapi_key = xxxxx
; ... 其他 Key
```

> 💡 未配置的信息源会被自动跳过（不报错）。至少配置 1 个即可运行。

### 4. 运行

```bash
uv run python main.py
```

或双击 `run_daily.bat`。

---

## 📐 系统架构

```
多源采集 (7 API)         三级去重          AI 评分
┌──────────────┐    ┌────────────┐    ┌──────────────┐
│ Tavily       │    │ URL 指纹   │    │ 技术创新 30% │
│ NewsAPI      │───▶│ 标题骨架   │───▶│ 行业影响 30% │
│ Exa.ai       │    │ Simhash    │    │ 稀缺性   20% │
│ GNews        │    └────────────┘    │ 创投度   20% │
│ Jina         │                      └──────┬───────┘
│ Google CX    │                             │
│ Brave Search │                             ▼
                                    ┌────────────────┐
                                    │ V3 选题引擎     │
                                    │ 论点驱动+聚类   │
                                    │ 情绪曲线+盲区   │
                                    └────────┬───────┘
                                             ▼
                        ┌────────────────────────────────────┐
                        │ V3 深度特稿 (骨架审稿+反驳预演)     │
                        │ 8种文风 + 20+创意特性 + 去AI化审计  │
                        └────────────────┬───────────────────┘
                                         ▼
                               ┌──────────────────┐
                               │ 📧 简报邮件       │
                               │ 📄 深度特稿邮件   │
                               │ 📊 半月谈/月谈    │
                               └──────────────────┘
```

---

## 📂 文件结构

```
HardTech-Alert-V1/
│
├── 📌 领域层 (Fork 时只需替换这 2 个文件)
│   ├── domain_config.py      # 关键词 / 分类 / 评分 / 角度 / Prompt / 品牌
│   └── styles_hardtech.json  # 文风库 (8种)
│
├── 📡 采集层 (多源)
│   ├── source_manager.py     # 采集调度器
│   └── sources/              # 信息源插件
│       ├── base_source.py    #   抽象接口 + 额度管理
│       ├── tavily_source.py
│       ├── newsapi_source.py
│       ├── exa_source.py
│       ├── gnews_source.py
│       ├── jina_source.py
│       ├── google_cx_source.py
│       └── brave_source.py
│
├── 🧠 AI 核心层
│   ├── AI.py                 # 评分 / 去重 / 简报 / alert 去重
│   ├── angle_engine.py       # V2 视角选题引擎
│   ├── angle_engine_v3.py    # V3 论点驱动选题（聚类+情绪曲线+盲区）
│   ├── llm_client.py         # 4级优先级 AI 通路
│   └── topic_tracker.py      # 话题新鲜度追踪
│
├── ✍️ 内容生成层
│   ├── article_writer_v2.py  # V2 深度特稿 (4章接力)
│   ├── article_writer_v3.py  # V3 论点驱动写作 (20+创意特性)
│   ├── write_article.py      # 独立写作入口 (CLI/Web，与V3同步)
│   ├── article_renderer.py   # Markdown → HTML 渲染
│   ├── humanizer_plugin.py   # 去 AI 化外挂
│   ├── fact_purifier.py      # 实体事实审计
│   ├── text_utils.py         # 文本工具函数
│   └── periodic_summarizer.py# 半月谈/月谈
│
├── 📧 分发层
│   └── email_generator.py    # HTML 邮件 + SMTP
│
├── ⚙️ 基础设施
│   ├── config.ini            # API Key / 邮箱 (纯基建)
│   ├── config_loader.py      # 配置加载 (基建)
│   ├── setup_wizard.py       # 可视化配置向导
│   ├── main.py               # 主入口
│   ├── run_daily.bat         # Windows 定时运行脚本
│   ├── pyproject.toml        # UV 项目定义
│   ├── uv.lock               # UV 依赖锁定
│   └── .gitignore
│
├── 📁 数据目录
│   ├── knowledge_base/       # 特稿存档 (按年/月)
│   └── archive/              # 已处理 JSON 归档
│
└── 📁 运行时自动生成
    ├── simhash_history.json  # 去重指纹库
    ├── angle_history.json    # 选题历史 (30天滚动)
    ├── topic_history.json    # 话题追踪
    ├── fact_archive.json     # 跨篇事实归档
    ├── .task_state.json      # 特稿断点续传
    └── .alert_sent_today.json # Alert 邮件去重状态
```

---

## 🔀 解耦架构：如何 Fork 到新领域

本系统所有领域特定的"知识"集中在 **2 个文件**中，引擎层完全解耦。

### Fork 所需替换的文件

| 文件 | 包含内容 |
|:--|:--|
| `domain_config.py` | 品牌名 / 关键词矩阵 / 分类体系 / 评分维度 / 6大选题视角 / 全部 AI Prompt |
| `styles_xxx.json` | 文风库 (多种写作人格的 DNA 样本) |

### 操作步骤

```bash
# 1. 复制项目
cp -r HardTech-Alert-V1 NewEnergy-Alert-V1
cd NewEnergy-Alert-V1

# 2. 编辑 domain_config.py —— 全部领域知识在此
#    修改: brand, keyword_matrix, categories, scoring_dimensions,
#          content_angles, prompts

# 3. 替换 styles_xxx.json —— 新领域的文风库

# 4. 更新 config.ini —— 如果邮件收件人不同

# 5. 完成！所有引擎模块自动从 domain_config.py 读取
uv run python main.py
```

**无需修改** 的文件：source_manager / AI / article_writer / email_generator / simout / llm_client 等全部引擎模块。

---

## 🔑 API Key 获取指南

| 服务 | 免费额度 | 注册地址 | 用途 |
|:--|:--|:--|:--|
| **Tavily** | 1000 次/月 | https://tavily.com | 深度搜索 (长文本摘要) |
| **Exa.ai** | 1000 次/月 | https://exa.ai | 研报/白皮书精准溯源 |
| **NewsAPI** | 100 次/天 | https://newsapi.org | 全球实时新闻快讯 |
| **GNews** | 100 次/天 | https://gnews.io | 中文新闻补位 |
| **Brave** | 1000 次/月 | https://brave.com/search/api | 独立索引搜索+新闻 |
| **Google CX** | 100 次/天 | https://cse.google.com | 定向网站搜索 |
| **Jina** | 有免费额度 | https://jina.ai | 全能搜索 (已从V3继承) |

---

## 📊 6 大选题视角详解

| 视角 | 触发条件 | 写作框架 |
|:--|:--|:--|
| **产业 Megatrend** | 多条新闻指向同一趋势 | 现象→驱动→投资映射→终局推演 |
| **政策深度解读** | 出现重大政策/管制新闻 | 政策要点→意图→传导→策略建议 |
| **地域产业分析** | 多条新闻集中于同一地区 | 画像→集群→玩家→投资机会 |
| **上市公司研发** | 头部公司多条关联新闻 | 动作→对比→护城河→估值 |
| **技术路线解析** | 重大技术突破/新架构 | 代际对比→挑战→时间表→标的 |
| **重磅产品合集** | 多个新品/展会新闻 | 横评→亮点→供应链→格局 |

---

## 🚀 V3 写作管线特性

V3 是论点驱动的深度写作管线，核心理念：先发现论点，再构建论证弧；先有故事，再有章节。

| 特性 | 编号 | 说明 |
|:--|:--|:--|
| 骨架审稿 | B1 | 写完第 1 章后 LLM 审查，动态修正后续大纲 |
| 自适应章节数 | B2 | 根据核心新闻密度自动调整 3-5 章 |
| 预建事实库 | B3 | 写作前批量预搜索每章方向的事实 |
| 历史反馈闭环 | B4 | 从选题历史提取反馈信号 |
| 多风格分段注入 | B5 | 每章根据论证目标匹配不同写作风格 |
| 反驳预演 | B6 | 每章写完后生成质疑，注入下一章 |
| 动态标题后置 | B7 | 写完后基于全文重新生成标题候选 |
| 跨日叙事连续性 | B8 | 注入近期文章线索形成系列叙事 |
| 章节权重自适应 | B9 | 根据信息量分配每章字数目标 |
| 竞品叙事差异化 | B10 | 注入"市场共识会怎么写"以避免同质化 |
| 自动引用往期 | B11 | 文末自动匹配历史相关文章 |
| 事实新鲜度标注 | B13 | 搜索结果标注数据时效性 |
| 情绪曲线 | C1 | 为每章设计不同情绪基调，防重复 |
| 论点强度评分 | C2 | 量化评估论点质量，弱论点触发重写 |
| 反向大纲验证 | C3 | 从成文提取实际大纲，检测主题漂移 |
| 半衰期预测 | C4 | 预测文章时效性，影响归档策略 |
| 事实核查网 | C5 | 提取关键数据点，交叉搜索验证 |
| 文末互动钩子 | C6 | 自动生成有争议性的结尾问题 |
| 开篇钩子 A/B | C8 | 3 种风格开篇候选，选张力最高 |
| 矛盾检测 | C9 | 检查今日论点是否与历史论点矛盾 |
| 盲区分析 | C13 | 发现市场忽略但有证据的角度 |
| 数据故事化 | C14 | 将纯数据罗列改写为叙事表达 |
| 引用密度优化 | C16 | 统计每千字数据引用密度 |

---

## 🛠️ 故障排查

| 问题 | 解决方案 |
|:--|:--|
| 采集结果为空 | 检查 config.ini 中 API Key，运行 `uv run python setup_wizard.py` 查看状态灯 |
| AI 评分全部失败 | 系统触发"终极兜底"提取前 20 条；检查云雾 API Key 余额 |
| 邮件发送失败 | 检查 SMTP 配置，确认已开启授权码 |
| 深度特稿超时 | 有断点续传 (`.task_state.json`)，重新运行自动续写 |
| `uv` 命令找不到 | 新终端窗口中重试，或手动添加 `~/.local/bin` 到 PATH |

---

## ⏰ 定时自动运行

**Windows 任务计划程序**：指向 `run_daily.bat`，每日 08:30 运行。

**GitHub Actions**：
```yaml
on:
  schedule:
    - cron: '30 0 * * *'  # UTC 00:30 = 北京 08:30
jobs:
  daily-alert:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv venv && uv pip install requests simhash
      - run: uv run python main.py
        env:
          AI_API_KEY_CHEAP: ${{ secrets.AI_API_KEY_CHEAP }}
          TAVILY_API_KEY: ${{ secrets.TAVILY_API_KEY }}
          # ... 其他 Key (用环境变量覆盖 config.ini)
```

---

## 📄 License

MIT
