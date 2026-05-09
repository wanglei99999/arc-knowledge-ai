# ADR-043: 可观测性基础设施 — Prometheus + Grafana 选型

**状态**: 已采纳  
**日期**: 2026-05-08  
**决策人**: 开发团队

---

## 背景

Phase 13 目标：为已有的 7 个 Prometheus metrics（`arc_pipeline_total`、`arc_stage_duration_seconds`、`arc_llm_tokens_total`、`arc_llm_cost_usd_total` 等）提供可视化看板，支持运营和排查。`/metrics` 端点已实现，但 docker-compose 中没有 Prometheus 和 Grafana 服务，数据无法被收集和展示。

需要决定：
1. 监控采集组件选型
2. 可视化组件选型
3. 看板如何持久化和版本控制

---

## 备选方案

| 维度 | Prometheus + Grafana | Datadog / New Relic | Victoria Metrics + Grafana |
|------|---------------------|---------------------|---------------------------|
| 成本 | 开源免费 | 按数据量收费，MVP 阶段贵 | 开源免费，资源占用更低 |
| 本地部署 | docker-compose 一键 | 需要 Agent 和外网 | docker-compose 一键 |
| 生态兼容 | prometheus_client 已集成 | 需迁移 SDK | 兼容 Prometheus 协议 |
| 看板版本控制 | JSON provisioning | UI 导出、无原生 Git 集成 | 同 Grafana |
| 学习曲线 | 成熟、社区资源丰富 | 低（SaaS） | 较小众 |

---

## 决策

选择 **Prometheus + Grafana**。

`prometheus_client` 已集成且 `/metrics` 端点已可用，选用 Prometheus 零迁移成本。Grafana provisioning 机制（`/etc/grafana/provisioning/`）支持将看板 JSON 纳入 Git 版本控制，避免手动导入。MVP 阶段全程本地部署，无外部依赖。

---

## 实现要点

- `prom/prometheus:v2.52.0` + `grafana/grafana:10.4.2` 加入 `docker-compose.yml`
- `monitoring/prometheus/prometheus.yml`：scrape `host.docker.internal:8000/metrics`（Docker Desktop 环境；Linux 需改为宿主 IP）
- `monitoring/grafana/provisioning/`：自动注册数据源 + 加载 Dashboard JSON
- `arc-overview.json`：6 Panel 看板（Pipeline 成功率、Stage P95 延迟、LLM Token 速率、费用累计、语义缓存命中率、缓存速率）

---

## 后果

**正面**：
- 看板 JSON 纳入 Git，与代码同生命周期
- 零运营成本，本地开发完整闭环
- 未来可无缝接入 Grafana Cloud 或企业版

**负面**：
- Linux 环境下 `host.docker.internal` 不可用，需手动修改 scrape target
- Prometheus 数据默认非持久化（容器重启后丢失，已挂 Volume 缓解）
- 无内置告警 UI（告警规则需单独配置 Alertmanager，Phase 13 暂不实现）

---

## 参考

- `docker-compose.yml` — prometheus / grafana 服务定义
- `monitoring/prometheus/prometheus.yml` — scrape 配置
- `monitoring/grafana/provisioning/dashboards/arc-overview.json` — 预置看板
