"""邮件发送 API"""
from fastapi import APIRouter
from app.models import EmailRequest

router = APIRouter()


@router.post("/write/{task_id}/email")
async def send_article_email(task_id: str, req: EmailRequest):
    """发送文章邮件"""
    from app.sse import get_task
    from email_generator import send_email
    from article_renderer import render_deep_article_to_html
    from datetime import datetime

    log_buf = get_task(task_id)
    if not log_buf or not log_buf.done or log_buf.error:
        return {"error": "文章不存在或尚未完成"}

    result = log_buf.result
    article = result.get("article", "")
    title = result.get("topic", {}).get("title", "未命名")

    today_str = datetime.now().strftime("%Y-%m-%d")
    html = render_deep_article_to_html(article, title, "V3 Web", today_str)
    subject = req.subject or f"📰 [深度文章] {today_str} | {title}"

    ok = send_email(subject, html, receiver=req.recipient)
    if ok:
        return {"ok": True, "msg": f"已发送至 {req.recipient}"}
    return {"ok": False, "msg": "邮件发送失败，请检查邮件配置"}
