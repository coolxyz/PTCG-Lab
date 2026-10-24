# P5-B 本地网络桥接验收

更新：2026-10-25。P5-A 已提交为 `b35174e`。本阶段在 `apps/desktop-api` 实现可选原生 owner 和本机 ASGI 接口，尚未切换现有入口，也尚未提供网络大厅、卡图缓存或双设备 APP 验收。

## 1. 交付与边界

`ptcg-desktop-api==0.5.0.dev2` 仅依赖 `ptcg-online-client==0.5.0.dev2`、Starlette/Uvicorn 及其锁定闭包。客户端小版本增加 `recovery_status()` 与 `recover_control()`，用于返回安全的待确认摘要及恢复未知结果的控制请求；原命令持久化和认证语义保持不变。

本地后台持有唯一客户端 owner；浏览器只拿本机窗口 capability 和当前窗口的控制 lease。服务端、共享规则、Provider、协议、共享 GUI 和既有人机运行代码均未修改。没有连接用户 SQLite，没有 AI advance 调用，没有将本地图片或数据库发送到远端。

导入包不启动任何服务。显式构造 Bridge 才启动一个轻量清理线程；客户端按首次授权操作懒加载，不会因为导入应用而创建远程游客。ASGI 工厂不隐式加载任何用户配置，也不自动挂入当前 FastAPI 应用。

## 2. 原生宿主与浏览器授权

原生宿主显式提供最多八个 `profileId → Server` 配置。Server 仍绑定规范 HTTPS origin 和 CA 内容；浏览器只选 profileId，不能上传地址、CA 路径、请求路径、远程头或 token。重复指向同一个 origin/CA 的别名被拒绝，避免多个别名争夺一个凭据文件。

宿主只绑定 `127.0.0.1`，设置精确的非默认本机端口，使用单 worker，关闭自动 reload、Uvicorn 代理头解析与访问日志。API 对每个请求独立检查：

- TCP peer 必须是回环地址；Host 和 Origin 必须与配置逐字一致，缺失 Origin 不放行。
- 本机 capability 由可信 Python 宿主 `issue_window()` 签发，仅交给可信窗口；HTTP 没有签发、刷新或找回授权的公开接口。
- 拒绝 Cookie、远程 Authorization、Forwarded/X-Forwarded-*、跨站 Fetch Metadata、重复关键头、查询字符串及百分号编码路径。
- 固定 POST + JSON；不提供 CORS，拒绝预检、WebSocket 和其他方法。能力头同时承担不可伪造的本机会话及 CSRF 防护，不能依赖 Origin 单独授权。
- 所有响应 no-store、nosniff，并设置禁止嵌入的 CSP。不将 Python 异常文本、文件路径或远程凭据作为错误返回。

capability 有效期八小时，最多八个窗口。`close-window` 或原生宿主撤销后立即阻止后续操作，停止该窗口独占的订阅；在途请求可能已经提交，因此撤销不冒充回滚。该 capability 是本机临时秘密，不应写入 URL、日志、localStorage 或一般配置。

当前仓库产品仍以本地网页为主，P5-B 提供的是可信宿主的签发与关闭接口；**同源 GUI、窗口启动和安全交付 capability 的实际流程属于 P5-C，尚未接入**。不能通过增加一个无鉴权的 bootstrap HTTP 路由跳过这一步。测试由原生测试宿主直接交付 capability，不声称已完成人工浏览器启动体验。

## 3. 本机 API 合同

前缀 `/api/desktop/online/v1`。全局 `POST /profiles` 和 `POST /close-window` 使用空对象。操作前缀为 `/profiles/{profileId}/{operation}`，下表所有字段之外的额外字段均拒绝；嵌套对战合同继续由共享协议严格校验。

| operation | 请求体 | 返回 result |
| --- | --- | --- |
| status | `{}` | 身份摘要、是否需重新认证、pendingMatches、命令/控制恢复摘要 |
| capabilities | `{}` | 服务能力与规则版本 |
| guest | 可选 name、deviceName | 安全身份元数据 |
| deck | entries、ruleReleaseId、requestId | 冻结 deckRevision |
| create-room | deckRevision、ruleReleaseId、requestId | RoomView |
| join-room | inviteCode、deckRevision、requestId | RoomView |
| room | roomId | RoomView |
| ready / room-deck / leave-room | roomId、expectedRoomVersion、requestId；前两者分别附 ready / deckRevision | RoomView / RoomLeft |
| snapshot | matchId | 本人 MatchSnapshot |
| control | matchId、expectedControllerEpoch | 新本机 lease 与当前快照 |
| recover-control | matchId | 恢复已持久化接管请求后返回新 lease 和快照 |
| command / resign | matchId、lease、command | 服务器原命令回执 |
| recover / resolve-rejected | matchId | `{receipt}`，可以为 null |
| replay | matchId；可选 cursor | 本人 ReplayPage |
| watch / events | matchId | 最新 event、revision、error、done |
| unwatch | matchId | `{watching: false}` |

成功统一 `{result: ...}`；失败统一 `{error: {code, retryable}}`。`PROFILE_BUSY`/`BRIDGE_BUSY` 表示本地繁忙；`LOCAL_CONTROL_REQUIRED` 需重新接管；`PENDING_COMMAND`/`OUTCOME_UNKNOWN` 需恢复原请求；`REAUTHENTICATION_REQUIRED` 不能自动重用旧 refresh。无待确认控制请求的 `recover-control` 返回 `NO_PENDING_CONTROL`。

大厅写入要求调用方传非空 requestId，不自动替换空 ID；重试同一操作时必须保持 ID 与内容一致。持久大厅操作日志和相应 GUI 提示仍待 P5-C，不能在响应丢失后无条件新建请求。

## 4. 多窗口、恢复与推送

同 profile 的前台操作由一把锁串行处理，忙时立即返回冲突，不排积远程请求。多个窗口共享一套远程会话和凭据刷新，不各自打开凭据文件。控制接管成功后生成绑定当前窗口的随机 lease；请求接管前即废止旧 lease，接管回复丢失时也不允许旧窗口继续提交。随后通过安全摘要和 `recover-control` 恢复原请求，不暴露远端 controllerId。

命令请求必须同时匹配窗口和 lease；本地检查成功后仍由远端 controllerEpoch、matchSeq、stateVersion 和 decisionId 作权威检查。关闭窗口或切换界面不发送认输。显式认输使用独立 operation 与共享 MatchResign 合同。

待确认命令保留在客户端私有存储，Bridge 重启后先显示摘要，再调用 `recover` 查回执并重放同一原始请求。恢复接口不接受新 commandId 或新 choice，因此其他授权窗口只能帮助恢复既有请求。身份删除、丢弃待确认命令和凭据读取没有浏览器接口。

每个 profile/match 仅一条后台 WSS。多个窗口 watch 后共享最新可见快照，events 只读本机缓存，慢窗口不会积压历史队列。窗口每次 watch/events 续期 60 秒；清理线程每秒检查，无消费者即停止。断流错误只影响对应 feed，显式 watch 可重新启动已结束的流。快照/游标的持久一致性仍由客户端负责，本地 revision 只用于判断本机缓存是否更新，不能作为服务器游标。

## 5. 资源与关闭限制

| 项目 | 限制 |
| --- | --- |
| 原生配置 / 授权窗口 | 各最多 8 个 |
| ASGI 在途请求 | 4 个；包含读取请求体和等待远端的时间 |
| 请求体 | 32 KiB；分块也计数，读取总超时 5 秒 |
| 同 profile 前台操作 | 1 个，其他操作立即报忙 |
| 后台远程流 | 共最多 4 条；每条保留一个最新 event |
| 本机控制 lease | 每 profile 最多 64 局 |
| 窗口授权 / feed 续期 | 8 小时 / 60 秒 |
| 关闭等待 | 全 owner 默认 20 秒；超时返回 SHUTDOWN_PENDING |

关闭开始即撤销授权、设置 stop，然后等待流和前台操作。若仍有工作，保留相关凭据文件锁并拒绝新请求，原生宿主稍后重试 close；不会释放锁后让另一个实例并发使用同一凭据。关闭只处理本 owner 创建的线程和客户端，不停止系统中其他服务。

本地请求超时与远端超时是不同概念：远端仍沿用 P5-A 的每次 socket I/O 超时，尚无绝对 HTTP 总时长限制。请求取消或窗口离开不保证远端操作未提交，应查看持久化恢复状态。凭据仍为 POSIX 权限明文文件，Windows 后端和系统钥匙串尚未实现；本阶段不声称跨平台发布完成。

## 6. 验收记录

P5-B 已完成本地隔离验收。证据只保留在忽略目录 `var/p5b/`，不发布真实卡组、CA 私钥、令牌或批准规则资产。

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| 原生客户端 + 本机桥接 + 两组真实 TLS 集成 | 57 passed；5 个上游库弃用提示 | `acceptance-03.log`、`acceptance-03/results.xml` |
| 共享核心、Provider、协议、迁移与公开导出 | 230 passed | `contracts.log` |
| 源代码审计、冻结基线、10 项目构建与包外 wheel 安装 | 通过，包含客户端和 bridge smoke | `build-01.log`、`build-01/build-report.json` |
| 三个 npm 发布包在 checkout 外构建 | 通过 | `npm-01.log` |
| 既有人机、局域网 HTTP 与 HTTPS 入口 | 均 HTTP 200；测试容器已清理 | `service-health.json` |

收尾中修复了两个测试文件同名导致的收集冲突。首次联合测试的推送等待在 5 秒内未达标，单项复现通过；随后统一使用已有原生客户端集成测试的 15 秒等待预算，并在 feed 提前结束时记录安全错误码，完整联测通过。尚不能据此定位原等待超时的唯一原因或宣称延迟指标，后续 P6 另做性能测量。

测试覆盖恶意 Origin/Host、无/伪造本机 capability、重复头、非回环 peer、代理头、URL 查询、未知操作、额外字段、重复 JSON 字段、分块超限、全局请求上限、错误脱敏、关闭超时、多窗口接管栅栏、无人消费流清理、慢窗口最新值、跨 profile 隔离，以及未知控制/命令结果恢复。

真实集成测试使用两个随机回环 HTTP 桥接、独立凭据目录、第三个真实 HTTPS/WSS 服务和独立 PostgreSQL。两位玩家提交真实批准卡组、邀请、准备、命令、认输及私有回放；模拟服务器提交后响应丢失，验证回执恢复和数据库没有重复命令。此证据仍是同机双实例，未完成两台物理设备或本地 GUI 验收。

公共 CI 不带私有规则输入，真实规则测试明确 skip；合成边界测试照常运行。发布门禁禁止 desktop-api 导入服务端、引擎、AI 或 SQLite，独立 wheel 在 checkout 外验证 owner 和工厂而不启动服务。

复现：

```sh
PTCG_V2_REAL_INPUTS_FILE="$PWD/var/p4b/real-inputs.json" \
  var/p4a/venv/bin/python scripts/migration/p4a_postgres.py \
  --output var/p5b/acceptance-new --python var/p4a/venv/bin/python --suite bridge
var/p1/venv/bin/python -m pytest tests/core tests/providers tests/protocol \
  tests/migration tests/portable/test_public_export.py -q
var/p1/venv/bin/python scripts/migration/gate.py \
  --build-output var/p5b/build-new --dependency-wheelhouse var/p4a/wheelhouse
node scripts/migration/p3_package_smoke.mjs var/p5b/build-new
```

## 7. 下一阶段

P5-C 将在本地 GUI 接入服务器选择、游客身份、邀请准备、共享 Arena 与本人回放，并完成可信窗口启动/授权交付及本地资源缓存。原生后台始终持有远端凭据，页面只接触本机 capability。P5-D 再完成两台设备、自然终局、PLAYER2 操作、断线恢复、资源命中与人机隔离验收。本阶段不提前宣告 P5 整体完成。
