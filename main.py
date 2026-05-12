# main.py — 多源硬科技情报系统主入口
import os
import argparse
import logging
import logging.handlers
import shutil
import json
import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config_loader import load_config
from source_manager import SourceManager
from domain_config import DOMAIN
import simout
import AI
from angle_engine import AngleEngine

# 双通道：文件日志记录全部，终端只显示警告和错误
_file_handler = logging.handlers.RotatingFileHandler(
    'run_log.txt', maxBytes=1024*1024, backupCount=5, encoding='utf-8'
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)
_console_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler])
logger = logging.getLogger(__name__)

console = Console()


def archive_files(file_paths):
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'archive')
    if not os.path.exists(archive_dir): os.makedirs(archive_dir)
    for file_path in file_paths:
        if file_path and os.path.exists(file_path):
            try:
                dest = os.path.join(archive_dir, os.path.basename(file_path))
                if os.path.exists(dest): os.remove(dest)
                shutil.move(file_path, dest)
                logger.info(f"已归档: {os.path.basename(file_path)}")
            except Exception as e:
                logger.error(f"归档失败: {e}")


def main():
    parser = argparse.ArgumentParser(description=f"{DOMAIN['brand']} 多源硬科技情报系统")
    parser.add_argument('--skip-score', action='store_true', help='跳过阶段3评分，直接用今日 Clean.json 进入选题+写作')
    parser.add_argument('--article-only', action='store_true', help='跳过阶段1-3，直接用已有的 top_news 数据写特稿')
    args = parser.parse_args()

    brand = DOMAIN['brand']
    brand_emoji = DOMAIN['brand_emoji']
    start_time = time.time()

    # 运行指标收集
    metrics = {
        'raw_count': 0,
        'clean_count': 0,
        'selected_count': 0,
        'top_score': 0,
        'low_score': 0,
        'angle': '',
        'topic': '',
        'article_generated': False,
        'article_words': 0,
        'email_daily': False,
        'email_deep': False,
        'periodic_report': False,
    }

    # 启动面板
    console.print()
    console.print(Panel(
        f"[bold white]{brand_emoji} {brand}[/bold white]\n"
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M')}[/dim]",
        title="[bold cyan]启动[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    logger.info(f"{brand_emoji} {brand} 启动 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    config = load_config()

    today_str = datetime.now().strftime('%Y-%m-%d')
    _dir = os.path.dirname(os.path.abspath(__file__))
    raw_json = os.path.join(_dir, f"{today_str}-Raw.json")
    clean_json = os.path.join(_dir, f"{today_str}-Clean.json")
    top_news_file = os.path.join(_dir, f"{today_str}-top_news.json")

    json_to_process = None

    # --article-only 模式：跳过阶段 1-3，直接用缓存的 top_news
    if args.article_only:
        if not os.path.exists(top_news_file):
            console.print(f"[red]未找到缓存的 top_news: {os.path.basename(top_news_file)}[/red]")
            console.print("[red]请先正常运行一次，或去掉 --article-only 参数。[/red]")
            return
        console.print(f"  [green]✓ 加载缓存 top_news[/green] {os.path.basename(top_news_file)}")
        with open(top_news_file, 'r', encoding='utf-8') as f:
            top_news = json.load(f)
        metrics['selected_count'] = len(top_news)
        scores = [n.get('parsed', {}).get('score', 0) for n in top_news]
        metrics['top_score'] = max(scores) if scores else 0
        metrics['low_score'] = min(scores) if scores else 0
        console.print(f"  [dim]跳过阶段 1-3，直接进入选题+写作[/dim]")
        # 跳到阶段 4
        json_to_process = None  # 标记跳过常规流程
        # 直接进入阶段 4 写作
        console.print()
        console.print("[bold cyan][4/5][/bold cyan] ✍️  深度特稿")
        pipeline_version = config.app_settings.get('writing_pipeline', 'v2')
        console.print(f"  [dim]写作管线: {pipeline_version}[/dim]")
        console.print("  ⏳ AI 选题规划中...")
        if pipeline_version == 'v3':
            from angle_engine_v3 import AngleEngineV3
            engine = AngleEngineV3()
        else:
            engine = AngleEngine()
        topic = engine.select_and_plan(top_news)
        if topic:
            metrics['angle'] = topic.get('selected_angle', '')
            metrics['topic'] = topic.get('title_proposal', '')
            thesis = topic.get('thesis', '')
            if thesis:
                console.print(f"  [green]✓ 论点:[/green] [bold]{thesis[:60]}...[/bold]")
            console.print(f"  [green]✓ 选题:[/green] [bold]{metrics['topic']}[/bold]")
            console.print(f"  [green]✓ 视角:[/green] [dim]{metrics['angle']}[/dim]")
            outline = topic.get('outline', [])
            console.print(f"  ⏳ 写作中 ({len(outline)} 章节)...")
            if pipeline_version == 'v3':
                from article_writer_v3 import ArticleWriterV3
                deep_writer = ArticleWriterV3()
            else:
                from article_writer_v2 import ArticleWriterV2
                deep_writer = ArticleWriterV2()
            deep_writer.run(topic, top_news)
            metrics['article_generated'] = True
            console.print("  [green]✓ 特稿已生成、归档并发送[/green]")
        else:
            console.print("  [yellow]选题失败[/yellow]")
        # 归档
        console.print()
        console.print("[bold cyan][5/5][/bold cyan] 📧 收尾")
        console.print("  ⏳ 归档数据...")
        archive_files([raw_json, clean_json])
        console.print("  [green]✓ 数据已归档[/green]")
        # 运行报告
        elapsed = time.time() - start_time
        elapsed_str = f"{int(elapsed//60)}分{int(elapsed%60)}秒"
        table = Table(title="运行报告", border_style="cyan", show_lines=False, padding=(0, 2))
        table.add_column("项目", style="dim", no_wrap=True)
        table.add_column("结果", style="bold")
        table.add_row("模式", "[yellow]--article-only[/yellow]")
        table.add_row("耗时", elapsed_str)
        if metrics['selected_count']:
            table.add_row("入选", f"{metrics['selected_count']} 条")
        if metrics['topic']:
            table.add_row("选题", metrics['topic'])
        if metrics['article_generated']:
            table.add_row("特稿", "[green]已生成[/green]")
        console.print()
        console.print(table)
        console.print()
        return

    # --- 阶段 1/5 · 多源采集 ---
    console.print()
    console.print("[bold cyan][1/5][/bold cyan] 📡 多源采集")

    # 策略 A: 已有去重后数据
    if os.path.exists(clean_json):
        console.print(f"  [green]✓ 发现本地去重后数据[/green] {os.path.basename(clean_json)}")
        logger.info(f"发现本地去重后数据: {clean_json}")
        json_to_process = clean_json

    # 策略 B: 已有原始数据，需要去重
    elif os.path.exists(raw_json):
        console.print(f"  [green]✓ 发现本地原始数据[/green] {os.path.basename(raw_json)}")
        console.print("  ⏳ 执行去重中...")
        logger.info(f"发现本地原始数据: {raw_json}，执行去重...")
        if simout.deduplicate_news_optimized(raw_json, clean_json):
            json_to_process = clean_json
            console.print("  [green]✓ 去重完成[/green]")

    # 策略 C: 归档中查找
    elif os.path.exists(os.path.join(_dir, 'archive', os.path.basename(clean_json))):
        console.print("  [green]✓ 在归档中发现今日数据，正在恢复...[/green]")
        logger.info(f"在归档中发现今日数据，正在恢复: {clean_json}")
        shutil.copy(os.path.join(_dir, 'archive', os.path.basename(clean_json)), clean_json)
        json_to_process = clean_json

    # 策略 D: 执行多源采集
    if not json_to_process:
        console.print("  [yellow]未发现今日缓存，启动多源采集...[/yellow]")
        logger.info("未发现今日缓存，启动多源采集...")
        sm = SourceManager()
        console.print(f"  ⏳ 信息源初始化完成，可用通道: {len(sm.sources)} 个")
        console.print("  ⏳ 并行采集中 (多领域同时执行)...")
        t0 = time.time()
        fetched_file = sm.collect_all()
        elapsed_collect = time.time() - t0
        if fetched_file:
            console.print(f"  [green]✓ 采集完成[/green] (耗时 {elapsed_collect:.0f}s)")
            console.print("  ⏳ 去重中...")
            target_clean = fetched_file.replace("-Raw.json", "-Clean.json")
            if simout.deduplicate_news_optimized(fetched_file, target_clean):
                json_to_process = target_clean
                raw_json = fetched_file
                clean_json = target_clean
                console.print("  [green]✓ 去重完成[/green]")

    if not json_to_process:
        console.print("[red]流程结束：今日无新资讯或采集失败。[/red]")
        logger.info("流程结束：今日无新资讯或采集失败。")
        return

    # --- 阶段 2/5 · 智能去重 ---
    console.print()
    console.print("[bold cyan][2/5][/bold cyan] 🔍 智能去重")

    try:
        with open(json_to_process, 'r', encoding='utf-8') as f:
            data = json.load(f)
            metrics['raw_count'] = len(data)
            if not data:
                console.print("[yellow]内容为空 (可能是100%重复)，流程终止。[/yellow]")
                logger.info("内容为空 (可能是100%重复)，流程终止。")
                archive_files([raw_json, clean_json])
                return
            console.print(f"  去重后保留 [bold]{len(data)}[/bold] 条")
            logger.info(f"去重后保留 {len(data)} 条")

        # --- 阶段 3/5 · AI 评分筛选 ---
        console.print()
        console.print("[bold cyan][3/5][/bold cyan] 🤖 AI 评分筛选")

        if args.skip_score and os.path.exists(top_news_file):
            console.print(f"  [yellow]--skip-score: 加载缓存 top_news[/yellow] {os.path.basename(top_news_file)}")
            logger.info(f"--skip-score: 加载缓存 top_news: {top_news_file}")
            with open(top_news_file, 'r', encoding='utf-8') as f:
                top_news = json.load(f)
        else:
            if args.skip_score:
                console.print("  [yellow]缓存 top_news 不存在，回退到正常评分[/yellow]")

            def _ai_progress(batch_num, total, matched):
                console.print(f"  ⏳ 批次 {batch_num}/{total} 完成 (已匹配 {matched} 条)")

            top_news = AI.process_and_send_alerts(json_to_process, progress_callback=_ai_progress)

        if top_news:
            metrics['selected_count'] = len(top_news)
            scores = [n.get('parsed', {}).get('score', 0) for n in top_news]
            metrics['top_score'] = max(scores) if scores else 0
            metrics['low_score'] = min(scores) if scores else 0
            console.print(f"  入选 [bold]{len(top_news)}[/bold] 条 | 最高分 [bold red]{metrics['top_score']}[/bold red] | 最低分 [bold]{metrics['low_score']}[/bold]")
            logger.info(f"入选 {len(top_news)} 条 | 最高分 {metrics['top_score']} | 最低分 {metrics['low_score']}")

            # --- 阶段 4/5 · 深度特稿 ---
            console.print()
            console.print("[bold cyan][4/5][/bold cyan] ✍️  深度特稿")

            # V2/V3 切换
            pipeline_version = config.app_settings.get('writing_pipeline', 'v2')
            console.print(f"  [dim]写作管线: {pipeline_version}[/dim]")

            console.print("  ⏳ AI 选题规划中...")
            if pipeline_version == 'v3':
                from angle_engine_v3 import AngleEngineV3
                engine = AngleEngineV3()
            else:
                engine = AngleEngine()
            topic = engine.select_and_plan(top_news)

            if topic:
                metrics['angle'] = topic.get('selected_angle', '')
                metrics['topic'] = topic.get('title_proposal', '')
                thesis = topic.get('thesis', '')
                if thesis:
                    console.print(f"  [green]✓ 论点:[/green] [bold]{thesis[:60]}...[/bold]")
                console.print(f"  [green]✓ 选题:[/green] [bold]{metrics['topic']}[/bold]")
                console.print(f"  [green]✓ 视角:[/green] [dim]{metrics['angle']}[/dim]")
                logger.info(f"选题: {metrics['topic']} | 视角: {metrics['angle']} | 论点: {thesis[:60]}")

                outline = topic.get('outline', [])
                console.print(f"  ⏳ 写作中 ({len(outline)} 章节)...")
                if pipeline_version == 'v3':
                    from article_writer_v3 import ArticleWriterV3
                    deep_writer = ArticleWriterV3()
                else:
                    from article_writer_v2 import ArticleWriterV2
                    deep_writer = ArticleWriterV2()
                deep_writer.run(topic, top_news)
                metrics['article_generated'] = True
                console.print("  [green]✓ 特稿已生成、归档并发送[/green]")
                logger.info("深度特稿生成与分发完成。")
        else:
            console.print("  [yellow]AI 评分无结果，跳过后续步骤[/yellow]")

        # --- 阶段 5/5 · 周期报告 + 归档 ---
        console.print()
        console.print("[bold cyan][5/5][/bold cyan] 📧 收尾")

        console.print("  ⏳ 检查周期报告...")
        try:
            from periodic_summarizer import PeriodicSummarizer
            summarizer = PeriodicSummarizer()
            summarizer.check_and_run()
            metrics['periodic_report'] = True
        except Exception as es:
            logger.error(f"周期性报告生成失败: {es}")

    except Exception as e:
        console.print(f"[bold red]执行异常:[/bold red] {e}")
        logger.exception(f"执行异常: {e}")

    # --- 归档（无论评分/写作是否成功，都归档原始数据）---
    console.print("  ⏳ 归档数据...")
    archive_files([raw_json, clean_json])
    console.print("  [green]✓ 数据已归档[/green]")

    # --- 运行报告 ---
    elapsed = time.time() - start_time
    elapsed_str = f"{int(elapsed//60)}分{int(elapsed%60)}秒"

    table = Table(title="运行报告", border_style="cyan", show_lines=False, padding=(0, 2))
    table.add_column("项目", style="dim", no_wrap=True)
    table.add_column("结果", style="bold")

    table.add_row("耗时", elapsed_str)
    table.add_row("采集", f"{metrics['raw_count']} 条原始结果")
    if metrics['selected_count']:
        table.add_row("入选", f"{metrics['selected_count']} 条 (最高 {metrics['top_score']}分 / 最低 {metrics['low_score']}分)")
    if metrics['topic']:
        table.add_row("选题", metrics['topic'])
    if metrics['angle']:
        table.add_row("视角", metrics['angle'])
    if metrics['article_generated']:
        table.add_row("特稿", "[green]已生成[/green]")
    table.add_row("归档", "[green]已完成[/green]")

    console.print()
    console.print(table)
    console.print()

    logger.info(f"{brand_emoji} {brand} 流程结束 (耗时 {elapsed_str})")


if __name__ == "__main__":
    main()
