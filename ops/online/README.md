# 独立在线 API 部署入口

本目录构建纯 API v2 镜像，使用独立 PostgreSQL、批准规则和显式连接配置。生成可迁移的完整 Compose kit 见 [构建与发布](../../docs/构建与发布.md)。

## 基础镜像与规则输入

在仓库根目录构建：

```sh
docker build -f ops/online/Dockerfile -t ptcg-lab-online-api:current .
```

Dockerfile 专属 ignore 采用白名单，只允许三个共享 Python 包与 online-server 的源码和包元数据进入构建上下文。运行层安装 wheel，不安装 React、图片、Pillow、AI/训练包，也不复制真实规则或用户数据。`PYTHON_IMAGE` 默认已固定本次构建使用的 Python 3.14 slim 摘要，可由部署方显式覆盖为其他已批准摘要；执行身份包含 Python patch 版本，因此应针对最终镜像确认 ruleReleaseId。

无规则的基础镜像 `/healthz` 可正常响应，`/readyz` 必须失败。Docker HEALTHCHECK 仅检查存活，不代表已安装可开局规则。可选 `compose.rules.yaml` 只读挂载受控 provider 目录，并要求显式 `bundle.json` SHA-256 和批准的执行身份；不自动从研究工作区搜索规则或复制资产。

## 专用本地 Compose 配置

先创建权限受限的独立凭证目录（输出必须不存在）：

```sh
python scripts/product/server_secrets.py --output var/online-v2/new-secrets
export PTCG_V2_DATABASE_PASSWORD_FILE="$PWD/var/online-v2/new-secrets/database-password"
export PTCG_V2_API_ENV_FILE="$PWD/var/online-v2/new-secrets/api.env"
export PTCG_V2_SERVER_ID=ptcg-v2-local-acceptance
docker compose -p ptcg-v2-new-acceptance -f ops/online/compose.yaml config --quiet
```

API 环境文件由 Compose 客户端读取后注入容器，避免非 root API 用户无法读取宿主 0600 bind secret 的差异；它不进入镜像或 GUI。运维仍需保护 Docker 管理权限，不把 `docker inspect`/完整 Compose 展开结果作为公开日志。也支持通过 `PTCG_ONLINE_V2_DATABASE_URL_FILE` 从部署秘密管理器读取 DSN；与直接 DATABASE_URL 二选一。

在**专用新项目**启动时，先启动 database，再使用 API 镜像显式迁移，最后启动 api：

```sh
docker compose -p ptcg-v2-new-acceptance -f ops/online/compose.yaml up -d database
docker compose -p ptcg-v2-new-acceptance -f ops/online/compose.yaml run --rm --entrypoint python api -m ptcg_online_server.migrate
docker compose -p ptcg-v2-new-acceptance -f ops/online/compose.yaml up -d api
```

API 端口仅映射 `127.0.0.1:8784`，数据库不发布宿主端口；新项目拥有自己的卷。原 v1 服务不在本 Compose 中。API 运行层使用 UID 10001、只读根目录和空能力集合。默认环回 HTTP 仅用于本机验收，外部环境需配置准确 HTTPS public origin 和 TLS 网关。

如需规则 overlay，所有上述命令一致追加 `-f ops/online/compose.rules.yaml`，并设置 `PTCG_V2_RULE_DIRECTORY`、`PTCG_V2_RULE_MANIFEST_SHA256`、`PTCG_V2_APPROVED_RULE_RELEASE_ID`。该目录需对容器 UID 10001 可读；不存在时拒绝创建宿主路径。readiness 还要求真实规则子进程启动成功；只有批准规则和进程就绪才开启 acceptingMatches。

TLS 网关需同时代理 HTTP 和 `/api/online/v2/stream` WebSocket，保留配置的 Host 与 Authorization，禁止记录凭证。当前 API 拒绝 Origin/Cookie，因此客户端为本地 APP 的原生网络层。不要打开浏览器 CORS 绕过此边界。流和回放同时受服务端配额约束。

停止时只对自己创建的项目执行 down；不使用全局 Docker prune，不停止其他项目，也不将新库合并回旧局域网数据。没有默认 `down -v` 操作，数据删除需要单独处理。

所有 schema migration 通过服务 CLI 显式应用。升级前备份数据库与当前镜像身份，不把升级后的数据库交给不匹配的旧镜像；回退是整套部署回退。

## 有界并发配置

Compose 中的 `PTCG_V2_DB_POOL_*` 控制连接复用、连接上限与等待者；`COMMAND_QUEUE_*` 限制全局、每局和每用户的排队。`STREAM_*` 限制握手、缓冲和推送速率，`REPLAY_*` 限制分页/并发。维护任务按批执行并有 SQL 时限。

默认单 API 和单常驻规则进程，`PTCG_V2_WORKER_COUNT` 可增加串行通道；同局保持稳定分配，缓存按项数、记录字节、空闲时长与 RSS 约束。缓存不作为跨 API 副本共享状态。`WORKER_COLD_LIMIT` 和带权冷热队列限制恢复风暴。具体名称、默认值见 compose.yaml 与服务配置源码。调整参数后仅重启对应部署，不触碰其他环境。

容量要按最终硬件、镜像和实际卡组测量；本目录不承诺固定并发局数。TLS 网关、证书续期、备份恢复与监控须在目标部署验收。
