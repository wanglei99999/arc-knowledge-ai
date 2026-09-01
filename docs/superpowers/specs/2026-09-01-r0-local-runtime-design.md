# R0 本地运行闭环设计

**日期：** 2026-09-01  
**状态：** 待用户审阅  
**范围：** `arc-knowledge-ai`、`arc-knowledge-web` 与本机 Docker Desktop 运行环境

## 1. 背景

Incipit 已经具备文档上传、异步入库、混合检索、RAG 问答、引用、会话、知识空间和管理页面等主要能力，但本地运行仍由多套边界共同承担：

- PostgreSQL、MinIO、Milvus、Elasticsearch、Redis、Temporal 等依赖通过 Docker Compose 启动；
- FastAPI、Temporal Worker 和 Vue 前端依赖宿主机分别启动；
- 当前后端 `.venv` 来自 Linux/WSL，日常操作环境又是 Windows PowerShell；
- Docker、数据库迁移、API、Worker、前端和模型服务之间缺少统一的启动顺序与就绪判断；
- 当前 `/health` 只证明 FastAPI 进程存活，不能证明数据库、检索服务、任务队列和模型链路可用；
- 前端已有自动化测试与 CI，后端缺少等价的持续验证入口。

这使“代码已经实现”与“系统可以可靠使用”之间仍有明显距离。R0 的任务不是增加产品功能，而是建立一个稳定、可诊断、可重复验证的本地运行基线。

## 2. R0 定义

R0 的目标是：

> Windows 登录后，在 Docker Desktop 已启动的前提下，用户执行一条命令即可启动完整核心系统；命令能够等待系统真正就绪，并通过固定冒烟流程证明上传、入库、检索、问答和引用链路可用。

R0 只承诺当前开发机的可靠恢复。全新电脑上的 Docker Desktop 安装、仓库克隆、大模型下载和首次环境引导属于后续 R0.5。

## 3. 目标

- 核心运行时不再依赖宿主机 Python、Node 或现有 `.venv`。
- 使用 Docker Compose 统一编排基础设施、数据库迁移、FastAPI、Temporal Worker 和前端。
- 提供稳定的 `doctor`、`start`、`stop`、`status`、`logs` 和 `smoke` 命令。
- 默认启动轻量核心能力；OCR、Rerank 和可观测性按需启用。
- 区分进程存活与服务就绪，失败时指出具体故障组件和建议动作。
- 停止或重复启动系统时不删除数据、不重复初始化业务记录。
- 用固定样本文档验证真实业务闭环，而不只检查容器是否处于 running 状态。
- 建立后端单元测试和容器构建的 CI 闸门。
- 在实施过程中同步讲解容器、网络、健康检查、卷、幂等和 CI 等运维概念。

## 4. 非目标

- 不自动安装或启动 Docker Desktop GUI；Docker Desktop 配置为登录后自动启动。
- 不承诺在全新电脑上一条命令完成全部首次安装。
- 不在 R0 中实现 SHA-256 去重、卡死任务回收或孤儿数据巡检。
- 不在 R0 中调整 Chunk、Top-K、RRF、Rerank 或 Prompt 参数。
- 不实现 Java 控制面、Kubernetes、Helm、连接器或百万文档压测。
- 不要求默认下载和启动 MinerU、PaddleOCR、Infinity 等大型模型。
- 不实现数据库备份、灾难恢复或生产级密钥管理。
- 不提供自动删除全部本地数据的快捷命令。

## 5. 方案比较

### 5.1 方案 A：宿主机脚本编排

PowerShell 依次启动 Docker 依赖、Windows Python API、Windows Python Worker 和 Node 前端。

优点是改动少、开发热更新直接。缺点是继续依赖宿主机 Python、Node、编译库和虚拟环境，无法消除当前 Windows/WSL 混用的根因。该方案不作为 R0 主运行方式。

### 5.2 方案 B：核心系统全部容器化（采用）

Docker Compose 同时管理基础设施、迁移任务、API、Worker 和 Web。宿主机只需要 Docker Desktop 与 PowerShell 管理脚本。

该方案启动边界统一、日志入口统一、重复执行行为清晰，也最接近未来的私有化交付方式。代价是需要维护 API、Worker 和 Web 镜像，首次构建时间更长。

### 5.3 方案 C：全新电脑安装器

自动安装 Docker Desktop、WSL、Git、模型和代理配置，并完成仓库初始化。

这会把应用编排、操作系统安装和网络适配混成一个里程碑，故障面过大。该方案推迟到 R0.5，并复用 R0 已稳定的容器运行层。

## 6. 运行架构

### 6.1 编排所有权

运行编排继续由 `arc-knowledge-ai/docker-compose.yml` 管理，因为现有基础设施定义、迁移脚本、API 和 Worker 均位于该仓库。Web 镜像的构建上下文指向兄弟目录 `../arc-knowledge-web`。

R0 明确依赖以下目录布局：

```text
knowledge_base/
├── arc-knowledge-ai/
└── arc-knowledge-web/
```

管理入口位于 `arc-knowledge-ai/incipit.ps1`。R0.5 如需支持任意目录或独立发行包，再引入顶层运行仓库或安装清单。

### 6.2 服务分层

默认核心层：

```text
postgres + minio + etcd + milvus + elasticsearch + redis + temporal
                                  ↓
                               migrate
                                  ↓
                           api + worker
                                  ↓
                                web
```

可选能力通过 Compose profile 启用：

| Profile | 服务 | 用途 |
|---|---|---|
| `rerank` | Infinity | BGE Rerank；不可用时检索链路按现有策略降级 |
| `ocr` | MinerU、PaddleOCR | 扫描版 PDF 和复杂文档解析 |
| `observe` | Prometheus、Grafana、Phoenix | 指标与 Trace 调试 |

`start` 只启动核心层；`start -Full` 启用三个可选 profile。单独 profile 可在实现时通过参数组合开放，但不增加多个重复脚本。

### 6.3 外部模型依赖

LLM 与 Embedding 继续使用 `.env` 指定的 OpenAI 兼容端点或 Ollama。R0 不强制把 Ollama 纳入 Compose，但 `doctor` 和 `status` 必须检查所选端点是否可达。

Rerank 是可降级能力；LLM 和 Embedding 是完整业务冒烟测试的必需能力。缺失时核心容器仍可启动，但系统状态显示为 `degraded`，完整 `smoke` 返回失败并说明缺少的模型依赖。

## 7. 配置设计

### 7.1 配置分层

- `.env.example`：可提交的字段说明和安全默认值，不包含真实密钥。
- `.env`：本机配置，Git 忽略。
- Compose 内部地址：固定使用服务 DNS 名称，例如 `postgres:5432`、`temporal:7233`。
- 宿主机端口：所有对宿主机暴露的端口都通过环境变量覆盖，例如 `POSTGRES_HOST_PORT=5432`，发生冲突时可改为 `15432`。

容器内部地址与宿主机端口必须分开。API 容器不能使用 `localhost:15432` 访问 PostgreSQL，因为容器中的 `localhost` 指向 API 容器自身。

### 7.2 镜像和模型缓存

- 基础镜像与第三方服务使用明确版本，不使用 `latest`。
- Hugging Face、OCR 和 Rerank 模型缓存使用命名卷，容器重建后继续复用。
- R0 不自动执行大型模型下载；可选服务缺少模型时给出明确诊断。
- 代理变量只通过显式配置传入需要联网的构建或模型服务，不读取未知的宿主机代理状态。

### 7.3 数据持久化

PostgreSQL、MinIO、Milvus、etcd、Elasticsearch、Redis 和 Phoenix 继续使用命名卷。`stop` 只停止并移除容器，不传递 `--volumes`。

R0 不提供 `reset-data` 管理命令。确需清盘时必须使用显式 Docker 命令并人工确认目标卷，这属于维护操作，不属于日常启动流程。

## 8. 管理命令

统一入口：

```powershell
.\incipit.ps1 doctor
.\incipit.ps1 start
.\incipit.ps1 start -Full
.\incipit.ps1 stop
.\incipit.ps1 status
.\incipit.ps1 logs
.\incipit.ps1 smoke
```

### 8.1 `doctor`

只读检查，不启动服务：

- Docker CLI 与 Compose 插件是否存在；
- Docker daemon 是否可连接；
- 两个兄弟仓库和关键文件是否存在；
- `.env` 是否存在，必需字段是否缺失；
- Compose 配置是否能通过解析；
- 宿主机端口是否冲突；
- Docker 可用内存低于 8 GiB 或磁盘空间低于 20 GiB 时失败；内存低于 12 GiB 或磁盘低于 40 GiB 时警告；
- 所选 LLM、Embedding 和可选模型端点是否可达。

检查结果按 `PASS`、`WARN`、`FAIL` 输出。存在 `FAIL` 时 `start` 不继续；只有可降级依赖失败时输出 `WARN`。

### 8.2 `start`

启动顺序固定为：

1. 执行 `doctor`。
2. 启动核心基础设施。
3. 按条件轮询基础设施健康状态。
4. 运行一次性 `migrate` 服务，成功退出后继续。
5. 启动 API 与 Worker。
6. 等待 API readiness 和 Temporal Worker poller 注册。
7. 启动 Web。
8. 输出访问地址、降级项和下一条建议命令。

任何阶段失败都停止后续阶段，保留已启动容器供诊断，并打印失败服务的最近日志。脚本返回非零退出码。

### 8.3 `stop`

停止 R0 管理的容器并保留命名卷。重复执行视为成功。停止失败时报告仍在运行的容器，不尝试删除数据。

### 8.4 `status`

状态不是简单转发 `docker compose ps`，而是汇总：

- 容器运行状态；
- 容器健康状态；
- API liveness 与 readiness；
- Temporal Worker 是否注册 poller；
- LLM、Embedding、Rerank、OCR 的可用或降级状态；
- Web 入口是否响应。

最终状态为 `healthy`、`degraded` 或 `unhealthy`。

### 8.5 `logs`

默认聚合 API、Worker、Web 和失败服务的日志。支持传入服务名查看单个服务。R0 使用 Docker 日志作为唯一运行日志入口，不再为后台 PowerShell 进程维护 PID 与独立日志文件。

### 8.6 `smoke`

执行固定业务冒烟测试，结束后输出每个阶段的耗时和结果。失败时保留必要测试记录供排查，但不得删除用户已有数据。

## 9. 健康模型

### 9.1 Liveness

`GET /health` 继续只回答 FastAPI 进程是否存活。它不访问外部依赖，避免依赖抖动导致容器被反复重启。

### 9.2 Readiness

新增 `GET /ready`，检查处理真实请求所需的依赖：

- PostgreSQL 可执行轻量查询；
- MinIO Bucket 可访问；
- Milvus Collection 可访问；
- Elasticsearch Index 可访问；
- Redis 可执行 PING；
- Temporal 可连接；
- LLM、Embedding、Rerank 和 OCR 配置及端点状态。

响应逐项返回状态，不只返回一个布尔值。PostgreSQL、MinIO、Milvus、Elasticsearch、Redis 或 Temporal 任一核心依赖失败时返回 HTTP 503，状态为 `unhealthy`。模型端点不可用时返回 HTTP 200，状态为 `degraded`，避免外部服务抖动触发 API 容器重启；完整 L1 `smoke` 仍返回失败。

### 9.3 Worker 就绪

Worker 不额外启动 HTTP 服务。`status` 通过 Temporal 的 task queue 描述接口确认 `arc-ingestion` 队列存在活跃 poller。这样检查的是 Worker 是否真正准备接收任务，而不是 Python 进程是否存在。

## 10. 数据库迁移

迁移脚本封装为一次性 Compose 服务 `migrate`，与 API/Worker 使用同一后端镜像和配置。启动流程只有在迁移进程以零状态退出后才继续。

迁移必须满足：

- 在空数据库上可以完整执行；
- 在当前数据库上重复执行安全；
- 失败时不启动依赖新结构的 API 与 Worker；
- 日志明确显示失败 SQL 所属迁移阶段；
- R0 不自动执行破坏性回滚。

## 11. 冒烟测试

### 11.1 L0 运行时冒烟

不创建业务数据，验证：

- 核心容器全部达到预期健康状态；
- 数据库迁移已完成；
- API `/health` 与 `/ready` 响应；
- Worker 已注册 Temporal poller；
- Web 首页可访问。

### 11.2 L1 业务闭环冒烟

使用仓库内固定的小型 Markdown 或文字型 PDF，避免 OCR 成为默认路径的前置条件：

1. 创建或复用专用 smoke 用户。
2. 创建带唯一后缀的 smoke 知识空间。
3. 上传样本文档。
4. 轮询直到文档进入 `indexed` 或明确失败。
5. 对固定问题执行检索并验证命中样本文档。
6. 发起 RAG 问答并验证至少存在一条指向样本文档的引用。
7. 删除样本文档和 smoke 空间中本次创建的数据。
8. 输出各阶段耗时、文档 ID、失败组件和最近 Trace ID（存在时）。

测试数据使用唯一运行 ID 隔离。清理只针对本次创建并记录 ID 的数据；清理失败作为独立警告报告，不能扩大删除范围。

## 12. 错误处理

| 场景 | 行为 |
|---|---|
| Docker daemon 未运行 | `doctor` 失败，提示启动 Docker Desktop，不尝试启动 GUI |
| 宿主机端口冲突 | 显示冲突端口和对应覆盖变量，不自动终止占用进程 |
| `.env` 缺少密钥 | 核心启动可继续或失败取决于字段是否必需；`smoke` 明确拒绝执行完整问答 |
| 基础设施健康超时 | 停止后续启动，打印失败容器最近日志 |
| 数据库迁移失败 | API/Worker 不启动，迁移容器和数据库保留供诊断 |
| Worker 未注册 | 系统标为 unhealthy，上传入口不得宣称可用 |
| Rerank/OCR 不可用 | 系统标为 degraded，沿用已有降级策略 |
| LLM/Embedding 不可用 | readiness 返回 HTTP 200 + degraded；完整 L1 smoke 失败 |
| 重复执行 `start` | 复用现有容器和卷，迁移幂等，不创建重复后台进程 |

等待使用条件轮询和明确超时，不使用固定 `sleep` 猜测服务启动时间。

## 13. 安全边界

- `.env` 与真实密钥不得进入镜像层、构建日志或 Git。
- Web 只暴露必要的宿主机端口；数据库管理端口保留为本地开发用途，不绑定外部网卡。
- Compose 网络内通过服务名互联，不把内部服务暴露给公网。
- `logs` 和 `smoke` 不输出完整 Token、API Key、文档正文或 Prompt。
- 管理脚本不包含删除卷、删除目录或清空数据库的默认路径。

## 14. CI 设计

### 14.1 后端 CI

为 `arc-knowledge-ai` 增加 GitHub Actions：

1. 安装锁定的 Python 版本与 `uv`。
2. 按 `uv.lock` 安装依赖。
3. 运行后端单元测试。
4. 校验 Compose 配置。
5. 构建 API/Worker 镜像，避免 Dockerfile 漂移到无法构建。

R0 不要求每次 PR 在 GitHub Runner 中启动 Milvus、Elasticsearch 和完整 L1 冒烟测试，因为资源与外部模型依赖过重。完整冒烟测试作为本地发布闸门；后续可增加手动触发或自托管 Runner。

### 14.2 前端 CI

保留现有单元测试、Playwright E2E 和构建步骤，并追加 Web 镜像构建校验。前后端仓库各自保持绿色，不建立跨仓库提交的原子性假象。

## 15. 教学式实施方式

实现 R0 时，每个阶段按相同格式沟通：

1. **概念**：说明本阶段对应的运维知识。
2. **现状**：展示当前代码为什么不能满足目标。
3. **设计**：解释选用方案及舍弃方案。
4. **修改**：列出实际变更文件和关键配置。
5. **验证**：运行命令并解释输出中哪些字段最重要。
6. **故障演练**：至少演示一个安全、可恢复的失败场景。

阶段对应的学习主题：

| 阶段 | 学习主题 |
|---|---|
| 镜像与 Dockerfile | 镜像、容器、构建上下文、层缓存、多阶段构建 |
| Compose 核心层 | 服务 DNS、宿主机端口、依赖关系、profile |
| 卷与配置 | 数据持久化、环境变量、Secret 不进入镜像 |
| 健康检查 | liveness、readiness、依赖健康与降级 |
| 管理脚本 | 幂等命令、退出码、条件等待、故障诊断 |
| 冒烟测试 | 运行时检查与业务闭环检查的区别 |
| CI | 可复现构建、质量闸门、资源型测试分层 |

讲解以当前项目中的真实文件和命令为例，不额外写一套与实现脱节的教程。

## 16. 实施分解

R0 按以下顺序实施，每一步独立验证：

1. 配置契约与 `doctor` 检查。
2. API/Worker 共用后端镜像。
3. Web 生产镜像。
4. Compose 核心层、可选 profile 与版本固定。
5. 一次性数据库迁移和启动顺序。
6. API readiness 与 Worker poller 检查。
7. `start/stop/status/logs` 管理命令。
8. L0 与 L1 冒烟测试。
9. 后端 CI、Web 镜像 CI 与运行文档。

不并行修改同一 Compose 和管理脚本，避免运行契约在多个阶段同时漂移。

## 17. 验收标准

R0 完成必须同时满足：

- Windows 重启并登录、Docker Desktop 就绪后，一条 `start` 命令启动核心系统。
- 用户无需手动激活 Python 虚拟环境、运行 `npm` 或打开多个终端。
- `start` 连续执行两次均成功，不创建重复业务数据或孤立进程。
- 默认启动不强制加载 OCR、Rerank 和可观测性重型服务。
- `status` 能区分 healthy、degraded 和 unhealthy，并指出具体组件。
- `stop` 后所有命名卷和用户数据保留。
- 数据库迁移在空库和现有库上都能成功执行。
- L0 运行时冒烟测试通过。
- 配置有效模型端点时，L1 上传、入库、检索、问答和引用冒烟测试通过。
- 后端单元测试、Compose 校验、后端镜像构建、前端单元测试、前端 E2E、前端构建和 Web 镜像构建均通过对应闸门。
- README 只保留一条权威的 R0 启动路径，旧的手动启动方式标记为开发调试路径。

## 18. 发布与回退

R0 不替换现有手动开发方式，而是新增权威的容器化运行方式。发布顺序为：

1. 合入镜像和 Compose 定义，但保留旧服务定义。
2. 验证 `doctor`、核心基础设施与迁移。
3. 验证 API、Worker、Web 和 L0 冒烟。
4. 配置真实模型后验证 L1 冒烟。
5. 更新 README，把容器化方式设为默认路径。

若容器化应用层存在阻塞，可停止新增的 API、Worker、Web 容器并恢复手动启动；命名卷与基础设施保持不变，不执行破坏性数据库回滚。
