# ADR-045: AlertRule 以占位形式实现，暂不接入 Alertmanager

**状态**: 已采纳  
**日期**: 2026-05-11  
**决策人**: 开发团队

---

## 背景

Phase 13 原计划包含三条 Prometheus AlertRule（Stage 延迟过高 / Pipeline 错误率过高 / LLM 费用超限）。告警完整闭环需要 Alertmanager 容器 + 通知渠道（邮件 / Slack / 钉钉）配置，成本较高。

当前系统尚无真实用户，不存在"需要半夜叫醒人"的运营场景，告警通知的实际价值有限。但规则定义本身有文档价值，且在 `localhost:9090/alerts` 可视化展示对演示有帮助。

---

## 备选方案

| 维度 | 方案 A：完整实现 | 方案 B：占位实现 | 方案 C：跳过 |
|------|----------------|----------------|------------|
| 工作量 | 高（Alertmanager + 通知渠道） | 低（仅规则文件） | 无 |
| 演示价值 | 高 | 中（规则可见） | 无 |
| 运营价值 | 高 | 低（无通知） | 无 |
| 当前适用性 | 过度 | 合适 | 丢失文档价值 |

---

## 决策

选择**方案 B：占位实现**。

只编写 `alerting_rules.yml` 定义三条规则，挂载给 Prometheus 后在 `/alerts` 页面可见。不引入 Alertmanager，不配置任何通知渠道。等系统有真实用户、需要运营告警时，再补接 Alertmanager。

---

## 实现要点

- `monitoring/prometheus/alerting_rules.yml`：三条规则，阈值：P95 延迟 > 3s（warning）、错误率 > 5%（critical）、费用 > $10（warning）
- `for: 2m`：延迟和错误率规则等待 2 分钟持续触发再报警，避免毛刺误报
- `for: 0m`：费用规则立即触发
- `prometheus.yml` 新增 `rule_files` 引用

---

## 后果

**正面**：
- 规则定义纳入 Git，阈值和触发条件有据可查
- `localhost:9090/alerts` 展示三条规则，演示时可见
- 后续接入 Alertmanager 只需加容器 + 配置路由，规则本身无需改动

**负面**：
- 当前无实际告警通知能力，规则触发后无人感知
- 阈值（3s / 5% / $10）是经验值，需上线后根据真实流量调整

---

## 参考

- `monitoring/prometheus/alerting_rules.yml` — 告警规则定义
- `monitoring/prometheus/prometheus.yml` — rule_files 引用
- [ADR-043](ADR-043-prometheus-grafana-observability.md) — Prometheus + Grafana 选型
