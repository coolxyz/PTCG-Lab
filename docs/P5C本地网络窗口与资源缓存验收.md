# P5-C 本地网络窗口与资源缓存验收

更新：2026-10-25。P5-B 已提交为 `a61cbf3`。本阶段实现可选本地网络窗口和显示资源缓存，不替换正在运行的人机、局域网 v1 或 AI 服务。P5 整体仍待 P5-D 两台设备完整验收。

## 1. 交付

- `ptcg-desktop-api==0.5.0.dev3`：一次性文件启动、同源网页宿主、本机会话、版本资源目录和哈希图像缓存，保留 P5-B API 工厂。
- `@ptcg/desktop-web==0.5.0-p5c`：独立网络入口、服务器选择、游客、卡组清单、邀请/准备、Arena、控制权、待确认操作恢复与私有回放。
- `ptcg-online-client==0.5.0.dev2`、远端 API、共享核心/Provider/协议、共享 BattleArena 和旧人机代码保持原版本。

网络窗口使用共享 `BattleArena`、`snapshotView` 和 `replayView`，不在 GUI 重建规则，不交换 PLAYER1/PLAYER2 字段。Python owner 持有远端认证和 WSS，浏览器只请求本机。`npm run p5:build` 输出到 `var/p5c/web-dist`；旧构建与运行入口没有覆盖。

## 2. 安全启动与刷新

原生启动器只绑定 `127.0.0.1`，默认随机端口；禁止使用已有 8765、8770、8443。启动器使用单进程，不启用代理头和访问日志，也不发现或停止其他进程。服务器地址及 CA 来自明确指定的本机配置文件，浏览器不能配置任意 URL 或本地路径。

1. 启动器在私有目录生成 0600 HTML 文件，包含随机一次性 ticket，有效期两分钟。
2. 浏览器打开该文件，以表单 POST 把 ticket 交给本机 `/launch`。只接受精确 Host、回环 peer、file 页面产生的 `Origin: null`、指定编码和有效未消费 ticket。普通对战 API 从不接受 null Origin。
3. ticket 消费即失效并删除文件。后台设置 HttpOnly、SameSite=Strict 本机 Cookie，303 跳转 `/`。URL 不含 ticket、Cookie 或 capability。
4. 同源页面 POST `/session`，携带本机 Cookie 和非秘密 windowId，换取仅保留在 JS 内存中的窗口 capability。该接口要求精确 Origin 与 JSON，不提供 CORS。
5. 对战请求携带 capability 并显式省略 Cookie，继续走 P5-B 严格保护。远端 access/refresh token 不进入浏览器。刷新页面重新签发并废止同 windowId 旧 capability；操作权需重新取得。

从 file 页面跳转后 Chromium 会把首页导航标记为跨站。宿主只允许顶层 document 导航打开**无秘密的静态首页**；iframe、会话签发、资源请求与对战 API 仍按各自边界拒绝跨站访问。首页 CSP 禁止嵌入、外部脚本、外部连接和对象，资源不允许脚本类 SVG。

本机会话/窗口有效期八小时，最多八个会话/窗口；ticket 至多八个。已失效 ticket 在下一次访问/签发或退出时清理。临时桌面宿主路由最多八个在途请求，图片最多四个，并保留 P5-B 的四请求桥接限制。页面关闭只撤销本机窗口并停止其独占流，不认输。窗口重复复制若继承了同一 sessionStorage windowId，新页会废止旧页授权；推荐使用页面中的 noopener 新窗口入口。

## 3. GUI 状态与恢复

卡组输入为 printingId/quantity JSON 数组，只上传清单；不读取原收藏数据库。规则版本由 capabilities 提供，换规则清空旧 revision。当前阶段不自动把旧 APP 的私有卡组数据库迁入新窗口。

卡组提交、创建/加入房间、准备/离开均先在窗口 sessionStorage 保存原 requestId 和请求内容，再发送。响应丢失后保留“重试大厅操作”，刷新页面沿用原请求。首次得到明确的输入/版本拒绝可解除该请求；未知结果不自动生成新 ID。关闭标签页丢失 sessionStorage 的大厅恢复能力，仍可用已知对局编号打开本人对局；原生待确认命令则持久保存在后台，不依赖标签页。

双方准备后显示初始化状态。房间的 matchId 可先于权威 MatchSnapshot 出现：界面只在房间就绪后尝试进入，并对该已授权房间的短暂 FORBIDDEN 做有界等待；任意手工输入的对局编号不放宽权限。开局失败或权限错误保留明确反馈，不通过新建对局掩盖失败。

进入 Arena 默认只读，显式“取得操作权”后才开放操作。窗口 lease 与远端 controllerEpoch 共同保护提交；收到控制 epoch 更新时清除旧 lease。命令带原 matchSeq/stateVersion/decisionId，结果未知时恢复后台原请求，禁止另发命令。刷新重连时若旧流正在关闭，最多等待约三秒再尝试订阅，不无限创建流。

UI 轮询的是本机最新事件缓存（约 600ms），远端仍每局一条 WSS。房间约 1.5 秒刷新一次，忙时不叠加请求，出错暂停自动刷新并提供重试。离开 Arena 不认输；认输需要共享界面的二次确认。

本人回放通过服务器分页读取，使用共享只读适配器，不把历史 decision 变成可操作按钮。当前窗口至多载入约 300 帧，并提供“加载更多回放”、逐步与播放控制；完整长局的虚拟化/磁盘回放库不在本轮。PLAYER2 的座位、胜负及本人视角直接沿用服务数据。

## 4. 本地资源与缓存

清单只用于显示，必须由原生配置给出文件路径与完整 SHA-256，内容变化时 pin 必须变化。每份最多 4 MiB / 10,000 张卡，CLI 最多加载八份。格式示例：

```json
{
  "catalogVersion": "与服务 capabilities 对应的目录版本",
  "assetVersion": "本地资源版本",
  "cards": [{
    "engineId": "显示用规则卡牌标识",
    "englishName": "Example Card",
    "cnName": "示例卡牌",
    "image": null
  }]
}
```

image 也可为 `{ "sha256": "64 位内容摘要", "url": null }`，从清单同级 `files/<sha256>` 读取；或明确指定固定 HTTPS URL。只读取声明的哈希对象，不遍历/上传原用户图片目录。HTTPS 下载启用系统证书验证，不带远程对战认证，不使用环境代理，不跟随跳转，不接受压缩体；单图最大 2 MiB、每次 socket I/O 超时五秒。与 P5-A 一样，该 I/O 超时并非整次网络读取的绝对时限。

图片支持 PNG/JPEG/WebP，保存前验证 SHA-256 与签名；不允许 SVG/HTML。缓存目录权限 0700，文件以临时写入再原子替换，读取也核对哈希。相同哈希直接命中本机，离线图源仍可显示已缓存图。新版本只补充新哈希，不删除旧 blob 或旧版本元数据；默认总上限 256 MiB，空间不足时缺图降级，不自动删除正在使用的旧资源。

显示目录匹配远端 `displayCatalogVersion`；若远端提供明确 assetManifestVersion，还须精确匹配。当前 P4 服务返回 `assetManifestVersion=unavailable`，因此只能从本机明确批准的同 catalogVersion 清单中选择最后配置的版本，不能据此声称服务端已经分发图包。无匹配目录、网络失败、图缺失或容量耗尽都回退卡名/文字，对战提交不依赖资源成功。

资源响应通过本机 `/resources/<sha256>` 提供，限制 same-origin，设置 CORP、nosniff 和私有 immutable 缓存。浏览器不访问远端对战地址或图源，纯 API 对战服务的流量不含图片。完整资源分发站、自动远程清单更新及图片授权分发不在本轮；增量更新通过明确批准新清单实现。

## 5. 验收

P5-C 已完成本地验收。证据位于忽略目录 `var/p5c/`，真实卡组、截图和临时凭据不进入公开发布物。

| 验收 | 结果与证据 |
| --- | --- |
| 原生客户端、桥接、宿主单元与真实集成 | **71 passed**，`acceptance-final/results.xml`；7 条上游弃用警告，无跳过 |
| 两个本地浏览器上下文与真实 TLS 规则服务 | 邀请、PLAYER2、真实命令、全屏进入/退出、刷新接管、认输、只读回放通过；`acceptance-final/browser-report.json`，浏览器远程请求 0，页面错误 0 |
| 网络控制器恢复与隔离 | 四个场景通过，`controller-final.log` |
| 既有人机与共享界面回归 | **30 passed**，`p3-browser-final.log` |
| 核心、Provider、协议、迁移与导出回归 | **232 passed**，`contracts.log` |
| 网络独立构建 | TypeScript 与 Vite 通过，`build-web-final.log` |
| 发布门禁与离线包消费 | 边界、冻结代码、源码/归档扫描、协议生成、wheel 构建与安装通过，`build-final.log`；三个 npm 包在仓库外消费通过，`npm-final.log` |
| 现有服务只读检查 | 人机 8765、局域网 HTTP 8770、HTTPS 8443 均为 200，`service-health.json`；未重启或修改服务 |

已复查 1440px 对战界面和 900px 回放截图；回放明确显示“只读回放”。真实 GUI 测试使用缺图文字降级；图像下载、哈希校验、离线命中和版本保留由资源层测试验证，完整 GUI 图源离线验收留到 P5-D。隔离 PostgreSQL 已由 helper 回收，临时凭据文件已删除。

目前已覆盖启动文件单次消费/过期、Host/Origin/本机会话边界、刷新授权轮换、跨站嵌入拒绝、固定静态路径、缓存哈希/版本/离线命中/跳转/容量/符号链接、控制器原大厅请求恢复、未知命令阻塞、只读回放和服务器切换隔离。

真实浏览器使用两个独立回环宿主、同一个隔离 HTTPS/WSS 服务、临时 PostgreSQL 与已批准真实规则输入。已验证邀请、双方准备、真实 GUI 命令、PLAYER2 显示、全屏、刷新重连、认输与本人回放。它仍是**同一台机器上的两个浏览器上下文**，不替代 P5-D 两台物理设备与自然终局验收。

## 6. 运行与回退

```sh
npm run p5:build
python -m ptcg_desktop_api.launch --config /absolute/online.json \
  --state-dir /absolute/private-online-state --build /absolute/PTCG-Lab/var/p5c/web-dist
```

配置样例：`{"servers":{"lan":{"origin":"https://battle.example.org"}},"manifests":[]}`。私有 CA 使用 `ca_file` 字段，不能关闭证书验证。状态目录须私有；默认打开浏览器，或加 `--no-browser` 手动打开输出的启动文件。

退出独立宿主即停用新增入口，不影响旧 APP、旧局域网服务或 AI 进程。保留 state-dir 可保留远端身份、待确认命令和图片缓存；不要为回退删除该目录。POSIX 明文权限凭据和 Windows 后端限制继承 P5-A/B；本阶段未发布跨平台安装器。

下一阶段 P5-D 完成两台设备从本地窗口自然终局、不同座位、故障恢复、图源离线、缓存命中及人机隔离的完整验收，再决定是否切换产品默认入口。
