"""
话题级去重系统验证测试
测试内容:
1. 模块导入 & 初始化
2. L1 URL黑名单
3. L2 新鲜度衰减
4. L3 话题每日上限
5. L4 关键词配额
6. 突发保护
7. 历史记录读写
8. 使用 archive 数据的回溯测试
"""
import json
import os
import sys

def test_imports():
    """测试所有修改过的模块能否正常导入"""
    print("=" * 60)
    print("1. 模块导入测试")
    print("=" * 60)
    
    try:
        from topic_tracker import TopicTracker, is_blacklisted_url
        print("  ✅ topic_tracker 导入成功")
    except Exception as e:
        print(f"  ❌ topic_tracker 导入失败: {e}")
        return False

    try:
        from config_loader import load_config
        config = load_config()
        print(f"  ✅ config_loader 导入成功")
        print(f"     新配置项: topic_lookback_days={config.app_settings.get('topic_lookback_days')}")
        print(f"     新配置项: freshness_penalty_per_day={config.app_settings.get('freshness_penalty_per_day')}")
        print(f"     新配置项: topic_daily_cap={config.app_settings.get('topic_daily_cap')}")
        print(f"     新配置项: breakout_score_threshold={config.app_settings.get('breakout_score_threshold')}")
    except Exception as e:
        print(f"  ❌ config_loader 失败: {e}")
        return False

    try:
        import simout
        print("  ✅ simout 导入成功 (含 URL 黑名单)")
    except Exception as e:
        print(f"  ❌ simout 导入失败: {e}")
        return False

    try:
        import AI
        print("  ✅ AI 导入成功 (含话题追踪器)")
    except Exception as e:
        print(f"  ❌ AI 导入失败: {e}")
        return False

    try:
        import source_manager
        print("  ✅ source_manager 导入成功 (含 L4 配额)")
    except Exception as e:
        print(f"  ❌ source_manager 导入失败: {e}")

    return True


def test_url_blacklist():
    """L1: URL 黑名单测试"""
    print("\n" + "=" * 60)
    print("2. L1 URL 黑名单测试")
    print("=" * 60)

    from topic_tracker import is_blacklisted_url

    # 应该被过滤的 URL
    blocked = [
        "https://www.techpowerup.com/news-tags/CoWoS",
        "https://www.digitimes.com/tag/cowos/00111842.html",
        "https://example.com/category/tech",
        "https://example.com/topic/ai-chips",
        "https://example.com/search?q=cowos",
        "https://example.com/news/page/2",
        "https://example.com/feed",
    ]

    # 不应该被过滤的 URL
    allowed = [
        "https://www.cnbc.com/2026/04/08/tsmc-nvidia-advanced-packaging.html",
        "https://semiwiki.com/semiconductor-manufacturers/tsmc/366052",
        "https://markets.financialcontent.com/stocks/article/tokenring",
        "https://eu.36kr.com/en/p/3580962946874242",
        "https://blog.fugle.tw/post/cowos-industry-analysis",
    ]

    all_pass = True
    for url in blocked:
        result = is_blacklisted_url(url)
        status = "✅" if result else "❌"
        if not result:
            all_pass = False
        print(f"  {status} 应拦截: {url[:60]}")

    for url in allowed:
        result = is_blacklisted_url(url)
        status = "✅" if not result else "❌"
        if result:
            all_pass = False
        print(f"  {status} 应放行: {url[:60]}")

    # None / 空字符串
    assert not is_blacklisted_url(None), "None 应返回 False"
    assert not is_blacklisted_url(""), "空字符串应返回 False"
    print(f"  ✅ None/空字符串 安全处理")

    return all_pass


def test_freshness_penalty():
    """L2: 新鲜度衰减测试"""
    print("\n" + "=" * 60)
    print("3. L2 新鲜度衰减测试")
    print("=" * 60)

    from topic_tracker import TopicTracker
    from datetime import datetime, timedelta

    tracker = TopicTracker()

    # 模拟历史数据
    today = datetime.now()
    mock_history = {"scored_articles": {}}
    for days_ago in range(1, 6):
        date_str = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        mock_history["scored_articles"][date_str] = [
            {
                "title": "台积电CoWoS产能扩张",
                "summary": "CoWoS封装产能持续紧缺",
                "tags": ["CoWoS", "台积电", "先进封装"],
                "keywords": ["CoWoS", "TSMC"],
                "score": 80,
            }
        ]

    tracker._history = mock_history

    # 测试: CoWoS 标签应有惩罚 (连续5天)
    penalty = tracker.calc_freshness_penalty(["CoWoS", "先进封装"], ["TSMC"])
    print(f"  CoWoS 话题 (5天连续): penalty = {penalty}")
    assert penalty > 0, "连续5天的话题应有衰减惩罚"
    assert penalty == 20, f"5天应扣 min(4*5, 20)=20, 实际={penalty}"
    print(f"  ✅ 5天连续: penalty={penalty} (预期20)")

    # 测试: 全新话题应无惩罚
    penalty_new = tracker.calc_freshness_penalty(["量子计算", "新话题"], ["quantum"])
    print(f"  全新话题: penalty = {penalty_new}")
    assert penalty_new == 0, "全新话题不应有衰减惩罚"
    print(f"  ✅ 全新话题: penalty={penalty_new} (预期0)")

    # 测试: 空标签应安全返回0
    penalty_empty = tracker.calc_freshness_penalty([], [])
    assert penalty_empty == 0, "空标签应返回0"
    print(f"  ✅ 空标签: penalty={penalty_empty} (预期0)")

    return True


def test_breakout_protection():
    """突发保护测试"""
    print("\n" + "=" * 60)
    print("4. 突发保护测试")
    print("=" * 60)

    from topic_tracker import TopicTracker

    tracker = TopicTracker()

    # 90+ 分应豁免
    assert tracker.is_breakout(90) == True
    assert tracker.is_breakout(95) == True
    assert tracker.is_breakout(100) == True
    print(f"  ✅ 90/95/100 分 → 突发保护 (豁免)")

    # <90 不应豁免
    assert tracker.is_breakout(89) == False
    assert tracker.is_breakout(75) == False
    assert tracker.is_breakout(0) == False
    print(f"  ✅ 89/75/0 分 → 正常处理 (不豁免)")

    return True


def test_topic_cap():
    """L3: 话题每日上限测试"""
    print("\n" + "=" * 60)
    print("5. L3 话题每日上限测试")
    print("=" * 60)

    from topic_tracker import TopicTracker
    from datetime import datetime

    tracker = TopicTracker()
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 模拟今天已有3篇 CoWoS 文章
    tracker._history = {
        "scored_articles": {
            today_str: [
                {"title": f"CoWoS 文章 {i}", "tags": ["CoWoS"], "keywords": ["TSMC"], "score": 80}
                for i in range(3)
            ]
        }
    }

    # 默认 cap=3, 已有3篇 → 应返回0
    cap = tracker.get_topic_cap(["CoWoS"])
    print(f"  CoWoS 已有3篇: remaining_cap = {cap}")
    assert cap == 0, f"已满额应返回0, 实际={cap}"
    print(f"  ✅ 已满额: cap={cap} (预期0)")

    # 全新话题 → 应返回3
    cap_new = tracker.get_topic_cap(["量子计算"])
    print(f"  量子计算 (全新): remaining_cap = {cap_new}")
    assert cap_new == 3, f"全新话题应返回3, 实际={cap_new}"
    print(f"  ✅ 全新话题: cap={cap_new} (预期3)")

    # 空标签 → 不限制
    cap_empty = tracker.get_topic_cap([])
    assert cap_empty == 999, f"空标签应返回999, 实际={cap_empty}"
    print(f"  ✅ 空标签: cap={cap_empty} (预期999)")

    return True


def test_keyword_quota():
    """L4: 关键词配额测试"""
    print("\n" + "=" * 60)
    print("6. L4 关键词配额测试")
    print("=" * 60)

    from topic_tracker import TopicTracker
    from datetime import datetime, timedelta

    tracker = TopicTracker()
    today = datetime.now()

    # 模拟5天高频覆盖 CoWoS
    mock_history = {"scored_articles": {}}
    for days_ago in range(1, 6):
        date_str = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        mock_history["scored_articles"][date_str] = [
            {"title": f"CoWoS 产能分析 {i}", "tags": ["CoWoS", "先进封装"], "keywords": ["CoWoS"], "score": 80}
            for i in range(3)
        ]

    tracker._history = mock_history

    quota_cowos = tracker.get_topic_quota("先进封装 CoWoS")
    print(f"  '先进封装 CoWoS' (5天x3篇): quota = {quota_cowos}")
    assert quota_cowos <= 1, f"高频话题应降低配额, 实际={quota_cowos}"
    print(f"  ✅ 高频话题配额已降低: {quota_cowos}")

    # 全新话题
    quota_new = tracker.get_topic_quota("核聚变 商业化")
    print(f"  '核聚变 商业化' (全新): quota = {quota_new}")
    assert quota_new == 5, f"全新话题应默认配额5, 实际={quota_new}"
    print(f"  ✅ 全新话题默认配额: {quota_new}")

    return True


def test_archive_backtest():
    """回溯测试: 用 archive 数据验证 URL 黑名单效果"""
    print("\n" + "=" * 60)
    print("7. Archive 回溯测试 (URL 黑名单效果)")
    print("=" * 60)

    import re
    from topic_tracker import is_blacklisted_url

    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'archive')
    if not os.path.exists(archive_dir):
        print("  ⏭️ archive 目录不存在，跳过")
        return True

    total_articles = 0
    blacklisted = 0
    blacklisted_urls = []

    for fname in sorted(os.listdir(archive_dir)):
        if not fname.endswith('-Raw.json'):
            continue
        fpath = os.path.join(archive_dir, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for mail in data:
            if 'body' not in mail:
                continue
            for cat, articles in mail['body'].items():
                for art in articles:
                    total_articles += 1
                    url_match = re.search(r'<(https?://[^>]+)>', art)
                    if url_match:
                        url = url_match.group(1)
                        if is_blacklisted_url(url):
                            blacklisted += 1
                            blacklisted_urls.append((fname, url[:70]))

    print(f"  总文章数: {total_articles}")
    print(f"  URL黑名单命中: {blacklisted}")
    if blacklisted_urls:
        print(f"  示例:")
        for fname, url in blacklisted_urls[:5]:
            print(f"    {fname}: {url}")

    return True


def test_graceful_degradation():
    """鲁棒性测试: 模拟各种失败场景"""
    print("\n" + "=" * 60)
    print("8. 鲁棒性降级测试")
    print("=" * 60)

    from topic_tracker import TopicTracker

    # 场景1: 空历史
    tracker = TopicTracker()
    tracker._history = {"scored_articles": {}}
    penalty = tracker.calc_freshness_penalty(["CoWoS"], ["TSMC"])
    assert penalty == 0, "空历史应返回0"
    print(f"  ✅ 空历史 → penalty=0")

    # 场景2: 损坏的历史数据
    tracker._history = {"scored_articles": {"bad-date": "not-a-list"}}
    penalty = tracker.calc_freshness_penalty(["CoWoS"], ["TSMC"])
    assert penalty == 0, "损坏数据应安全返回0"
    print(f"  ✅ 损坏的历史数据 → penalty=0")

    # 场景3: None tags/keywords
    tracker._history = {"scored_articles": {}}
    penalty = tracker.calc_freshness_penalty(None, None)
    assert penalty == 0, "None 输入应返回0"
    print(f"  ✅ None tags → penalty=0")

    # 场景4: topic_cap with no history
    cap = tracker.get_topic_cap(["任意话题"])
    assert cap >= 3, "无历史应返回完整配额"
    print(f"  ✅ 无历史 → cap={cap}")

    # 场景5: keyword quota with no history
    quota = tracker.get_topic_quota("先进封装 CoWoS")
    assert quota == 5, "无历史应返回默认配额5"
    print(f"  ✅ 无历史 → quota={quota}")

    return True


if __name__ == "__main__":
    print("🧪 话题级去重系统验证测试\n")

    results = []
    results.append(("模块导入", test_imports()))
    results.append(("L1 URL黑名单", test_url_blacklist()))
    results.append(("L2 新鲜度衰减", test_freshness_penalty()))
    results.append(("突发保护", test_breakout_protection()))
    results.append(("L3 话题上限", test_topic_cap()))
    results.append(("L4 关键词配额", test_keyword_quota()))
    results.append(("Archive 回溯", test_archive_backtest()))
    results.append(("鲁棒性降级", test_graceful_degradation()))

    print("\n" + "=" * 60)
    print("📊 测试结果汇总")
    print("=" * 60)
    all_pass = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("🎉 所有测试通过!")
    else:
        print("⚠️ 部分测试失败，请检查上方输出")
    
    sys.exit(0 if all_pass else 1)
