import re
import html
from domain_config import DOMAIN

def render_deep_article_to_html(md_text, title, style_name, date_str):
    """长文渲染引擎 — 品牌适配为 HardTech Insight"""
    if not md_text: return ""

    text = md_text.replace('\\n', '\n').replace('\\"', '"')
    text = text.strip().strip(r'[]"\{}').strip()

    start_match = re.search(r'[0-9#\u4e00-\u9fa5]', text)
    if start_match: text = text[start_match.start():]
    text = re.sub(r'\n{2,}', '\n\n', text)

    lines = text.split('\n')
    html_output = []
    in_list = False

    for line in lines:
        line = line.strip()
        if not line:
            if in_list: html_output.append("</ul>"); in_list = False
            continue
        if '![' in line and '](' in line:
            img_match = re.search(r'!\[(.*?)\]\((.*?)\)', line)
            if img_match:
                alt, url = html.escape(img_match.group(1), quote=True), html.escape(img_match.group(2), quote=True)
                html_output.append(f'<div style="text-align:center; margin:45px 0;"><img src="{url}" alt="{alt}" style="max-width:100%; border-radius:12px; box-shadow:0 10px 30px rgba(0,0,0,0.15);"><p style="font-size:13px; color:#888; margin-top:12px;">{alt}</p></div>')
                continue
        if line.startswith('# '):
            html_output.append(f'<h1 style="font-size:32px; font-weight:900; color:#000; border-bottom:4px solid #1a237e; padding-bottom:10px; margin:50px 0 25px;">{html.escape(line[2:])}</h1>')
        elif line.startswith('## '):
            html_output.append(f'<h2 style="font-size:24px; font-weight:800; color:#111; margin:40px 0 20px; border-bottom:1px solid #ddd; padding-bottom:5px;">{html.escape(line[3:])}</h2>')
        elif line.startswith('> '):
            html_output.append(f'<div style="background:#f0f4ff; border-left:6px solid #1a237e; padding:20px; margin:25px 0; color:#444; font-size:1.05em; font-style:italic;">{html.escape(line[2:])}</div>')
        elif line.startswith('- ') or line.startswith('* '):
            if not in_list: html_output.append('<ul style="padding-left:25px; margin-bottom:25px;">'); in_list = True
            html_output.append(f'<li style="margin-bottom:12px; line-height:1.7; color:#333;">{html.escape(line[2:])}</li>')
        else:
            if in_list: html_output.append("</ul>"); in_list = False
            processed_line = re.sub(r'\*\*(.*?)\*\*', r'<strong style="color:#000;">\1</strong>', html.escape(line))
            html_output.append(f'<p style="margin-bottom:28px; line-height:1.9; text-align:justify; color:#222; font-size:17px;">{processed_line}</p>')

    if in_list: html_output.append("</ul>")
    full_body = "".join(html_output)

    safe_title = html.escape(title) if title else ''
    return f"""
    <html>
    <head><title>{safe_title} | {DOMAIN['brand']}</title></head>
    <body style="line-height:1.9; max-width:750px; margin:0 auto; padding:40px; background:#fff; color:#111; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
        <div style="border-bottom:4px solid #1a237e; padding-bottom:10px; margin-bottom:40px;">
            <p style="color:#666; font-style:italic; margin:0;">{DOMAIN['brand_emoji']} {DOMAIN['brand']} · 深度特刊 · {date_str}</p>
            <p style="color:#1a237e; font-weight:bold; margin:5px 0 0;">今日主笔：{html.escape(style_name)}</p>
        </div>
        {full_body}
        <div style="margin-top:100px; text-align:center; color:#999; font-size:12px; border-top:1px solid #eee; padding-top:20px;">
            本文由 AI 执行编辑自动生成,基于当日多源采集与定向深度调研。
        </div>
    </body>
    </html>
    """
