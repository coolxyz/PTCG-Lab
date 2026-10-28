# API Server 人工验收环境

当前为单规则、单 API 实例的私有预览环境，包含实际规则、Python 运行依赖、API 与 PostgreSQL 镜像归档。与源码目录、旧 APP、既有 LAN 数据库无依赖。镜像架构、测试地址、规则身份及文件校验见 `MANIFEST.json`，不代表 P7 容量或正式发布验收通过。

在本目录使用 Python 3.11+ 和 Docker Compose v2：

```sh
python3 manage.py verify
python3 manage.py up
python3 manage.py check
python3 manage.py status
python3 manage.py down
```

`up` 从本目录归档加载并核对镜像，自动启动独立数据库、执行 migration，再启动 HTTPS API，等待 `/readyz`。不会拉取新镜像，也不会读取仓库中的数据库。首次运行可能花费一两分钟。`down` 仅停止本 manifest 的 Compose 项目并保留数据卷，不删除数据库。再次 `up` 使用原数据。不要同时将同一个包当作两个独立服务器；独立部署应重新生成自己的证书、服务器身份和数据库秘密。

服务器也可以只安装 Docker，通过原生命令运行。Compose 内已固定本包的独立项目名，勿自行改成既有服务的项目名：

```sh
docker load --input images/api.tar.gz
docker load --input images/postgres.tar.gz
docker compose -f compose.yaml up -d --wait
docker compose -f compose.yaml down
```

Python 仅用于可选管理器的清单／镜像核对和便捷命令，API 所需 Python 已在镜像内。

默认端口为 8876；实际地址见 `client.json`。API 不提供网页，也不接受浏览器 CORS，使用本地 APP 的原生 API v2 客户端。WebSocket 使用相同 TLS 地址。数据库不发布宿主端口。测试 API 使用 1 CPU／768 MiB，数据库 0.5 CPU／256 MiB；这些预算不是并发容量保证。

Mac 连接：关闭 `PTCG Lab Preview.app`，运行 `configure-mac.command`，再重新打开 APP。此脚本默认使用相邻 `../macos-arm64/PTCG Lab Preview.app` 的随包 Python；APP 已移动时可设置 `PTCG_APP` 为 APP 的路径后运行。它只复制公共 CA 和服务器地址到 preview profile，不导入身份、不修改卡组、不覆盖已有配置。已有 profile 配置会提示人工检查；凭据目录必须保留，不要为更换服务器而删除整个 profile。

测试 TLS CA 和服务端证书有效期 30 天，绑定构建时指定的主机／IP；仅本 APP 使用提供的公共 CA，不要求导入系统根证书。更换地址或到期时用打包脚本生成新候选环境。此包含私有数据库密码、测试 CA 私钥与 TLS 私钥，整个目录以 0700 生成，不能上传为公开 release；只分享 APP ZIP 和公共 CA／连接信息。TLS 文件在容器挂载目录内对 UID 10001 可读，宿主访问由外层私有目录保护。

当前 `linux/arm64` 镜像适用于相同架构 Docker 主机；x86_64 服务器请用 `--platform linux/amd64` 重新打包，不把模拟运行当作原生架构验收。规则更新不保留旧核心，不要求旧规则续局；只保存历史可见帧与回执。

正式发布仍需完成签名／公证、许可／SBOM、资源授权审查、干净系统、备份恢复、Windows 和 P7 容量验收。该目录的清单是完整性检查，不是数字签名。请勿运行全局 Docker prune 或对其他 Compose 项目执行删除操作。
