# ADR-042: 用量追踪 — EventBus 捕获、写时计费、Redis 缓存限额

**状态**: 已采纳  
**日期**: 2026-05-08  
**决策人**: 开发团队

---

## 背景

Phase 12 目标：记录每次 LLM 调用的 token 消耗与费用，支持日费用限额执行，为 Admin UI 提供用量查询数据。需要决定：

1. token usage 如何从 LLMProvider 传出（不破坏现有接口）
2. 费用在哪里计算（写入时 vs 查询时）
3. QuotaGuard 如何获取今日费用（避免每请求查 DB）
4. Python 与 Java 控制面的职责划分

---

## 备选方案

### 1. token 捕获方式

| 维度 | 修改返回类型 | callback 注入 | EventBus 发布 |
|------|------------|--------------|--------------|
| 接口破坏性 | 高（返回类型变） | 低（可选参数） | 无 |
| 架构合规 | ✅ | ✅ | ✅ |
| 与现有基础设施一致性 | 无关 | 无关 | 复用 EventBus + TOKEN_CONSUMED |
| 未来扩展 | 改返回类型 | 改参数 | 新增订阅者 |

### 2. 费用计算时机

| 维度 | 写入时计算 | 查询时 JOIN |
|------|-----------|------------|
| 历史准确性 | ✅ 反映调用时单价 | ❌ 单价变动后历史失真 |
| 查询性能 | 快（直接 SUM） | 慢（每次 JOIN） |

### 3. 今日费用获取

| 维度 | 每次查 DB | Redis 缓存（1 分钟 TTL） |
|------|----------|------------------------|
| 精度 | 精确 | 近似（最多 1 分钟延迟） |
| DB 压力 | 高（每请求一次） | 低 |
| 并发超限风险 | 有（PRE_PIPELINE 检查，POST 写入） | 同样存在，接受最终一致性 |

---

## 决策

### 1. 使用 EventBus 发布 TOKEN_CONSUMED 事件

`events.py` 中已定义 `TOKEN_CONSUMED` 事件类型和完整的 `EventBus` 实现，本次激活。

Provider 内部调用 `event_bus.publish()`（providers → infrastructure，符合层级），`LLMProvider` 接口签名完全不变。`usage_service.register_handlers()` 在应用启动时注册订阅者，DB 写入和 Prometheus 更新集中在同一处理函数中。

### 2. 写入时计算 cost_usd（历史快照）

`usage_records.cost_usd` 在写入时查询 `model_configs` 当时单价计算并存储。模型单价变动不影响历史记录，查询时直接 `SUM(cost_usd)` 无需 JOIN。

### 3. Redis 缓存今日费用，接受最终一致性

`QuotaGuard` 通过 `get_today_spend_cached()` 读取 Redis（TTL 60s），未命中时回查 DB 并回填。接受 1 分钟内的费用近似值，不引入分布式锁，费用超限后可能有少量溢出，对当前业务场景可接受。

### 4. Python 先建后拆，预留 Java 接管路径

`usage_records` / `model_configs` 表、费用计算逻辑、限额执行当前全部在 Python 实现。等 Java 控制面 `arc-tenant` 就绪时，Python 侧的计费/限额逻辑下线，改为调用 Java API；历史数据迁移至 Java 管理的数据库。

---

## 后果

**正面**：
- LLMProvider 接口零变动，Ollama / OpenAI / ResilientLLM 全部不受影响
- EventBus 订阅者模式：未来新增用量告警只需加一个订阅函数
- 历史费用数据准确，不受单价变动影响
- QuotaGuard DB 压力极低，Redis 缓存命中率高

**负面**：
- 费用限额非精确执行，高并发下可能小幅超出上限（最终一致性）
- Python 侧计费逻辑在 Java 接管后需要下线，存在短期重复建设
- Ollama token 统计依赖其 API 返回的 `eval_count`，精度低于 OpenAI

---

## 参考

- `app/pipeline/core/events.py` — EventBus + TOKEN_CONSUMED 定义
- `app/services/usage_service.py` — 订阅者实现
- `app/providers/llm/openai_llm.py` — usage 发布（stream_options 方案）
- `app/providers/llm/ollama_llm.py` — usage 发布（done chunk 方案）
- `app/infrastructure/postgres/repositories/usage_repo.py`
- `app/infrastructure/postgres/repositories/model_config_repo.py`
- Phase 14+ Java 控制面：arc-tenant 负责正式接管计费与限额
