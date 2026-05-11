"""HardTech Writer Web — FastAPI 入口"""
import sys
import os

# 将上层目录加入 sys.path，以便 import 原有模块
_WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_WEB_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 在 import 原有模块之前，先 monkey-patch config_loader
from app.config import patch_config_loader
patch_config_loader()

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from app.auth import AuthMiddleware, auth_endpoint
from app.routes import writer, styles, email, history

app = FastAPI(title="HardTech Writer", version="1.0.0")

# 认证中间件
app.add_middleware(AuthMiddleware)

# 静态文件
_STATIC_DIR = os.path.join(_WEB_DIR, "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# 路由
app.add_api_route("/api/auth", auth_endpoint, methods=["POST"])
app.include_router(writer.router, prefix="/api")
app.include_router(styles.router, prefix="/api")
app.include_router(email.router, prefix="/api")
app.include_router(history.router, prefix="/api")


@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = os.path.join(_STATIC_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
