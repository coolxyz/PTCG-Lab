# API 公网部署与自动证书

服务仅提供 API v2 与 WSS，规则和运行依赖随 Docker 镜像交付；GUI、卡组编辑、收藏、卡图及人机练习在 App 本地。一个服务可承载多个独立房间，对局状态、权限、序列和回执按 match 隔离。

## 交付形态

- `local-ca`：单机/局域网验证，API 自带 30 天测试证书，客户端额外配置同批公开 CA。
- `acme`：公网域名由 Caddy 自动申请和续期 Let's Encrypt 证书。仅 Caddy 发布 TCP 80/443，API 和 PostgreSQL 在 Docker 私网；客户端使用系统根证书，不导入测试 CA。

这里的自动签发是服务器 TLS 证书，与桌面程序的代码签名无关。桌面开源源码由用户自行编译，不要求 Developer ID、公证或 Authenticode；iOS TestFlight 分发使用另外的 Apple 签名流程。

当前交付是单主机、单 API 进程、默认一个规则 worker 的有界方案，不支持通过简单复制 API 容器获得多副本一致性。不要使用 `--scale api`。容量需按当前镜像、规则、主机和网络实测；本机双局功能通过不代表并发容量已达标。

## 1. 公网主机准备

准备自己管理的域名、ACME 联系邮箱、Linux Docker/Compose v2 主机和批准的当前规则 bundle。选择 `linux/arm64` 或 `linux/amd64`，与服务器 CPU 对应。构建机另需开发 Python 3.14+；目标主机可仅装 Docker，便捷管理器需 Python 3.11+。

1. 域名 A 记录指向服务器公网 IPv4。若配置 AAAA，必须同样可达；错误的 IPv6 记录会影响验证。
2. 安全组/防火墙及 NAT 放行 TCP 80、443；现有其他网关占用时先规划统一入口。不要把 PostgreSQL、API 8784 或 Caddy 管理口 2019 映射到公网。
3. 允许主机正常 DNS、时间同步和到 ACME 服务的出站 HTTPS。默认使用 HTTP-01/TLS-ALPN-01，验证方访问的是标准 80/443；仅修改宿主高端口不能替代公网端口转发。本工具不自动配置 DNS-01 或 Cloudflare 代理。
4. `--proxy-subnet` 默认 `172.30.80.0/28`，须与主机、VPN、已有 Docker 网段不同。每个同机独立 kit 使用不同网段。
5. 首次先用 staging 演练；它的证书不被系统信任，不要给日常 App 导入 staging CA 或关闭验证。

参考 [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)。

## 2. 生成私有部署目录

从项目根目录执行，替换所有占位值；每次选择全新输出目录：

```sh
python3 -B scripts/packaging/api_server.py \
  --rules /approved/current-rule-bundle \
  --rule-pin <bundle.json的可信SHA256> \
  --tls-mode acme --host api.example.com --email operator@example.com \
  --bind 0.0.0.0 --port 443 --http-port 80 \
  --platform linux/amd64 \
  --acme-staging \
  --output release/api-staging
```

生产包去掉 `--acme-staging`，使用新输出目录。不要编辑清单再绕过核对。可使用 `--base-image`、`--postgres-image`、`--caddy-image` 指定已批准的对应架构镜像；基础 API 的实际源码必须与当前 checkout 匹配。工具核验规则 pin、镜像身份/架构及 PostgreSQL 17，在镜像中激活规则，并归档固定 ID 的镜像。构建不会启动服务、修改 DNS 或发布公网。

目录含 `compose.yaml`、`Caddyfile`、`images/*.tar.gz`、`private/*.env`、`MANIFEST.json`、`client.json`、`manage.py`、`server_backup.py` 及客户端配置助手。整个 kit 包含数据库秘密，应以私有通道交付，不提交 Git、不上传公开 Release；卡图和用户 profile 不进入镜像。`MANIFEST.json` 提供完整性核对，不替代可信来源或数字签名。

公网 `client.json` 的 `caFile` 为 `null`；局域网 `local-ca` 仍为 `tls/ca.pem`。App 只需要 origin 和对应的信任配置，不能把私有 kit 全部交给对战用户。

## 3. 启动和确认

安全复制完整 kit 到目标主机后：

```sh
cd /srv/ptcg-api
python3 manage.py verify
python3 manage.py up
python3 manage.py check
python3 manage.py check-public
python3 manage.py status
```

`up` 加载归档并核对镜像，启动独立 PostgreSQL、执行迁移、启动 API 和 Caddy。`check` 只验证内部 readiness；证书签发完成后，`check-public` 必须通过系统根验证公网域名的 `/readyz`，才算公网入口可用。staging 下该命令因不可信链失败是预期行为。

只有 Docker 时，依次 `docker load --input images/api.tar.gz`、`images/postgres.tar.gz`、`images/caddy.tar.gz`，再 `docker compose -f compose.yaml up -d --wait`。使用 `docker compose -f compose.yaml logs --tail 100 caddy` 检查签发结果；不要公开包含网络地址或私有请求的原始日志。

Caddy 的 `caddy_data` 保存 ACME 账号与证书，`caddy_config` 保存运行配置，`database` 保存服务资料；`down` 保留它们。正常续期无需重启 API，也不应中断既有 WSS。不要删除证书卷反复申请，否则可能触发 CA 限额。网关健康不等于证书已经可被公网客户端信任。

客户端 HTTPS origin 与 WSS 使用同一域名；公网证书在系统信任链内时不用填额外 CA。iOS 填写 origin 并保存、建立游客身份；桌面使用网络配置入口/kit 公开配置助手。助手拒绝覆盖已有 profile，服务器切换时保留原凭据和资料，不通过清空 profile 解决连接问题。

## 4. 网络边界与运行预算

网关保留 Authorization、controller、Origin/Cookie 等原始协议语义；浏览器跨域直接访问仍被 API 拒绝。Caddy 重写 Host 为配置的 origin，覆盖客户端伪造的 X-Forwarded-For/Proto；Uvicorn 只信任私网内的固定 Caddy IP，不能设置为 `*`。PostgreSQL 和 migration 仅在后端网，API 不能从公网绕过网关直连。参考 [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) 和 [Uvicorn 代理参数](https://uvicorn.dev/settings/#http)。

默认资源上限：API 1 CPU/768 MiB、PostgreSQL 0.5 CPU/256 MiB、Caddy 0.5 CPU/192 MiB。它们合计约 1.2 GiB 容器内存上限，宿主还需 Docker、系统、缓存等余量，不能据此承诺 1 GiB 主机可运行。建议先从至少 2 GiB 内存的单机做目标负载验证。

当前规则正常恢复峰值约 279 MiB，worker 原 256 MiB 回收线会导致反复冷启动。默认已改为 `PTCG_ONLINE_V2_WORKER_RSS_MB=384`；这是回收阈值，不是容器硬限额。保持 worker 1、缓存 8、空闲 60 秒的起始配置，根据当前规则实测决定调整；开启双 worker 前需同时验证总内存和尾延迟。不要为提升吞吐取消 per-match 串行、限流、鉴权、幂等或持久化确认。

定期观察 `/readyz`、容器重启/OOM、CPU/RSS、worker 回收/缓存、命令排队/拒绝、数据库连接等待和存储空间；证书告警应检查真实公网链和到期时间。运行统计是容量与故障定位依据，不把首次冷恢复与缓存命中混在一个平均数里。

## 5. 备份与空库恢复

数据库包含游客身份、令牌相关资料、命令/回执、私有对局及历史帧。备份本身也应作为秘密存储；不要放在公开发布目录。

```sh
python3 manage.py backup --output /secure/backups/ptcg-20261103
```

命令在服务运行中使用 PostgreSQL 一致性 `pg_dump`，生成 `database.dump`、`BACKUP.json` 与 `.sha256`，不修改对局。目录权限 0700、数据文件 0600；另行加密并复制到可信离线存储，单独保留 `BACKUP.json` 的可信 SHA-256。只复制同目录 hash 不能证明来源可信。

恢复必须使用同 origin、同 serverId、同当前 ruleReleaseId 的空部署。原 kit/镜像及必要配置应与备份一起私有归档；换主机时可以重新生成相同规则 kit，使用 `--server-id <BACKUP.json中的原值>`，其余域名配置也一致。目标 PostgreSQL 仍为 17；新 kit 可生成新数据库密码，但不能改写备份的规则或用户身份。

先在旧部署停止接收新操作，确保不同时运行两个同身份的服务器；保留旧卷以备明确回退。在新空部署的目录执行：

```sh
python3 manage.py load
python3 manage.py restore \
  --backup /secure/backups/ptcg-20261103 \
  --backup-sha256 <独立保存的BACKUP.json可信SHA256>
python3 manage.py up
python3 manage.py check
python3 manage.py check-public
```

`restore` 只启动数据库，拒绝 API 正在运行、非空库、哈希不符或 origin/serverId/规则不符；在单个事务中恢复，成功后也不自动启动 API。`up` 再核对部署/规则身份。恢复后使用原客户端身份检查当前对局、本人回放和原 commandId 回执，确认后再放流量。若备份之后存在新操作，它们不在快照内，需明确告知操作者数据截点。

同机新 kit 的项目名和卷是全新的，避免与原服务重名；保留旧部署不等于允许双主运行。恢复演练优先使用完全隔离的测试环境，不能为了清空目标而删除已有数据库。

数据库快照不含 Caddy 账号/证书卷。主机迁移时私有迁移 `caddy_data`/`caddy_config` 卷可保持证书状态；卷备份前停本 kit 的 Caddy，保留原卷备份。若不迁移，可在域名指向新主机后由 ACME 重新签发，但需要考虑 CA 限额及签发期间不可用。

## 6. 更新和整套回退

规则只运行当前批准版本，不兼容旧规则继续执行。更新前备份数据库、原 kit 和批准镜像，记录 origin/serverId/规则身份及可信摘要。停止接收操作后停止本项目 API，使用当前源码配套的镜像、规则 pin 和资源；保留部署身份、数据库配置和现有证书卷。迁移/规则检查不通过就保持 API 停止，不能改写旧对局身份或放宽校验。

同规则部署更新后，原身份与对局需重开/重连验证；规则身份变化的旧局可提示重新开局，历史可见帧仍按本人权限只读。新生成 kit 使用新项目与新卷，不会自动采用旧部署数据；采用迁移必须走明确的备份/恢复流程，且本恢复工具拒绝跨规则恢复。跨规则升级的具体迁移计划应按该次发布提供，不能把空库恢复当隐式升级。

回退使用对应快照的整套旧镜像、配置与数据库；不能只降镜像、复用已不兼容数据库。任何回退都明确其数据截点，不加载历史规则 worker 为单局提供兼容。正常停止使用 `python3 manage.py down`，不加 `--volumes`，不执行全局 Docker prune。
