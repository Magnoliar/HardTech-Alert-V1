"""Pydantic 请求/响应模型"""
from typing import Optional
from pydantic import BaseModel


class ApiConfig(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class WriteRequest(BaseModel):
    # 素材输入（至少填一项）
    keywords: Optional[str] = None
    outline: Optional[list[str]] = None  # 每行一个方向
    ideas: Optional[str] = None
    links: Optional[list[str]] = None

    # 文风参考
    style_mode: str = "existing"  # existing | text | link
    style_id: Optional[str] = None
    style_text: Optional[str] = None
    style_link: Optional[str] = None

    # 模型覆盖
    model_override: Optional[str] = None
    api_config: Optional[ApiConfig] = None

    # 邮件
    email_recipient: Optional[str] = None


class WriteResult(BaseModel):
    article: str
    stats: dict
    sources: list[dict]
    topic: dict


class TaskStatus(BaseModel):
    task_id: str
    status: str  # running | done | error
    article: Optional[str] = None
    stats: Optional[dict] = None
    sources: Optional[list[dict]] = None
    topic: Optional[dict] = None
    error: Optional[str] = None


class EmailRequest(BaseModel):
    recipient: str
    subject: Optional[str] = None


class HistoryItem(BaseModel):
    id: str
    title: str
    date: str
    chars: int
