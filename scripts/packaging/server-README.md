# 独立 API Server 部署包

本包包含当前批准规则、Python 运行依赖、API/PostgreSQL 镜像归档、Compose 与私有数据库配置，无需源码、桌面 GUI 或卡图目录。`MANIFEST.json` 记录 origin、serverId、规则、架构及文件/镜像哈希；它证明完整性，不证明来源真实性或并发容量。

目标机需要匹配架构的 Docker/Compose v2；便捷管理器另需 Python 3.11+：

```sh
python3 manage.py verify
python3 manage.py up
python3 manage.py check
python3 manage.py status
python3 manage.py down
```

`up` 从归档加载并核对固定镜像，启动独立数据库、执行 migration，再启动 API。`down` 只停止本 manifest 的项目，保留所有数据卷。不要修改成既有服务的项目名，不执行全局 prune 或 `down --volumes`。API 只提供 API v2/WSS，不提供网页，不允许浏览器 CORS。

## 两种 TLS 模式

检查 MANIFEST 的 `tlsMode` 和 `client.json`：

- `local-ca`：API 直接提供 HTTPS，默认 8876；提供 30 天测试 CA/TLS，客户端只导入同批公开 `tls/ca.pem`，不导入私钥，不关闭验证。
- `acme`：Caddy 提供公网 HTTPS/WSS 与自动 ACME 续期，默认 TCP 80/443；API、数据库、管理口均不对外。必须提前配置正确的公网 DNS、防火墙/NAT 和出站网络。签发完成后运行 `python3 manage.py check-public`，验证系统信任链与公网 `/readyz`。内部 `check` 或 Caddy 健康通过不代表公网证书已就绪。

`acmeStaging=true` 是测试 CA，不被系统信任。生产部署应重新构建去掉 staging 的私有 kit。保留 Caddy 的 `caddy_data`/`caddy_config` 卷，不要反复删除并重签；数据库位于 `database` 卷。续期不要求重启 API。公开端口冲突或私网 `--proxy-subnet` 冲突时，重新规划并构建，不覆盖其他服务。

只有 Docker 时，分别 `docker load --input images/api.tar.gz`、`images/postgres.tar.gz`；ACME 模式还需加载 `images/caddy.tar.gz`。然后执行 `docker compose -f compose.yaml up -d --wait`。

## App 配置

公网模式 `caFile=null`，App 使用系统信任，只设置 origin。局域网模式额外配置公开 CA。Mac 关闭 App 后可运行 `configure-mac.command`；默认解释器在相邻 `../macos-arm64/PTCG Lab Preview.app`，移动后设置 `PTCG_APP` 路径。助手拒绝覆盖已有配置，不改凭据、卡组或收藏。不要为换服务器而删除 profile。

## 备份与恢复

```sh
python3 manage.py backup --output /secure/backups/new-backup
```

输出含秘密和私有对局，用 0700/0600 权限保管并另行加密离线保存，独立保留 BACKUP.json 的可信 SHA-256。恢复 kit 必须同 origin/serverId/当前规则，数据库为空且 API 停止：

```sh
python3 manage.py load
python3 manage.py restore --backup /secure/backups/new-backup --backup-sha256 <可信SHA256>
python3 manage.py up
python3 manage.py check
```

ACME 模式补充 `check-public`。工具拒绝非空库，不覆盖既有资料；原 kit 与卷继续保留。数据库备份不含 Caddy 证书卷，迁移时另行私有保留或重新签发。恢复后用原身份核对当前局、回放和重复命令回执，再开放流量。

## 预算与交付边界

API 1 CPU/768 MiB、数据库 0.5 CPU/256 MiB；公网 Caddy 另有 0.5 CPU/192 MiB，宿主还需系统余量。worker 默认 384 MiB 回收阈值，避免当前规则正常恢复即超过旧 256 MiB；它不是总内存或并发容量保证。不要直接 scale API，多局由单 API 内按 match 串行和 worker 有界调度。

本目录包含数据库密码；局域网包还含测试 CA/TLS 私钥，公网运行卷含 ACME 私钥。整个目录不可公开发布，只分享批准的 App 与公开 origin/CA。桌面开源分发无需维护者代码签名；iOS TestFlight 另行处理。规则只执行当前批准版本，身份、哈希、鉴权与幂等不放宽；不提供旧规则续局。更新前备份，回退按同一截点的整套镜像、配置和数据库操作。
