# ptcg-desktop-api

P8-A 本地宿主，版本 `0.8.0.dev0`。支持独立网络窗口、产品 ASGI 单端口组合宿主及继承管道控制的原生后台。包本身不导入 SQLite、规则或人机模块，由产品装配入口显式注入；原浏览器启动流程保持兼容。

Python 3.14；依赖 `ptcg-online-client==0.5.0.dev6`、Starlette 和 Uvicorn，完整外部闭包见 `requirements.lock`。P8 macOS 验证另锁定随包闭包。凭据存储仍继承原生客户端的 POSIX 权限文件限制，Keychain 和 Windows 后端尚待实现，不能据此标记 P8-A 或跨平台发布完成。

```python
from pathlib import Path
from ptcg_online_client.transport import Server
from ptcg_desktop_api.bridge import Bridge
from ptcg_desktop_api.api import create_app

owner = Bridge(
    {"my-server": Server("https://battle.example.org")},
    Path.home() / ".ptcg-lab-online",
)
app = create_app(owner, origin="http://127.0.0.1:18785")
window_capability = owner.issue_window()
# 原生宿主将 window_capability 交给它启动的可信窗口；不打印或写入 URL。
# 宿主启动 Uvicorn 时仅绑定 127.0.0.1:18785，使用单 worker，关闭自动 reload、proxy_headers 和 access_log。
# 此为嵌入式 P5-B API 用法；独立同源 GUI 使用文末 P5-C 启动器。
```

窗口只调用固定的 `POST /api/desktop/online/v1/profiles/{profile}/{operation}`，设置 `Origin`、`Content-Type: application/json` 和 `X-PTCG-Bridge`。原生宿主须在同一 origin 提供 GUI，通过可信启动通道注入短期本机 capability。远程 access/refresh token 永不下发；capability 也不可存入 URL、日志或持久浏览器存储。

两个全局 POST：`/api/desktop/online/v1/profiles` 返回配置摘要，`/api/desktop/online/v1/close-window` 撤销当前窗口授权。请求体均为 `{}`。其余路由、请求体、恢复状态及容量限制见 [P5-B 验收文档](../../docs/P5B本地网络桥接验收.md)。没有“任意 URL 请求”、读取凭据、任意文件路径、自动刷新 token、删除身份或改变服务器配置的浏览器接口。

窗口取得 `control` 返回的本机 lease 后才能 `command/resign`；另一个窗口接管会废止旧 lease。`recover` 可核对并恢复原待确认请求，不允许构造新请求。`watch/events/unwatch` 共享后台 WSS，`events` 读取最新缓存，无远程轮询。每 60 秒必须有窗口续期，否则停止无人消费的流；离开页面不等于认输。

ASGI lifespan 会关闭 owner。原生宿主也可调用 `owner.close()`：默认最多等待 20 秒，若仍有后台操作，返回 `SHUTDOWN_PENDING` 并保留相关凭据锁，待操作结束后重试关闭。不要绕过锁再启动另一个 owner。`IMPLEMENTATION_READY=False` 表示完整产品整合与发布尚未完成；P5 独立窗口验收已由用户确认，P5.5 完整产品仍须单独验收。

## P5-C 本地网络窗口（0.5.0.dev3）

现已提供独立启动器、同源 GUI 宿主和按哈希缓存的显示资源；旧产品入口保持不变。先在仓库根执行 `npm run p5:build`，再使用安装包的解释器启动：

```sh
python -m ptcg_desktop_api.launch --config /absolute/path/online.json \
  --state-dir /absolute/path/private-online-state \
  --build /absolute/path/PTCG-Lab/var/p5c/web-dist
```

默认使用随机回环端口并打开浏览器。`--no-browser` 输出一次性启动文件路径和同浏览器重新打开的本机地址，用户须在两分钟内完成首次授权；`--port` 可选固定专用端口，拒绝占用既有 8765/8770/8443。不要直接打开根地址期待自动授权。示例配置（地址和 CA 必须由用户明确配置）：

```json
{"servers":{"my-server":{"origin":"https://battle.example.org"}},"manifests":[]}
```

私有 CA 使用 Server 的 `ca_file` 字段；浏览器只选择已配置的服务器。启动文件以 0600 保存，内含一次性 POST ticket，URL 中不携带秘密。后台将它兑换成 HttpOnly/SameSite=Strict 本机会话，再向同源页面签发只留在内存中的窗口 capability。页面刷新会轮换 capability，远程凭据始终在 Python 后台。P5-B 的纯 API 工厂仍可单独使用。

显示资源清单通过配置 `manifests: [{"path":"/absolute/manifest.json","sha256":"完整 SHA-256"}]` 明确批准。清单严格包含 `catalogVersion`、`assetVersion`、`cards`；卡牌包含 `engineId`、`englishName`、`cnName`、`image`，image 为 null 或 `{sha256,url}`。`url` 为 null 时只读清单目录下的 `files/<sha256>`，也可明确配置 HTTPS 图源。不读取既有收藏或图片目录，不批量上传图片。详见 [P5-C 验收与运行说明](../../docs/P5C本地网络窗口与资源缓存验收.md)。

P5-D 修复 HTTP 快照晚于新推送返回时的时序竞争：先验证身份，再保留持久化的较新快照，避免初始订阅因旧响应退出。原生客户端版本为 `0.5.0.dev3`，桥接依赖版本随之推进到 `0.5.0.dev4`，权限和命令幂等规则保持不变。

P5-D 窗口重开修复（`0.5.0.dev5`）：关闭浏览器标签页后，若后台仍运行且本机会话有效，可在同一浏览器打开终端显示的本机地址。一次性 HTML 在使用后删除，不能重复使用；过期、清除浏览器数据或改用另一浏览器时，在交互式后台终端输入 `r` 并回车，生成新的两分钟授权 HTML，不重启后台、不重建远程游客身份。`--no-browser` 模式仍由用户手动打开；没有交互终端时可在有效会话中重开本机地址，授权失效后须用原 state-dir 重启自己的客户端后台。

## P5.5-A 单宿主基础

`create_product_app(local_app, online_host)` 在同一监听端口提供产品页面/API、`/online` 页面和原生网络桥接，并统一启动/退出生命周期。旧本地 API 的修改请求增加精确 Origin 校验；无 Origin 的写入不能通过宽松旧路由绕过。在线授权、远端令牌和窗口能力边界沿用原实现。

本仓库通过 `apps/api/product.py` 装配现有产品应用；`scripts/start.py --online-config ... --online-state ... --online-build ...` 只启动一个 HTTP 进程。参见 [实施记录](../../docs/P5.5产品整合实施记录.md)。统一产品现在通过 `/#online` 进入网络大厅，使用平台已有卡组及冻结版本、卡表和 CardMedia。desktop-api 版本为 `0.5.0.dev7`；独立测试包未自动升级。


统一产品首次启动在终端显示五分钟、一次性的本机授权码，浏览器网络大厅输入后换取 HttpOnly 会话。不需要授权 HTML；授权码不写入 URL 或浏览器存储。后台终端 `r` 生成新码；同一浏览器会话有效时直接重开产品地址。也可以使用同一配置和状态目录，在启动命令后追加 `--reopen`，通过私有 Unix socket 请求已有实例重新打开；不会启动第二个 HTTP 服务。Mac/Linux/WSL2 支持该路径，原生 Windows 仍未支持。状态目录应使用较短的本机路径（Unix socket 路径长度受平台限制）。

`bash start.sh --setup --online-config /absolute/online.json --port 18789` 一次安装产品与原生客户端依赖、构建两种前端，再启动一个 APP。`npm run product:build` 可单独构建产品与诊断页面。已有服务不会自动升级或重启。B/C 的剩余验收和迁移备份说明见实施记录。
