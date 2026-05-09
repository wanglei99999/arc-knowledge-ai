# ADR-044: Demo Dashboard 保留 Mock 数据，另设 Admin 页承载真实数据

**状态**: 已采纳  
**日期**: 2026-05-08  
**决策人**: 开发团队

---

## 背景

Phase 13 前端工作包含两个目标：
1. 将 Dashboard 页的硬编码 Mock 统计数据替换为真实 API
2. 新增 Admin 管理界面（模型定价 CRUD、租户配置）

考虑到真实数据依赖生产环境输入（文档上传、LLM 调用），在 Demo 或演示场景下，空数据库会导致图表全空、统计卡片均为 0，展示效果差。

---

## 备选方案

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| A：Dashboard 替换为真实数据 | 原 Dashboard 接真实 API，删除 Mock | 数据真实，无歧义 | Demo 时需先灌数据，展示受限 |
| B：Dashboard 保留 Mock，另设 Admin 页 | Dashboard 保留硬编码，新建 `/admin` 承载真实统计和管理功能 | Demo 随时可用，真实管理独立清晰 | 两处数字并存，需向用户说明 |
| C：Dashboard 支持 Mock/Real 切换开关 | 前端 Flag 控制数据来源 | 灵活 | 代码分支复杂，维护两套逻辑 |

---

## 决策

选择 **方案 B**：Dashboard 页保留全部 Mock 数据不做改动，新建 `/admin` 页面展示真实数据并提供管理功能。

Demo 阶段产品形态展示比数据真实性更重要；`/admin` 页面作为运营入口，默认面向已有真实数据的生产环境。两个页面职责清晰分离：Dashboard 是产品演示入口，Admin 是运营管理入口。

---

## 实现要点

- `src/views/dashboard/index.vue`：不做任何修改，保留原有 Mock 数据
- `src/views/admin/index.vue`（新建）：
  - 顶部：真实统计卡片（`GET /admin/stats`）
  - 趋势图：7 天 Token 消耗（`GET /admin/tenants/{id}/usage?group_by=day`）
  - Tab 1：模型定价 CRUD（`GET/POST/DELETE /admin/models`）
  - Tab 2：租户 LLM 配置（`GET/PUT /admin/tenants/{id}/config`）
- 侧边栏新增"管理配置"导航项，路由 `/admin`

---

## 后果

**正面**：
- Demo 可随时展示完整 UI，不依赖数据灌入
- Admin 页数据完全来自真实 API，运营使用无歧义
- 两页面独立演进，互不干扰

**负面**：
- Dashboard 的 Mock 数字与真实状态可能不一致，需要向用户说明这是演示数据
- 维护两个入口页面，后期需决定何时统一（接 Java 控制面后 Dashboard 再真实化）

---

## 参考

- `src/views/dashboard/index.vue` — Demo 入口（Mock）
- `src/views/admin/index.vue` — 运营管理入口（真实数据）
- `src/api/admin.ts` — Admin 相关 API 函数
