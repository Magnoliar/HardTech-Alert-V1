"""SSE 日志广播器 — 将写作管线的日志实时推送到前端"""
import logging
import queue
import json
import time
from typing import Optional


class SSELogHandler(logging.Handler):
    """自定义 logging handler，将日志写入 Queue"""

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            # 映射到简洁标签
            level_map = {"info": "info", "warning": "warn", "error": "error", "debug": "debug"}
            self.log_queue.put({
                "type": "log",
                "msg": msg,
                "level": level_map.get(level, "info"),
                "ts": time.strftime("%H:%M:%S"),
            })
        except Exception:
            pass


class TaskLogBuffer:
    """每个写作任务的日志缓冲"""

    def __init__(self):
        self.queue: queue.Queue = queue.Queue()
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.done = False

    def put_log(self, msg: str, level: str = "info"):
        self.queue.put({
            "type": "log",
            "msg": msg,
            "level": level,
            "ts": time.strftime("%H:%M:%S"),
        })

    def put_done(self, result: dict):
        self.result = result
        self.done = True
        self.queue.put({"type": "done", "data": result})

    def put_error(self, error: str):
        self.error = error
        self.done = True
        self.queue.put({"type": "error", "msg": error})


# 全局任务注册表
_tasks: dict[str, TaskLogBuffer] = {}


def create_task(task_id: str) -> TaskLogBuffer:
    buf = TaskLogBuffer()
    _tasks[task_id] = buf
    return buf


def get_task(task_id: str) -> Optional[TaskLogBuffer]:
    return _tasks.get(task_id)


def cleanup_task(task_id: str):
    _tasks.pop(task_id, None)
