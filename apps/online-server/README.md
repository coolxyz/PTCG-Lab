# ptcg-online-server

P6-C 原生客户端纯 API，版本 `0.6.0.dev3`。已提供游客会话、轮换/撤销、PostgreSQL 迁移/限流、卡组 revision、房间邀请、准备、独立规则进程初始化、权威命令、控制权、持久 WSS 推送与私有回放。P4-D 完整自然终局、真实进程故障及凭证生命周期的结果见 [验收记录](../../docs/P4D完整对局与故障恢复验收.md)。

入口为 `python -m ptcg_online_server`。数据库迁移必须显式运行 `python -m ptcg_online_server.migrate`，不会在 API worker 启动时自动安装或修改数据库。默认仅绑定本机 8784，禁止占用旧 8765/8770/8443 入口；生产 TLS 由专用网关终止，不信任转发头。

- `GET /healthz`：进程存活。
- `GET /readyz`：独立数据库、已批准规则输入及真实规则进程是否就绪；缺资产返回 503。
- `GET /api/online/v2/capabilities`：共享 v2 能力合同，批准规则进程就绪时 acceptingMatches=true，并提供 invite/duel/private-replay/reconnect。
- `POST /api/online/v2/sessions`：`protocolVersion: "2.0"`，可选 name/deviceName。
- `POST /api/online/v2/sessions/refresh`：protocolVersion 和 refreshToken。
- `GET /api/online/v2/sessions/current`：使用 Authorization Bearer accessToken。
- `POST /api/online/v2/sessions/revoke`：Bearer accessToken，正文仅 protocolVersion；同一尚未到期 access token 可重复撤销。

不接受 Origin、Cookie 或 URL 查询凭证；不会返回 Set-Cookie/CORS 许可。API 无静态页面、卡图、收藏、本地练习、同步、模型和训练接口。会话 envelope 的服务端 JSON Schema 随 wheel 发布，共享对战 DTO 继续由 `ptcg-battle-protocol` 校验；本阶段没有修改冻结的共享核心或协议。

部署、环境变量和隔离验收详见仓库 [P4 实施记录](../../docs/P4纯API服务实施记录.md)、[容器说明](../../ops/online/README.md)。本包 `IMPLEMENTATION_READY=False`，表示尚未通过完整产品发布与 P8 运维门禁；与 P4 的本地功能验收状态分开记录。

P4-B 新增 `/decks`、`/rooms`、`/rooms/join`、`/rooms/{roomId}` 及其 `deck/ready/leave` 操作。所有变更要求 requestId，房间修改要求 expectedRoomVersion；重复回执不代表最新房间状态。具体合同、并发/恢复验收和 001→002 升级/回退限制见 [P4-B 记录](../../docs/P4B房间邀请与开局验收.md)。

P4-C 新增 `/matches/{matchId}` 及其 `control/commands/resign/receipts/frames/timeline` 接口与 `/stream` 原生 WebSocket。命令同时要求 Bearer、X-PTCG-Controller、epoch 和当前版本；恢复游标绑定本人及对局。003 迁移必须显式应用。合同、原子提交、配额与验收范围见 [P4-C 记录](../../docs/P4C对局提交与私有推送验收.md)。P5 才接本地 GUI；当前每秒轮询和单计算槽不代表 P6 并发优化已完成。

P5.5 对局信息补齐：将已接受动作的公开声明、使用卡牌及投币结果写入现有 `events`，与局面、双方私有回放和回执在同一事务提交；不转发隐藏检索选项或规则诊断日志。协议和规则版本不变。显示边界及升级说明见 [对局信息展示设计](../../docs/P5.5对局信息展示设计.md)。

dev6 新增公开选牌来源：提交前核实提示来源对象位于公开场上、竞技场或弃牌区，提交后只发布该来源的印刷身份。不按名称猜测对象，不转发私人候选或数值选项原文。


P6-A 增加有界数据库连接池与低基数指标，详见 [实施记录](../../docs/P6多局调度与性能实施记录.md)。默认池上限 4、等待者 16、等待 1 秒；关闭复用仍保留有界准入。API lifespan 管理池，独立迁移/一次性 Repository 保持即用即关；需要复用的维护工具显式使用 `with Repository(settings)`。无新增公开 `/metrics` 接口；诊断通过进程内 `Repository.diagnostics()` 获取，不含 SQL 文本、DSN 或私有规则数据。鉴权、schema 校验与持久提交顺序未取消。

P6-B 默认启用集中订阅元数据检查与提交后通知；只有新游标才读取本人 outbox，定期补漏同时复核身份。`PTCG_ONLINE_V2_STREAM_HUB_ENABLED=0` 可恢复旧轮询。保留快照／游标事务关系、每次读取鉴权、发送超时及重连合同；没有认证结果缓存或跨进程消息总线。实现与验收见 [P6 记录](../../docs/P6多局调度与性能实施记录.md)。

P6-C 增加每局 FIFO、跨组轮转、在途重试合并和有界准入；卡组校验、初始化和投影共享同一规则通道。排队前和返回前验证调用身份，实际执行与提交继续使用原事务屏障。默认全局 32 任务、每组 2、每用户 4、全部等待者 64、每任务等待者 4，排队最多 2 秒；`COMMAND_QUEUE_ENABLED=0` 回退直接调用。认输／接管不排队，旧计算靠提交复核失效。参数完整前缀为 `PTCG_ONLINE_V2_`。
