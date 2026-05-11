"""写作 API 路由"""
import uuid
import json
import asyncio
import threading
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models import WriteRequest, TaskStatus
from app.sse import create_task, get_task
from app.pipeline import run_writing_pipeline

router = APIRouter()


@router.post("/write")
async def start_writing(req: WriteRequest):
    """启动写作任务"""
    # 验证至少有一项输入
    has_input = any([req.keywords, req.outline, req.ideas, req.links])
    if not has_input:
        return {"error": "至少填写一项输入"}

    task_id = uuid.uuid4().hex[:12]
    log_buf = create_task(task_id)

    # 后台线程运行
    thread = threading.Thread(
        target=run_writing_pipeline,
        args=(req, log_buf),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}


@router.get("/write/{task_id}/logs")
async def stream_logs(task_id: str):
    """SSE 实时日志流"""
    log_buf = get_task(task_id)
    if not log_buf:
        return {"error": "任务不存在"}

    async def event_generator():
        while True:
            try:
                # 非阻塞读取，超时 0.5s
                msg = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: log_buf.queue.get(timeout=0.5)
                )
                event_type = msg.get("type", "log")
                data = json.dumps(msg, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"

                if event_type in ("done", "error"):
                    break
            except Exception:
                # 超时，检查是否已完成
                if log_buf.done:
                    break
                # 发送心跳
                yield ": heartbeat\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/write/{task_id}/result")
async def get_result(task_id: str):
    """获取成文结果"""
    log_buf = get_task(task_id)
    if not log_buf:
        return {"error": "任务不存在"}
    if not log_buf.done:
        return {"status": "running"}
    if log_buf.error:
        return {"status": "error", "error": log_buf.error}
    return {"status": "done", **log_buf.result}


@router.put("/write/{task_id}/edit")
async def save_edit(task_id: str, body: dict):
    """保存编辑后的文章"""
    log_buf = get_task(task_id)
    article = body.get("article", "")
    if log_buf and log_buf.result:
        log_buf.result["article"] = article
    # 同步更新历史记录
    _update_history_article(task_id, article)
    return {"ok": True}


def _update_history_article(task_id: str, article: str):
    """更新历史记录中的文章内容"""
    import json
    import os
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    history_file = os.path.join(data_dir, "history.json")
    if not os.path.exists(history_file):
        return
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
        for item in history:
            if item.get("id") == task_id:
                item["article"] = article
                item["chars"] = len(article)
                break
        tmp = history_file + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp, history_file)
    except Exception:
        pass
