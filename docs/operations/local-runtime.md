# Local Runtime Operations

本文是 Incipit R0 在 Windows + Docker Desktop 环境中的本地运行手册。日常操作从
`incipit.ps1` 进入；只有故障定位时才直接使用 Docker Compose 命令。

## Prerequisites

运行前需要：

- PowerShell 7 或更高版本；
- Docker Desktop 已启动，并使用 Linux 容器；
- Docker Compose 插件可用；
- `arc-knowledge-ai` 与 `arc-knowledge-web` 是同一父目录下的同级目录；
- Docker 至少可用 8 GiB 内存和 20 GiB 磁盘空间，建议 12 GiB 内存和 40 GiB
  磁盘空间；
- LLM 与 Embedding 的 OpenAI 兼容端点已经准备好；默认示例使用宿主机上的
  Ollama。

先在后端仓库根目录创建本地配置：

```powershell
Copy-Item .env.example .env
./incipit.ps1 doctor
```

`.env` 含本机凭据，不能提交到 Git。R0 不负责安装或自动启动 Docker Desktop，
也不在启动时下载模型。

## Configuration and model endpoints

`.env.example` 是配置契约，`.env` 是本机覆盖。至少检查以下配置：

```dotenv
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
JWT_SECRET_KEY=change-this-for-any-shared-environment
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=qwen2.5:7b
EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSIONS=768
```

Compose 内的 API 容器不能用 `127.0.0.1` 访问宿主机模型服务：容器里的
`127.0.0.1` 指向容器自身。Docker Desktop 提供的 `host.docker.internal` 才指向
Windows 宿主机。

使用 Ollama 时，先确认所选模型已在本机存在，并确认 Ollama 正在监听 11434。
如果容器无法连接，而宿主机访问正常，请检查 Ollama 的监听地址；必要时把
`OLLAMA_HOST` 配为 `0.0.0.0:11434` 后重启 Ollama。模型缓存应在启动前准备好；
`HF_HUB_OFFLINE=1` 与 `TRANSFORMERS_OFFLINE=1` 会阻止运行期意外下载模型。

## Core versus full startup

核心启动：

```powershell
./incipit.ps1 start
```

核心模式管理 PostgreSQL、MinIO、etcd、Milvus、Elasticsearch、Redis、Temporal、
Temporal UI、数据库迁移、API、Worker 和 Web。启动顺序不是简单地一次性拉起所有
容器：脚本先检查环境并构建镜像，再等待基础设施健康，执行迁移，然后按
API → Worker → Web 的顺序等待应用就绪。

完整启动：

```powershell
./incipit.ps1 doctor -Full
./incipit.ps1 start -Full
```

`-Full` 额外启用三个 Compose profile：

- `rerank`：Infinity Rerank；
- `ocr`：PaddleOCR 与 MinerU；
- `observe`：Prometheus、Grafana 与 Phoenix。

完整模式需要相应的离线模型缓存。可选服务未准备好时应显示警告或降级，而不是
掩盖为健康状态。

## HEALTHY, DEGRADED, UNHEALTHY, and STOPPED

`./incipit.ps1 status` 汇总容器、API readiness、模型端点和 Temporal Worker poller，
不是 `docker compose ps` 的简单别名。

| 状态 | 含义 | 首选动作 |
|---|---|---|
| `HEALTHY` | 所有必需检查和已启用的可选检查通过 | 可以运行 L1 冒烟测试并使用系统 |
| `DEGRADED` | 核心系统可运行，但模型或可选能力存在警告/失败 | 查看失败项，修复模型端点或可选服务后重新检查 |
| `UNHEALTHY` | PostgreSQL、MinIO、Milvus、Elasticsearch、Redis、Temporal、API 或 Worker 等必需能力失败 | 查看状态和日志，恢复必需服务；不要继续业务操作 |
| `STOPPED` | 没有发现该 Compose 项目的受管容器 | 执行 `./incipit.ps1 start` |

API 的两个健康端点含义不同：

- `/health` 只证明 FastAPI 进程存活，不访问外部依赖；
- `/ready` 检查真实请求需要的依赖。核心依赖失败时返回 HTTP 503 +
  `unhealthy`；仅模型端点失败时返回 HTTP 200 + `degraded`，避免模型抖动导致
  API 容器被反复重启。

## Doctor checks

启动前运行：

```powershell
./incipit.ps1 doctor
./incipit.ps1 doctor -Json
./incipit.ps1 doctor -Full
```

Doctor 检查 PowerShell、Docker CLI、Docker daemon、Compose、配置文件、前端同级
目录、必需环境变量、Docker 资源、Compose 配置、端口占用和模型端点。`-Full`
还检查可选模型缓存。

`Required = True` 且 `State = FAIL` 会阻止 `start`。可选端点不可达通常是 `WARN`，
会产生 `DEGRADED`，但不会假装完整问答链路可用。Doctor 不会替用户启动或停止
Docker Desktop。

## Status and logs

常用诊断命令：

```powershell
./incipit.ps1 status
./incipit.ps1 status -Json
./incipit.ps1 logs
./incipit.ps1 logs -Service api
./incipit.ps1 logs -Service worker -Follow
```

先看 `status` 中具体失败的 check，再看对应服务日志。未指定服务时，日志入口聚合
API、Worker、Web、Temporal 以及异常服务；`-Follow` 持续跟随日志，按 `Ctrl+C`
退出不会停止容器。

必要时可补充查看：

```powershell
docker compose ps
docker compose logs --tail 100 redis
docker compose logs --tail 100 temporal
```

## L0 and L1 smoke tests

L0 是运行时冒烟测试：

```powershell
./incipit.ps1 smoke -Level L0
```

它快速验证 API `/health`、`/ready`、Web 和 Worker 等运行边界，不创建完整业务数据。
适合每次启动后先运行。

L1 是业务闭环冒烟测试：

```powershell
./incipit.ps1 smoke -Level L1
```

它使用 `.env` 中的专用 smoke 账号，验证鉴权、知识空间、上传、异步入库、检索、
问答与引用，并清理本次创建的数据。L1 要求 LLM 与 Embedding 健康；系统仅因模型
不可用而处于 `DEGRADED` 时，L1 会明确失败。不能用一次 L0 成功替代 L1 的业务
闭环证明。

## Restart after Windows reboot

Windows 登录后：

1. 等待 Docker Desktop 完全启动；
2. 打开 PowerShell 7，进入 `arc-knowledge-ai`；
3. 执行启动并验证。

```powershell
./incipit.ps1 doctor
./incipit.ps1 start
./incipit.ps1 smoke -Level L0
./incipit.ps1 smoke -Level L1
```

`start` 是幂等入口，可以再次执行。它会检查现有容器、重新确认依赖健康并执行
幂等迁移。不要把 Docker Desktop 尚未就绪误判为应用损坏。

## Port conflict recovery

Doctor 若报告 `port:<端口>` 被其他进程占用，先根据端口找到 `.env` 中对应的
`*_HOST_PORT`，改成空闲端口。例如 PostgreSQL 5432 冲突：

```dotenv
POSTGRES_HOST_PORT=15432
```

然后重新启动：

```powershell
./incipit.ps1 doctor
./incipit.ps1 start
```

常用映射包括 `POSTGRES_HOST_PORT`、`MINIO_API_HOST_PORT`、
`MINIO_CONSOLE_HOST_PORT`、`MILVUS_HOST_PORT`、`ELASTICSEARCH_HOST_PORT`、
`REDIS_HOST_PORT`、`TEMPORAL_HOST_PORT`、`TEMPORAL_UI_HOST_PORT`、
`API_HOST_PORT` 和 `WEB_HOST_PORT`。修改宿主机端口不会改变容器间使用的内部端口。

## Model endpoint recovery

模型故障的典型现象是 `status` 为 `DEGRADED`、`/ready` 仍返回 HTTP 200，但 L1
失败。按以下顺序恢复：

1. 确认 `.env` 中 LLM 与 Embedding URL 使用 `host.docker.internal`，而不是容器内
   的 `127.0.0.1`；
2. 在 Windows 上确认 Ollama 已启动、模型已存在、监听端口与 `.env` 一致；
3. 如果只允许本机回环访问导致容器连接失败，调整 Ollama 的监听地址并重启它；
4. 重启 API，让它读取恢复后的配置；
5. 再次检查状态和冒烟测试。

```powershell
docker compose restart api
./incipit.ps1 status
./incipit.ps1 smoke -Level L0
./incipit.ps1 smoke -Level L1
```

如果只修改了 `.env`，应使用 `./incipit.ps1 start` 重新应用完整 Compose 配置；
单独 `restart` 不会重新创建容器，也不保证注入更新后的环境变量。

## Container and volume recovery

普通停止与恢复：

```powershell
./incipit.ps1 stop
./incipit.ps1 start
```

`stop` 执行 `docker compose down --remove-orphans`：它删除受管容器和项目网络，
但不带 `--volumes`，因此命名卷会保留。下一次 `start` 会重新创建容器并挂载原卷。

单个服务异常时先看日志；可以重启单个无状态应用容器：

```powershell
docker compose restart api
docker compose restart worker
./incipit.ps1 status
```

核心存储服务恢复后要等待健康，再用统一入口确认迁移和应用状态：

```powershell
docker compose start redis
./incipit.ps1 start
./incipit.ps1 smoke -Level L0
```

R0 没有 `reset-data` 命令。删除 Docker volume 会造成难以恢复的数据丢失，因此
删除卷属于 R0 命令面之外的人工维护操作。不要把 `docker compose down -v` 当成
普通故障恢复命令。

## Data preservation and backup boundary

PostgreSQL、MinIO、etcd/Milvus、Elasticsearch、Redis、Temporal 以及观测组件的数据
使用 Docker 命名卷持久化。可以只读列出本项目卷：

```powershell
docker volume ls --filter label=com.docker.compose.project=incipit
```

停止、再次启动和重新创建容器不应改变这些卷名。命名卷是持久化手段，不等于备份：
磁盘损坏、Docker Desktop 重置、误删卷或跨机迁移都需要独立备份。R0 只保证日常
`stop`/`start` 保留卷，不提供一致性快照、定期备份、自动恢复演练或跨机器还原。
正式备份必须分别考虑 PostgreSQL、MinIO、Milvus/etcd、Elasticsearch 与 Temporal
的数据一致性，并在后续里程碑中设计和验证。

## Known R0 limitations and R0.5 clean-machine verification

R0 的边界是“当前开发机在 Docker Desktop 就绪后可可靠恢复”，已知限制如下：

- 不安装、不配置也不自动启动 Docker Desktop、WSL、Git、PowerShell 或 Ollama；
- 不克隆两个仓库，不自动生成生产凭据；
- 不下载 LLM、Embedding、Rerank、OCR 或 MinerU 模型；
- 不证明代理、Hugging Face 镜像源和模型缓存可在另一台电脑上复现；
- 不提供正式备份、卷删除、灾难恢复或跨机器迁移；
- L1 依赖外部模型端点，模型不可用时不会得到完整验收通过；
- 完整栈资源消耗较高，低于 Doctor 的资源下限时不会启动。
- 后端首次构建会安装完整文档/机器学习依赖，镜像约 14 GiB；首次下载和解包可能
  很慢，也会额外占用 Docker 构建缓存。后续仅修改源码时会复用依赖层。
- 核心容器强制使用 Hugging Face 离线模式；精确 tokenizer 缓存缺失时会降级为字符
  估算，不会在运行时下载模型。
- 当前前端锁文件的 `npm audit` 仍报告依赖漏洞。升级依赖应作为单独维护任务完成并
  回归测试，不能在运行故障处理中直接执行 `npm audit fix --force`。

R0.5 应在一台干净 Windows 机器上验证：Docker Desktop/WSL 安装、仓库获取、首次
配置、模型与缓存准备、代理/离线策略、镜像构建、一次完整启动、L1 冒烟、重启恢复
和备份还原。完成该验证前，不能把 R0 描述为面向任意新电脑的一键安装器。
