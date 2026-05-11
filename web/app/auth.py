"""Access Key 认证中间件"""
import os
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

ACCESS_KEY = os.getenv("ACCESS_KEY", "")
COOKIE_NAME = "hw_access_key"

# 不需要认证的路径
PUBLIC_PATHS = {"/", "/api/auth", "/favicon.ico"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 静态文件和公开路径不需要认证
        path = request.url.path
        if path.startswith("/static/") or path in PUBLIC_PATHS:
            return await call_next(request)

        # 检查 cookie
        key = request.cookies.get(COOKIE_NAME, "")
        if not ACCESS_KEY or key == ACCESS_KEY:
            return await call_next(request)

        return JSONResponse(status_code=401, content={"detail": "未授权，请输入访问密钥"})


async def auth_endpoint(request: Request):
    """POST /api/auth — 验证 Access Key"""
    body = await request.json()
    key = body.get("key", "")

    if not ACCESS_KEY:
        return JSONResponse(content={"ok": True, "msg": "未配置 ACCESS_KEY，跳过认证"})

    if key != ACCESS_KEY:
        raise HTTPException(status_code=401, detail="密钥错误")

    resp = JSONResponse(content={"ok": True})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=key,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,  # 7 天
    )
    return resp
