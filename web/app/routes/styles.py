"""风格库 API"""
import json
import os
from fastapi import APIRouter

router = APIRouter()

_ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_STYLES_FILE = os.path.join(_ENGINE_DIR, "styles_hardtech.json")


@router.get("/styles")
async def list_styles():
    """获取所有可用风格"""
    if not os.path.exists(_STYLES_FILE):
        return {"styles": {}}
    try:
        with open(_STYLES_FILE, 'r', encoding='utf-8') as f:
            styles = json.load(f)
        # 返回精简版（不含 dna_sample，太大）
        result = {}
        for k, v in styles.items():
            result[k] = {
                "name": v.get("name", ""),
                "persona": v.get("persona", ""),
                "structure": v.get("structure", ""),
            }
        return {"styles": result}
    except Exception as e:
        return {"styles": {}, "error": str(e)}
