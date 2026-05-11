import os
import re
import logging
from datetime import datetime, timedelta
from llm_client import call_ai_api
from email_generator import send_email, markdown_to_html_robust
from domain_config import DOMAIN

logger = logging.getLogger(__name__)

KB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_base")

class PeriodicSummarizer:
    """长周期洞察专家：生成半月谈/月谈报告"""

    def __init__(self):
        self.today = datetime.now()
        self.brand = DOMAIN["brand"]

    def check_and_run(self):
        day = self.today.day
        is_last_day = (self.today + timedelta(days=1)).month != self.today.month

        if day == 15:
            self.run(period_name="半月谈", days_back=15)
        elif is_last_day:
            self.run(period_name="月谈", days_back=30)
        else:
            logger.info("今天不是周期报告生成日，跳过。")

    def run(self, period_name="月谈", days_back=30):
        logger.info(f"📊 正在筹备《{self.brand} {period_name}》核心洞察报告...")
        summary_data = self._collect_history(days_back)
        if not summary_data:
            logger.warning("历史数据不足，无法生成周期报告。")
            return
        report_content = self._generate_report(period_name, summary_data)
        if not report_content:
            logger.error(f"《{self.brand} {period_name}》报告生成失败，AI 返回空内容，跳过发送。")
            return
        self._send_report_email(period_name, report_content)

    def _collect_history(self, days_back):
        history_content = []
        start_date = self.today - timedelta(days=days_back)
        for root, _, files in os.walk(KB_ROOT):
            for file in files:
                if file.endswith(".md"):
                    try:
                        if not re.match(r'^\d{4}-\d{2}-\d{2}', file):
                            continue
                        file_date_str = file[:10]
                        file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                        if file_date >= start_date:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                history_content.append(f.read()[:500])
                    except Exception: continue
        return "\n\n".join(history_content)

    def _generate_report(self, period_name, data):
        system_prompt = f"你是一位顶尖的硬科技产业智库分析师。撰写一份《{self.brand} {period_name}》深度综述,聚焦半导体、AI、智能终端、机器人、能源等领域的趋势演变。"
        user_prompt = f"以下是近期深度报道摘要：\n\n{data}\n\n请输出综述报告。"
        return call_ai_api([{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], description=f"Periodic {period_name}")

    def _send_report_email(self, period_name, content):
        html_body = f"""
        <div style="font-family: -apple-system, sans-serif; max-width: 800px; margin: auto; padding: 40px; color: #2c3e50;">
            <h1 style="color: #1a237e; border-bottom: 2px solid #1a237e; padding-bottom: 10px;">📊 {self.brand} {period_name} | 结构化洞察报告</h1>
            <p style="color: #7f8c8d;">生成日期：{self.today.strftime('%Y-%m-%d')}</p>
            <div style="line-height: 1.8;">{markdown_to_html_robust(content)}</div>
            <hr style="margin: 40px 0; border: 0; border-top: 1px solid #eee;">
            <p style="text-align: center; color: #bdc3c7; font-size: 12px;">此报告由 PeriodicSummarizer 自动生成。</p>
        </div>
        """
        subject = f"📊 重磅特刊 | {self.brand} {period_name}：底层逻辑与未来趋势"
        if send_email(subject, html_body):
            logger.info(f"📧 《{self.brand} {period_name}》已发送。")

if __name__ == "__main__": pass
