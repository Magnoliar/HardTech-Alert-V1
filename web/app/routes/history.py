"""历史记录 API"""
import json
import os
from fastapi import APIRouter

router = APIRouter()

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_HISTORY_FILE = os.path.join(_DATA_DIR, "history.json")


def _load_history():
    if not os.path.exists(_HISTORY_FILE):
        return []
    try:
        with open(_HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


@router.get("/history")
async def list_history():
    """获取历史记录列表（不含文章全文）"""
    history = _load_history()
    result = []
    for item in history:
        result.append({
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "date": item.get("date", ""),
            "chars": item.get("chars", 0),
        })
    return {"history": result}


@router.get("/history/{item_id}")
async def get_history_item(item_id: str):
    """获取某篇历史文章"""
    history = _load_history()
    for item in history:
        if item.get("id") == item_id:
            return {"item": item}
    return {"error": "未找到"}
