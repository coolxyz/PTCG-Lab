# ptcg-online-server

P4-C 原生客户端纯 API，版本 `0.4.0.dev3`。已提供游客会话、轮换/撤销、PostgreSQL 迁移/限流、卡组 revision、房间邀请、准备、独立规则进程初始化、权威命令、控制权、持久 WSS 推送与私有回放。完整自然终局和故障矩阵继续在 P4-D 验收。

入口为 `python -m ptcg_online_server`。数据库迁移必须显式运行 `python -m ptcg_online_server.migrate`，不会在 API worker 启动时自动安装或修改数据库。默认仅绑定本机 8784，禁止占用旧 8765/8770/8443 入口；生产 TLS 由专用网关终止，不信任转发头。

- `GET /healthz`：进程存活。
- `GET /readyz`：独立数据库、已批准规则输入及真实规则进程是否就绪；缺资产返回 503。
- `GET /api/online/v2/capabilities`：共享 v2 能力合同，批准规则进程就绪时 acceptingMatches=true，并提供 invite/duel/private-replay/reconnect。
- `POST /api/online/v2/sessions`：`protocolVersion: "2.0"`，可选 name/deviceName。
- `POST /api/online/v2/sessions/refresh`：protocolVersion 和 refreshToken。
- `GET /api/online/v2/sessions/current`：使用 Authorization Bearer accessToken。
- `POST /api/online/v2/sessions/revoke`：Bearer accessToken，正文仅 protocolVersion；同一尚未到期 access token 可重复撤销。

不接受 Origin、Cookie 或 URL 查询凭证；不会返回 Set-Cookie/CORS 许可。API 无静态页面、卡图、收藏、本地练习、同步、模型和训练接口。会话 envelope 的服务端 JSON Schema 随 wheel 发布，共享对战 DTO 继续由 `ptcg-battle-protocol` 校验；本阶段没有修改冻结的共享核心或协议。

部署、环境变量和隔离验收详见仓库 [P4 实施记录](../../docs/P4纯API服务实施记录.md)、[容器说明](../../ops/online/README.md)。本包 `IMPLEMENTATION_READY=False`，表示尚未达到完整 P4/P8 产品发布门槛；不影响独立安装与基础 API 测试。

P4-B 新增 `/decks`、`/rooms`、`/rooms/join`、`/rooms/{roomId}` 及其 `deck/ready/leave` 操作。所有变更要求 requestId，房间修改要求 expectedRoomVersion；重复回执不代表最新房间状态。具体合同、并发/恢复验收和 001→002 升级/回退限制见 [P4-B 记录](../../docs/P4B房间邀请与开局验收.md)。

P4-C 新增 `/matches/{matchId}` 及其 `control/commands/resign/receipts/frames/timeline` 接口与 `/stream` 原生 WebSocket。命令同时要求 Bearer、X-PTCG-Controller、epoch 和当前版本；恢复游标绑定本人及对局。003 迁移必须显式应用。合同、原子提交、配额与验收范围见 [P4-C 记录](../../docs/P4C对局提交与私有推送验收.md)。P5 才接本地 GUI；当前每秒轮询和单计算槽不代表 P6 并发优化已完成。
