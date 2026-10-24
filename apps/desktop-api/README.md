# ptcg-desktop-api

P5-B 可选本地网络桥接，版本 `0.5.0.dev2`。尚未挂载到现有产品入口，不启动人机、AI、SQLite、规则引擎或远程服务器。

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
# P5-C 将接入同源 GUI 与可信启动流程；本包不提供公开的授权签发端点。
```

窗口只调用固定的 `POST /api/desktop/online/v1/profiles/{profile}/{operation}`，设置 `Origin`、`Content-Type: application/json` 和 `X-PTCG-Bridge`。原生宿主须在同一 origin 提供 GUI，通过可信启动通道注入短期本机 capability。远程 access/refresh token 永不下发；capability 也不可存入 URL、日志或持久浏览器存储。

两个全局 POST：`/api/desktop/online/v1/profiles` 返回配置摘要，`/api/desktop/online/v1/close-window` 撤销当前窗口授权。请求体均为 `{}`。其余路由、请求体、恢复状态及容量限制见 [P5-B 验收文档](../../docs/P5B本地网络桥接验收.md)。没有“任意 URL 请求”、读取凭据、任意文件路径、自动刷新 token、删除身份或改变服务器配置的浏览器接口。

窗口取得 `control` 返回的本机 lease 后才能 `command/resign`；另一个窗口接管会废止旧 lease。`recover` 可核对并恢复原待确认请求，不允许构造新请求。`watch/events/unwatch` 共享后台 WSS，`events` 读取最新缓存，无远程轮询。每 60 秒必须有窗口续期，否则停止无人消费的流；离开页面不等于认输。

ASGI lifespan 会关闭 owner。原生宿主也可调用 `owner.close()`：默认最多等待 20 秒，若仍有后台操作，返回 `SHUTDOWN_PENDING` 并保留相关凭据锁，待操作结束后重试关闭。不要绕过锁再启动另一个 owner。`IMPLEMENTATION_READY=False` 表示整个本地网络产品仍待 P5-C/D 接入与验收。
