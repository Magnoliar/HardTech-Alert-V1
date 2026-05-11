import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import html
import re
import time
from datetime import datetime
import logging
from config_loader import load_config
from domain_config import DOMAIN

logger = logging.getLogger(__name__)

def markdown_to_html(text):
    if not text: return ""
    html_text = html.escape(text)
    html_text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html_text)
    html_text = html_text.replace('\n', '<br>')
    return html_text

def markdown_to_html_robust(md_text):
    if not md_text: return ""
    md_text = md_text.replace('\\n', '\n').replace('\\"', '"')
    start_match = re.search(r'[0-9#\u4e00-\u9fa5]', md_text)
    if start_match: md_text = md_text[start_match.start():]
    md_text = md_text.strip().strip('[]"\' \n')
    md_text = re.sub(r'\n{3,}', '\n\n', md_text)

    lines = md_text.split('\n')
    html_output = []
    in_list = False

    for line in lines:
        line = line.strip()
        if not line:
            if in_list: html_output.append("</ul>"); in_list = False
            continue
        if line.startswith('# '):
            html_output.append(f'<h1 style="font-size:30px; font-weight:900; color:#000; border-bottom:3px solid #1a237e; padding-bottom:10px; margin:45px 0 25px;">{html.escape(line[2:])}</h1>')
        elif line.startswith('## '):
            html_output.append(f'<h2 style="font-size:24px; font-weight:800; color:#111; margin:40px 0 20px;">{html.escape(line[3:])}</h2>')
        elif line.startswith('> '):
            html_output.append(f'<div style="background:#f0f4ff; border-left:6px solid #1a237e; padding:20px; margin:25px 0; color:#444; font-style:italic; font-size:1.05em;">{html.escape(line[2:])}</div>')
        elif line.startswith('- ') or line.startswith('* '):
            if not in_list: html_output.append('<ul style="padding-left:25px;">'); in_list = True
            content = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html.escape(line[2:]))
            html_output.append(f'<li style="margin-bottom:12px; line-height:1.7;">{content}</li>')
        else:
            if in_list: html_output.append("</ul>"); in_list = False
            processed = re.sub(r'\*\*(.*?)\*\*', r'<strong style="color:#000; font-weight:800;">\1</strong>', html.escape(line))
            html_output.append(f'<p style="margin-bottom:25px; line-height:1.9; text-align:justify; color:#222; font-size:1.05em;">{processed}</p>')

    if in_list: html_output.append("</ul>")
    return "".join(html_output)

def send_email(subject, html_body, receiver=None):
    if not html_body: return False
    config = load_config().email_ai
    target_receiver = receiver if receiver else config.get('receiver')
    if not target_receiver:
        logger.error("未指定接收人，发送终止。")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{DOMAIN['brand']} <{config.get('sender')}>"
    msg["To"] = target_receiver
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for attempt in range(3):
        try:
            port = config.get('port')
            if port == 587:
                server = smtplib.SMTP(config.get('host'), port, timeout=30)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(config.get('host'), port, timeout=30)
            server.login(config.get('sender'), config.get('password'))
            server.sendmail(config.get('sender'), [addr.strip() for addr in target_receiver.split(',')], msg.as_string())
            server.quit()
            logger.info(f"邮件发送成功: {subject} -> {target_receiver}")
            return True
        except Exception as e:
            logger.warning(f"邮件发送尝试 {attempt+1} 失败: {e}")
            if attempt < 2: time.sleep(10)

    logger.error(f"邮件最终发送失败: {subject}")
    return False


def build_html_email(data_by_category, summary):
    """构建 HardTech Insight 品牌的 HTML 简报邮件"""
    brand = DOMAIN["brand"]
    brand_emoji = DOMAIN["brand_emoji"]

    cover_story = None
    all_news = []
    for cat, items in data_by_category.items():
        all_news.extend(items)
    if all_news:
        all_news.sort(key=lambda x: x['parsed'].get('score', 0), reverse=True)
        cover_story = all_news[0]

    css = """
    <style>
        :root { --primary: #1a237e; --accent: #f57c00; --bg: #f9f9f9; --card: #ffffff; --text: #333; --meta: #666; --border: #eee; }
        @media (prefers-color-scheme: dark) {
            :root { --primary: #7986cb; --accent: #ffb74d; --bg: #121212; --card: #1e1e1e; --text: #e0e0e0; --meta: #aaa; --border: #333; }
        }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: var(--text); background: var(--bg); margin: 0; padding: 0; }
        .container { max-width: 800px; margin: 20px auto; background: var(--card); padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); }
        .header { text-align: center; margin-bottom: 40px; border-bottom: 2px solid var(--border); padding-bottom: 20px; }
        .header h1 { color: var(--text); margin: 0; font-size: 28px; letter-spacing: -0.5px; }
        .date { color: var(--meta); font-size: 14px; margin-top: 10px; }
        .cover { background: linear-gradient(135deg, #1a237e 0%, #283593 100%); color: #fff; padding: 30px; border-radius: 12px; margin-bottom: 40px; }
        .cover-label { text-transform: uppercase; font-size: 12px; font-weight: bold; letter-spacing: 1px; opacity: 0.8; }
        .cover-title { font-size: 24px; font-weight: 700; margin: 10px 0 15px; color: #fff; text-decoration: none; display: block; }
        .cover-summary { font-size: 16px; opacity: 0.95; }
        .summary-box { background: rgba(26, 35, 126, 0.05); border-left: 5px solid var(--primary); padding: 20px; margin-bottom: 40px; border-radius: 8px; }
        .summary-box h2 { color: var(--primary); margin-top: 0; font-size: 20px; }
        .cat-section { margin-bottom: 30px; }
        .cat-title { font-size: 22px; color: var(--text); border-bottom: 2px solid var(--border); padding-bottom: 10px; margin-bottom: 20px; }
        .cat-badge { background: var(--border); color: var(--meta); font-size: 14px; padding: 2px 10px; border-radius: 12px; margin-left: 10px; }
        .news-item { margin-bottom: 25px; padding-bottom: 20px; border-bottom: 1px solid var(--border); }
        .score-badge { display: inline-flex; font-weight: bold; font-size: 12px; padding: 2px 8px; border-radius: 4px; color: #fff; margin-right: 10px; }
        .s90 { background: #d32f2f; } .s75 { background: #f57c00; } .s60 { background: #1976d2; } .slow { background: #757575; }
        .news-title { font-size: 18px; font-weight: 600; color: var(--text); text-decoration: none; }
        .news-summary { font-size: 15px; color: var(--text); text-align: justify; margin: 8px 0; }
        .tag-pill { display: inline-block; background: var(--border); color: var(--meta); font-size: 11px; padding: 2px 8px; border-radius: 12px; margin-right: 6px; }
        .reasoning { margin-top: 8px; font-size: 13px; color: var(--meta); font-style: italic; border-left: 3px solid var(--border); padding-left: 10px; }
        .footer { text-align: center; margin-top: 50px; color: var(--meta); font-size: 12px; border-top: 1px solid var(--border); padding-top: 20px; }
        @media only screen and (max-width: 600px) { .container { padding: 20px; width: 100% !important; box-sizing: border-box; } }
    </style>
    """

    parts = []
    parts.append(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{brand}</title>{css}</head><body>")
    parts.append("<div class='container'>")

    current_date = datetime.now().strftime("%Y年%m月%d日")
    parts.append(f"<div class='header'><h1>{brand_emoji} {brand} 深度精选</h1><div class='date'>{current_date} | 聚焦硬科技产业创投</div></div>")

    if cover_story:
        p = cover_story['parsed']
        cover_url = html.escape(cover_story.get('url', '#'), quote=True)
        cover_title = html.escape(p.get('title', ''))
        cover_summary = html.escape(p.get('summary', ''))
        cover_reasoning = html.escape(p.get('reasoning', '')) if p.get('reasoning') else ''
        reasoning_html = f"<div class='reasoning'>💡 {cover_reasoning}</div>" if cover_reasoning else ''
        parts.append(f"<div class='cover'><span class='cover-label'>★ 今日头条 ({p.get('score')}分)</span><a href='{cover_url}' target='_blank' class='cover-title'>{cover_title}</a><div class='cover-summary'>{cover_summary}</div>{reasoning_html}</div>")

    if summary:
        parts.append(f"<div class='summary-box'><h2>💡 主理人洞察</h2><div>{markdown_to_html(summary)}</div></div>")

    categories = DOMAIN["categories"]
    existing_cats = list(data_by_category.keys())
    sorted_cats = sorted(existing_cats, key=lambda x: categories.index(x) if x in categories else 999)

    for cat in sorted_cats:
        items = data_by_category[cat]
        if not items: continue
        parts.append(f"<div class='cat-section'><div class='cat-title'>{html.escape(cat)}<span class='cat-badge'>{len(items)}</span></div>")
        for item in items:
            p = item['parsed']; s = p.get('score', 0)
            url = html.escape(item.get('url', '#'), quote=True)
            title = html.escape(p.get('title', ''))
            summary = html.escape(p.get('summary', ''))
            sc = "s90" if s >= 90 else ("s75" if s >= 75 else ("s60" if s >= 60 else "slow"))
            parts.append(f"<div class='news-item'><div><span class='score-badge {sc}'>{s}</span><a href='{url}' target='_blank' class='news-title'>{title}</a></div><div class='news-summary'>{summary}</div>")
            if p.get('reasoning') and s >= 75:
                reasoning = html.escape(p['reasoning'])
                parts.append(f"<div class='reasoning'>💡 {reasoning}</div>")
            all_tags = list(set(p.get('tags', []) + p.get('keywords', [])))
            if all_tags:
                parts.append("<div style='margin-top:8px;'>")
                for t in all_tags[:5]: parts.append(f"<span class='tag-pill'>{html.escape(t)}</span>")
                parts.append("</div>")
            parts.append("</div>")
        parts.append("</div>")

    parts.append(f"<div class='footer'><p>Powered by Multi-Source Intelligence | {brand}</p></div>")
    parts.append("</div></body></html>")
    return "".join(parts)
