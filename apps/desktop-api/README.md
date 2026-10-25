# ptcg-desktop-api

P5-C 可选本地网络宿主，版本 `0.5.0.dev3`。提供独立 GUI、可信启动和图片缓存，保留 P5-B 桥接 API。尚未挂载到现有产品入口，不启动人机、AI、SQLite、规则引擎或远程服务器。

Python 3.14；依赖 `ptcg-online-client==0.5.0.dev2`、Starlette 和 Uvicorn，完整外部闭包见 `requirements.lock`。凭据存储继承原生客户端的 POSIX 权限文件限制，目前不支持 Windows 后端。

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

ASGI lifespan 会关闭 owner。原生宿主也可调用 `owner.close()`：默认最多等待 20 秒，若仍有后台操作，返回 `SHUTDOWN_PENDING` 并保留相关凭据锁，待操作结束后重试关闭。不要绕过锁再启动另一个 owner。`IMPLEMENTATION_READY=False` 表示整个本地网络产品仍待 P5-D 两台设备完整验收。

## P5-C 本地网络窗口（0.5.0.dev3）

现已提供独立启动器、同源 GUI 宿主和按哈希缓存的显示资源；旧产品入口保持不变。先在仓库根执行 `npm run p5:build`，再使用安装包的解释器启动：

```sh
python -m ptcg_desktop_api.launch --config /absolute/path/online.json \
  --state-dir /absolute/path/private-online-state \
  --build /absolute/path/PTCG-Lab/var/p5c/web-dist
```

默认使用随机回环端口并打开浏览器。`--no-browser` 仅输出一次性启动文件路径，用户可在两分钟内打开；`--port` 可选固定专用端口，拒绝占用既有 8765/8770/8443。不要直接打开根地址期待自动授权。示例配置（地址和 CA 必须由用户明确配置）：

```json
{"servers":{"my-server":{"origin":"https://battle.example.org"}},"manifests":[]}
```

私有 CA 使用 Server 的 `ca_file` 字段；浏览器只选择已配置的服务器。启动文件以 0600 保存，内含一次性 POST ticket，URL 中不携带秘密。后台将它兑换成 HttpOnly/SameSite=Strict 本机会话，再向同源页面签发只留在内存中的窗口 capability。页面刷新会轮换 capability，远程凭据始终在 Python 后台。P5-B 的纯 API 工厂仍可单独使用。

显示资源清单通过配置 `manifests: [{"path":"/absolute/manifest.json","sha256":"完整 SHA-256"}]` 明确批准。清单严格包含 `catalogVersion`、`assetVersion`、`cards`；卡牌包含 `engineId`、`englishName`、`cnName`、`image`，image 为 null 或 `{sha256,url}`。`url` 为 null 时只读清单目录下的 `files/<sha256>`，也可明确配置 HTTPS 图源。不读取既有收藏或图片目录，不批量上传图片。详见 [P5-C 验收与运行说明](../../docs/P5C本地网络窗口与资源缓存验收.md)。
