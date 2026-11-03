# PTCG Lab API 离线部署

包含当前规则和运行依赖的 API、PostgreSQL 17、Caddy 镜像归档。目标主机只需 Linux Docker Engine、Compose v2 和 sha256sum（或 shasum），无需 Python/Node/Git。镜像从本包导入，启动时 `pull_policy: never`。选择匹配 CPU 的 linux-amd64 或 linux-arm64 包；amd64 不适用于 ARM 服务器。

## 安装并启动

将 tar.gz 及其 .sha256 复制到服务器，在可信目录中校验、解压：

```sh
sha256sum --check <包名>.tar.gz.sha256
tar --no-same-owner -xzf <包名>.tar.gz
cd <包名>
./server.sh verify
./server.sh setup --host api.example.com --email operator@example.com
./server.sh start
./server.sh check
./server.sh check-public
```

替换域名与邮箱。`setup` 自动离线加载镜像，使用镜像里的 Python 在无网络容器中生成全新 serverId、独立 Compose 项目及数据库随机密码，保存在 `state/deployment/`。它不启动服务，也不覆盖已有配置。`start` 启动数据库、迁移、API、Caddy，等待内部就绪；后续启动会复用原身份和数据库。不要将同一份已配置的 state 同时作为两个服务器运行。

可以先 `./server.sh load` 单独导入镜像。脚本会核对 SHA-256、镜像 ID 与架构，已有正确镜像时跳过导入；不会拉取镜像或构建。`PTCG_ALLOW_EMULATION=1` 仅用于维护者的跨架构模拟验收，生产使用匹配架构。

## 域名、证书及 App

公网域名 A/AAAA 必须正确指向服务器，TCP 80/443 可达，且允许访问 ACME 服务；离线导入镜像不表示自动证书可在断网环境中签发。Caddy 自动申请/续期 Let's Encrypt 证书，只发布网关端口。API、PostgreSQL、Caddy 管理端口均不暴露公网。

`check` 只核验内部 API；生产证书签发后 `check-public` 通过系统 CA 验证 HTTPS `/readyz`。staging 证书不被系统信任，检查失败是预期。可在独立测试安装中给 `setup` 加 `--staging`；正式安装选择新目录重新 setup，不将测试 CA 导入日常 App。

App 使用 `state/deployment/client.json` 的 HTTPS origin，无需填写额外 CA。仅分享 origin/该文件，不分享 private/ 或完整 state。

其他 setup 参数：`--port 443 --http-port 80 --bind 0.0.0.0 --proxy-subnet 172.30.80.0/28`。同机多个安装须使用不同端口和不冲突的私网；公网 ACME 验证仍需标准 80/443 正确路由，不是任意高端口即可签发。

## 日常维护

```sh
./server.sh status
./server.sh logs
./server.sh stop
./server.sh start
```

`stop` 只停止本安装的项目并保留全部卷；不执行全局清理或删除数据库。服务数据位于本项目的 database、caddy_data、caddy_config 卷，state 保存密码/身份/配置。备份必须包含一致性数据库快照、配置和必要的证书状态；只打包 state 不包含数据库。不要改项目名或删卷来“升级”。

可在本目录用 `docker compose --project-directory state/deployment -f state/deployment/compose.yaml` 操作同一项目。数据库备份例子（输出位置由操作者事先选定、设为私有，文件必须是新文件）：

```sh
(umask 077; set -C; docker compose --project-directory state/deployment -f state/deployment/compose.yaml exec -T database pg_dump -U ptcg_v2 -d ptcg_v2 --format=custom --no-owner --no-privileges > /secure/new-backup.dump)
```

归档不含用户凭据，setup 后 state 含秘密，须私有保存。默认单 API/单 worker，worker RSS 阈值 384 MiB；容器上限约 API 768 MiB、PG 256 MiB、Caddy 192 MiB，宿主还需系统余量，不承诺固定并发容量。规则只执行当前批准版本，更新规则不提供旧规则续局；升级/回退需对应整套镜像、数据库和身份计划。
