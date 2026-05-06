from __future__ import annotations

import logging
import time

import jwt
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config.settings import settings
from app.infrastructure.redis.client import get_redis

logger = logging.getLogger(__name__)

# 不参与限流的路径前缀
_SKIP_PREFIXES = ("/health", "/metrics", "/docs", "/openapi.json", "/redoc")


def _extract_tenant_id(scope: Scope) -> str | None:
    """
    从 ASGI scope 的请求头中提取 tenant_id。
    优先级：Authorization Bearer JWT → X-Tenant-Id header（非生产环境）
    """
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))

    auth = headers.get(b"authorization", b"").decode()
    if auth.startswith("Bearer "):
        try:
            payload = jwt.decode(
                auth[7:],
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            return payload.get("tenant_id")
        except jwt.InvalidTokenError:
            pass

    if settings.app_env != "production":
        x_tenant = headers.get(b"x-tenant-id", b"").decode().strip()
        if x_tenant:
            return x_tenant

    return None


class RateLimitMiddleware:
    """
    纯 ASGI 限流中间件。

    不继承 BaseHTTPMiddleware，避免其对 StreamingResponse / SSE 的响应体缓冲问题。
    算法：滑动窗口计数器（当前分钟桶 + 前一分钟桶加权）。
    Redis 不可用时 fail open——放行请求，记录 warning，不中断业务。
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._limit = settings.rate_limit_per_minute
        self._enabled = settings.rate_limit_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 非 HTTP 请求（WebSocket 等）直接透传
        if not self._enabled or scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # OPTIONS 预检请求和白名单路径跳过限流
        path: str = scope.get("path", "")
        method: str = scope.get("method", "")
        if method == "OPTIONS" or any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self._app(scope, receive, send)
            return

        tenant_id = _extract_tenant_id(scope)
        if not tenant_id:
            # 无法识别租户，跳过限流，由路由层统一处理 401
            await self._app(scope, receive, send)
            return

        # ── 滑动窗口计数 ────────────────────────────────────────────────────────
        now_ts = int(time.time())
        cur_bucket = now_ts // 60
        prev_bucket = cur_bucket - 1
        elapsed = now_ts % 60

        cur_key = f"rate:{tenant_id}:{cur_bucket}"
        prev_key = f"rate:{tenant_id}:{prev_bucket}"

        try:
            redis = get_redis()
            cur_count: int = await redis.incr(cur_key)
            if cur_count == 1:
                # 新桶首次写入，设 TTL 为两个窗口，保证前一桶数据可读
                await redis.expire(cur_key, 120)
            prev_raw = await redis.get(prev_key)
            prev_count = int(prev_raw) if prev_raw else 0
        except Exception:
            logger.warning(
                "Rate limit: Redis unavailable, failing open for tenant=%s", tenant_id
            )
            await self._app(scope, receive, send)
            return

        # 前一桶权重随当前窗口推进线性衰减
        weight = 1.0 - elapsed / 60.0
        estimated = cur_count + prev_count * weight

        # ── 超限：直接返回 429，不进入路由层 ───────────────────────────────────
        if estimated > self._limit:
            retry_after = 60 - elapsed
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "detail": f"{self._limit} requests per minute limit reached",
                },
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        # ── 放行：在响应头注入限流状态 ─────────────────────────────────────────
        remaining = max(0, int(self._limit - estimated))
        reset_ts = (cur_bucket + 1) * 60  # 当前分钟桶到期的 Unix 时间戳

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                mutable.append("X-RateLimit-Limit", str(self._limit))
                mutable.append("X-RateLimit-Remaining", str(remaining))
                mutable.append("X-RateLimit-Reset", str(reset_ts))
            await send(message)

        await self._app(scope, receive, send_with_headers)
