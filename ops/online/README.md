# 独立在线 API 部署入口（P6）

此目录对应新 `ptcg-online-server`，不复用旧网页 v1 镜像、数据库、CA 或服务。P4-C 已支持认证、卡组、房间、权威命令、控制权、私有推送/回放和重启恢复；P4-D 已通过完整自然终局及故障矩阵的本地验收。外部发布、正式镜像摘要锁定及完整运维验收属于 P8。

## 基础镜像与规则输入

在仓库根目录构建：

```sh
docker build -f ops/online/Dockerfile -t ptcg-lab-online-api:p6e .
```

Dockerfile 专属 ignore 采用白名单，只允许三个共享 Python 包与 online-server 的源码和包元数据进入构建上下文。运行层安装 wheel，不安装 React、图片、Pillow、AI/训练包，也不复制真实规则或用户数据。`PYTHON_IMAGE` 默认已固定本次构建使用的 Python 3.14 slim 摘要，可由部署方显式覆盖为其他已批准摘要；执行身份包含 Python patch 版本，因此应针对最终镜像确认 ruleReleaseId。

无规则的基础镜像 `/healthz` 可正常响应，`/readyz` 必须失败。Docker HEALTHCHECK 仅检查存活，不代表已安装可开局规则。可选 `compose.rules.yaml` 只读挂载受控 provider 目录，并要求显式 `bundle.json` SHA-256 和批准的执行身份；不自动从研究工作区搜索规则或复制资产。

## 专用本地 Compose 配置

先创建权限受限的独立凭证目录（输出必须不存在）：

```sh
python scripts/migration/p4a_secrets.py --output var/online-v2/new-secrets
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

如需规则 overlay，所有上述命令一致追加 `-f ops/online/compose.rules.yaml`，并设置 `PTCG_V2_RULE_DIRECTORY`、`PTCG_V2_RULE_MANIFEST_SHA256`、`PTCG_V2_APPROVED_RULE_RELEASE_ID`。该目录需对容器 UID 10001 可读；不存在时拒绝创建宿主路径。readiness 还要求真实规则子进程启动成功；P4-C 在批准规则和进程就绪时开启 acceptingMatches。

TLS 网关需同时代理 HTTP 和 `/api/online/v2/stream` WebSocket，保留配置的 Host 与 Authorization，禁止记录凭证。当前 API 拒绝 Origin/Cookie，因此客户端为本地 APP 的原生网络层。不要打开浏览器 CORS 绕过此边界。新配额 MAX_STREAMS、OUTBOX_RETENTION、MAX_MATCH_COMMANDS 与流恢复合同见 P4-C 文档。

停止时只对自己创建的项目执行 down；不使用全局 Docker prune，不停止其他项目，也不将新库合并回旧局域网数据。没有默认 `down -v` 操作，数据删除需要单独处理。

实际镜像构建/运行状态见 [P4 验收记录](../../docs/P4纯API服务实施记录.md)，配置文件存在不代表已经完成 Docker 或外部部署验收。

P4-C 必须显式应用新增 003 migration。已升级的数据库不能直接给旧 p4a/p4b 镜像使用；回退需升级前备份或专用新库。详细证据与范围见 [P4-C 验收](../../docs/P4C对局提交与私有推送验收.md)。P4-D 沿用 001—003，不新增 migration；版本为 0.4.0.dev4，测试故障工厂不进入 wheel 或镜像。结果见 [P4-D 验收](../../docs/P4D完整对局与故障恢复验收.md)。本次未启动长期 Compose 部署。

## P6-A 连接池开关

新建部署示例的镜像标签为 `ptcg-lab-online-api:p6a`。本次没有自动更新任何正在运行的服务。Compose 中 `PTCG_V2_DB_POOL_ENABLED/SIZE/WAITERS/TIMEOUT` 分别映射服务端 `PTCG_ONLINE_V2_DB_POOL_*`，默认 `1/4/16/1`。关闭 ENABLED 仅关闭复用，仍限制并发连接与等待者；恢复旧行为不等于取消鉴权或原子持久提交。重新启动本次部署才应用新值。范围和基线见 [P6 记录](../../docs/P6多局调度与性能实施记录.md)，不能据这些默认值推断可支持对局数。

## P6-B 集中推送开关

新镜像标签 `ptcg-lab-online-api:p6b`，服务版本 `0.6.0.dev2`。Compose 的 `PTCG_V2_STREAM_HUB_ENABLED` 映射服务端 `PTCG_ONLINE_V2_STREAM_HUB_ENABLED`，默认 `1`；设 `0` 并重启本次部署恢复逐连接轮询。沿用 001—003 schema，无新数据库迁移，也不增加专用监听连接。仅本进程提交即时通知，其他写入由周期补漏发现。没有自动替换既有局域网服务。

## P6-C 调度预算

新镜像标签 `ptcg-lab-online-api:p6c`，服务版本 `0.6.0.dev3`。Compose 传递 `PTCG_V2_COMMAND_QUEUE_ENABLED/TOTAL/PER_MATCH/PER_USER/WAITERS/DUPLICATES/SECONDS`，默认 `1/32/2/4/64/4/2`；服务对应 `PTCG_ONLINE_V2_` 前缀。任务上限包含执行中任务，重试等待者单独计数，排队期限不取消已开始的规则计算。关闭 ENABLED 后重启该部署可回退原路径，无数据库迁移。单 API 部署限定未变；这些参数不是已验证的容量承诺。本轮镜像仅供隔离验收，未替换局域网服务。

## P6-D 常驻 worker 与缓存

隔离验收镜像 `ptcg-lab-online-api:p6d`，服务版本 `0.6.0.dev4`。默认单 API、单常驻规则进程；`PTCG_V2_WORKER_COUNT=2` 才开启第二个串行通道。各对局稳定分配通道，不支持跨 API 副本共享缓存。`PTCG_V2_WORKER_COLD_LIMIT=1` 限制同时冷恢复，满额返回可重试错误；带权冷热队列留到 P6-E。

默认每 worker 最多 8 个缓存、8 MiB 序列化记录、60 秒闲置期，峰值 RSS 达 256 MiB 后回收进程。记录大小不等于引擎对象占用，RSS 水位不代替容器硬限制。冷恢复／计算预算分别 10／5 秒，管道操作另有 20 秒截止时间。恢复超时只能恢复已提交状态，不能重开对局。

`PTCG_V2_WORKER_CACHE_ENTRIES=0` 关闭缓存但继续复用规则进程；`PTCG_V2_WORKER_PERSISTENT=0` 回退一次性子进程。参数修改后重启本次部署生效，沿用原 schema。全部参数范围、提交确认和代次隔离见 [P6-D 实施记录](../../docs/P6多局调度与性能实施记录.md)。本轮没有更新正在运行的 LAN API，不将固定短局测试当作可承载对局数。


## P6-E 资源预算与回退

隔离镜像 `ptcg-lab-online-api:p6e`，服务 `0.6.0.dev5`。没有自动替换既有 LAN 服务。新增 Compose 参数均以 `PTCG_V2_` 开头，对应服务端 `PTCG_ONLINE_V2_`；完整默认值、范围与验证见 [P6-E 实施记录](../../docs/P6多局调度与性能实施记录.md#p6-e带权调度与资源预算)。

默认冷热维护权重 2:1:1，维护规则任务最多 2 个。`WORKER_WEIGHTED_ENABLED=0` 重启后回退原跨局轮转，`WORKER_CACHE_ENTRIES=0` 关闭缓存，`WORKER_PERSISTENT=0` 回退一次性进程，`STREAM_HUB_ENABLED=0` 回退逐连接轮询；以上开关均保留鉴权、幂等、原子提交与字节预算。无需新增数据库 migration。

HTTP 和 WSS 独立准入，传输总上限为 HTTP 槽数 + 流槽数 + `CONNECTION_RESERVE`（默认 8）。`STREAM_HANDSHAKES=8` 限未完成订阅数。发送默认每条 WSS 1 MiB/s、2 MiB 突发，单消息 1 MiB、读取页 2 MiB，等令牌最多 1 秒；这是 JSON 载荷预算，不包含 TLS/TCP 开销。小错误／心跳不占数据字节桶。慢消费者断开后按持久游标恢复，超大帧不会静默丢弃。

回放默认并发 2、页 1 MiB、对端地址摘要每分钟 120 次、全局 360 次，同时受普通 HTTP 预算约束。后台每轮每类清理最多 64 条，轮后等待 10 秒，清理 SQL 各 1 秒超时；保留对局／回放与刷新令牌检测记录。速率计数更新持有行锁，清理跳过竞争行。调大连接数时须同步评估帧、数据库和规则进程预算；P7 整部署 2 vCPU／2 GiB 压测尚未运行，当前参数不能作为并发局数承诺。


## 当前 P6 局域网部署（2026-10-27）

按用户要求，现有专用 API v2 局域网部署已升级为 `ptcg-online-server==0.6.0.dev5`。镜像摘要 `sha256:29ad6123c04a878e0d60c000436187adc38fcabadaa9f99ae6916e9eac462d43`，保留原 HTTPS 地址、CA、服务器身份、规则制品、数据库容器与数据卷；没有新增 migration。上文 P6-A—E 的“未替换”描述对应各阶段隔离验收时点。

基础 Compose 默认镜像更新为 `ptcg-lab-online-api:p6e`，可用 `PTCG_V2_API_IMAGE` 显式指定已验证摘要。该次私有部署继续采用原生 Uvicorn TLS 启动命令，传输并发上限同步从 32 调为 `32 HTTP + 32 WSS + 8 余量 = 72`；API 内部各预算独立执行。保持单 worker、API 1 CPU／768 MiB 与数据库 0.5 CPU／256 MiB，不能据此宣称已完成 P7 容量压测。

替换前保存私有 Compose 配置和可读取目录的 PostgreSQL 自定义格式备份；仅停止／重建 API，数据库未重建。配置的停机宽限为 60 秒，重启后通过 `/readyz`、原会话、原规则身份和既有对局／帧／回执摘要校验。备份、回退配置与原始报告位于忽略目录 `var/p6e/lan-upgrade-20261027/`，其中含受限部署资料，不进入源码归档。

回退时使用该目录的 `before-compose.json`，指定同一 Compose 项目与原项目目录，仅对 `api` 执行 `up -d --no-deps`，保留现有数据库与新增对局。P6 没有 schema 变更，通常无需恢复数据库；恢复备份会丢失备份后提交，只有明确决定恢复数据时才执行。部署功能验证范围与结果见 [P6 实施记录](../../docs/P6多局调度与性能实施记录.md)。

## 规则修复与未结束对局

2026-10-27 的神奇糖果修复补齐注册卡牌类的进化关系，并兼容没有编译 `spec` 的进化宝可梦。规则源码变化会产生新的执行身份；不能把新代码标成旧身份，也不能直接关闭恢复记录的版本检查。

需要保留旧局时，可显式使用 Uvicorn factory `ptcg_online_server.multirelease:create_app`。镜像须保留更新前完整、原样的 `ptcg_core` 包，例如 `/opt/previous-core/ptcg_core`，并设置 `PTCG_ONLINE_V2_PREVIOUS_CORE_DIRECTORY=/opt/previous-core` 和该旧包原有的 `PTCG_ONLINE_V2_PREVIOUS_RULE_RELEASE_ID`。默认规则仍由原有目录、清单摘要与批准执行身份配置。启动分别探测两套实际身份；旧局按恢复记录中的身份进入独立、无凭据的一次性进程，新局只使用默认新版。跨版本计算串行执行，进入旧版前回收默认闲置进程。未列入配置的版本仍拒绝执行。

迁移特定旧局必须另做明确维护：备份该局恢复记录和可见帧；在离线副本逐步核对全部历史状态，按动作内容校准可能变化的动作编号；事务更新该局执行身份和恢复记录，统一回放信封的版本元信息，并同步两端本局缓存。不能改变已发生的场面、事件、命令回执或对局步数。提交了新操作后，不得直接覆盖回迁旧备份。本次私有部署记录和回退脚本在忽略目录 `var/rare-candy-hotfix/`，不进入源码归档。

糖果回归测试使用真实、已批准的本地规则包，在独立进程运行，避免与其他规则测试混合导入：

```sh
PTCG_CANDY_RULE_DIRECTORY=/approved/rules \
PTCG_CANDY_RULE_PIN=<bundle.json的SHA256> \
python tests/core/test_rare_candy.py
```

覆盖旧式多龙进化链、完整结算、伤害和附着卡保留、编译卡牌的进化特性，以及首回合、刚登场、错误目标等限制。配置缺失会跳过，不能记为通过。
