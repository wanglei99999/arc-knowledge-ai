# ADR-040: 限流中间件 — 滑动窗口计数器 + 纯 ASGI 实现

**状态**: 已采纳  
**日期**: 2026-05-06  
**决策人**: 开发团队

---

## 背景

Phase 11a 目标：对 `/chat` 等 LLM 调用接口按租户限流，防止滥用并控制成本。需要决定：

1. 限流算法（Fixed Window / Sliding Window / Token Bucket）
2. 中间件实现方式（纯 ASGI / `BaseHTTPMiddleware`）

---

## 备选方案

### 限流算法

| 维度 | Fixed Window | Sliding Window Counter | Token Bucket |
|------|-------------|------------------------|--------------|
| 实现复杂度 | 低 | 低 | 中 |
| 边界突发风险 | 高（2× 流量） | 低（< 5% 误差） | 无 |
| Redis 操作数 | 1 | 2 | 3+ |
| 精度 | 粗 | 近似 | 精确 |

### 中间件实现

| 维度 | 纯 ASGI | BaseHTTPMiddleware |
|------|---------|-------------------|
| SSE 流式兼容 | ✅ 完全兼容 | ❌ 缓冲全部 chunks，破坏 SSE |
| 响应头注入 | 包装 `send` 回调 | 内置支持 |
| 复杂度 | 低 | 极低 |

---

## 决策

**限流算法**：选择 **滑动窗口计数器**。

两个 Redis 分钟桶 + 线性权重估算，消除 Fixed Window 的边界突发问题，误差 < 5%，实现成本与 Fixed Window 相当。

```
estimated = cur_count + prev_count × (1 - elapsed / 60)
```

**中间件实现**：选择**纯 ASGI**。

项目核心接口 `/chat` 使用 SSE 流式输出，`BaseHTTPMiddleware.call_next()` 会缓冲全部响应 chunks，直接破坏流式推送。纯 ASGI 通过包装 `send` 回调注入响应头，完全兼容流式。

---

## 后果

**正面**：
- SSE 流式响应不受影响
- 响应头 `X-RateLimit-*` 在正常放行时透传
- Redis 不可用时 fail open（记录警告，不影响服务）

**负面**：
- 限额为全局统一值，尚不支持按租户差异化配额（可在 `TenantConfig` 扩展 `rate_limit_per_minute` 字段）
- 近似算法，极端流量模式下误差 < 5%

---

## 参考

- `app/api/middleware/rate_limit.py`
- `docs/tech-notes/rate-limiting.md`：Redis 滑动窗口算法详解
- `docs/tech-notes/asgi-middleware.md`：纯 ASGI vs BaseHTTPMiddleware 原理对比
