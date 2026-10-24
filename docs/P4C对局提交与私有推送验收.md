# P4-C 对局提交与私有推送验收

日期：2026-10-24。基线：P4-B 提交 `577a6c1`。本阶段在 PTCG-Lab 的独立 API 中接通权威命令、控制权、持久推送、私有回放和重启恢复。完整自然终局及故障矩阵继续由 P4-D 验收，本地 APP 网络适配属于 P5。原产品入口没有切换。

## 1. 合同与接入顺序

服务版本为 `ptcg-online-server==0.4.0.dev3`。core/provider/protocol 保持 P2-D 冻结版本及源哈希。命令、快照、决策、错误和回放仍使用共享协议；服务新增 `match.schema.json`，定义控制权、认输、订阅和心跳输入，没有复制第二套对战 DTO。

双方完成 P4-B 准备后，服务从已保存的初始恢复记录恢复并生成两个成员各自的首帧，不重抽种子或起手。房间 `initialized` 到首帧发布之间存在短暂间隔，客户端此时 GET 对局可能得到 403；不能把房间状态直接当作首帧已发布。P5 应使用有上限的等待并处理服务不可用，后续可为该状态增加明确的就绪合同。

所有以下路径均以 `/api/online/v2` 开头，身份来自 Bearer access token：

| 接口 | 行为 |
| --- | --- |
| GET `/matches/{matchId}` | 读取本人 MatchSnapshot，包含当前 epoch 与流游标 |
| POST `/matches/{matchId}/control` | requestId、controllerId、expectedControllerEpoch；成功返回本人新快照 |
| POST `/matches/{matchId}/commands` | 共享 Command；另需 `X-PTCG-Controller` 头 |
| POST `/matches/{matchId}/resign` | commandId、expectedMatchSeq、expectedStateVersion、controllerEpoch；同样要求控制权头 |
| GET `/matches/{matchId}/receipts/{commandId}` | 查询本人命令确认，不返回规则内部记录 |
| GET `/matches/{matchId}/frames[/{cursor}]` | 本人 ReplayPage，每页最多 3 帧 |
| GET `/matches/{matchId}/timeline[/{cursor}]` | 同一分页边界下的帧序号、公开事件、效果及结果摘要 |
| WS `/stream` | 原生客户端鉴权后发送 MatchSubscribe，仅接收本人持久流 |

POST 与订阅均带 `protocolVersion: "2.0"`。controllerId 由原生客户端为当前控制实例生成 32 位小写十六进制随机值，不是独立凭证。服务器把它与持久用户、座位、已认证会话、递增 epoch 绑定。其他窗口接管必须使用最新 expectedControllerEpoch；旧控制实例随后收到 CONTROL_LOST。相同接管请求可重取原回执，回执不是最新控制权的保证。

共享 Command 包含 commandId、expectedMatchSeq、expectedStateVersion、controllerEpoch、decisionId 和 choice。座位从成员表推导；服务器用当前本人决策验证 optionId/selectedRefs，再映射为核心命令。不能上传客户端计算的伤害、种子、隐藏卡牌或完整局面。commandId 的公开命名空间为“本局、本人”，内部映射为座位和摘要，避免两人使用同一 ID 误命中核心回执。

操作响应只确认 commandId、accepted、matchSeq、stateVersion、streamCursor 和协议/对局身份。客户端以本人快照或推送为显示依据。成功响应丢失时先查回执或重发**原始同 ID、同正文、同 controllerId** 请求；不能用新 ID 猜测上次是否提交。未知回执返回 404，其他人的对局返回 403。

## 2. 原子提交与并发边界

新增 `003_matches.sql`，不改已发布的 001/002。包括 matches、成员控制权、双方历史帧、命令/接管回执及定向 outbox。

普通命令执行分三步：

1. 在短事务内重新认证并持有会话共享锁，锁定本局，检查成员、历史回执、操作权、matchSeq、stateVersion 和决策。读取服务端私有恢复记录后结束事务。
2. 一次性规则子进程恢复批准核心并执行命令，计算双方各自可见投影、公开效果和结果；此时不持有数据库连接或锁。
3. 新事务再次认证并锁定本局，先检查相同命令回执，再核对控制权和版本。一次提交写入新私有恢复记录、双方可见帧、双方 outbox 和本人回执；自然终局时同事务关闭房间。

任意一项写入失败都会回滚全部变更。并发相同请求只产生一次有效推进；其他命令、接管、会话撤销或内部技术中止发生在计算期间时，旧候选结果被拒绝。不同对局使用各自行锁，不共用规则提交全局锁。现有认证/限流仍有独立全局准入锁，不能据此宣称已经完成 P6 并发优化。

核心命令使 matchSeq 与 stateVersion 各增加 1。接管只增加本人 controllerEpoch 和 stream 序号，不增加 matchSeq。认输增加 matchSeq、保留 stateVersion 与私有核心恢复记录，在双方可见终帧中写入 resigned 和胜方；服务层终局状态决定不得继续提交。内部 technical_abort 同样发布双方终帧，但无胜者，不把技术故障记作玩家失败；目前只有内部方法，没有玩家 API、自动裁决或运维 CLI。

恢复读取私有核心记录，展示读取对应成员的持久帧。两者都在数据库中，API 重启不依赖原进程内存。规则失败/超时未提交候选结果，后台重新探测 worker；不会因计算失败自动判负。已终局记录不会被初始恢复流程重新激活。

## 3. 推送、恢复与私有回放

原生客户端以 WSS 连接 `/stream`，在 Authorization 头携带令牌；拒绝 Origin、Cookie、查询参数以及重复身份头。连接建立后 10 秒内发送：

```json
{"protocolVersion":"2.0","matchId":"32位小写十六进制ID","streamCursor":null}
```

这里的 matchId 为示意占位，实际必须满足 schema。null 游标在同一事务内取得最新本人快照和边界游标；已有游标则按本人 outbox 序号补发。游标绑定对局和用户，过期、其他玩家或其他对局的游标返回 RESYNC_REQUIRED；客户端重新以 null 游标订阅，不得猜测缺失事件。

推送 envelope 为 `{protocolVersion, type, snapshot}`，type 为 snapshot/update/control；snapshot 使用共享 MatchSnapshot。约每 15 个轮询周期发送 `{protocolVersion, type:"heartbeat", streamCursor}`。客户端之后只可发送 `{protocolVersion:"2.0", type:"ping"}`，命令仍走 HTTP。接管事件具有独立流序号，所以相同 matchSeq 也可带不同 epoch；客户端不能仅凭 matchSeq 丢弃 control。

当前每连接最多每秒读取一次数据库，每次最多 8 条；每次读都重新检查会话及成员权限。撤销通常在下一轮被发现，但在途已读取帧和发送超时会影响实际断开时刻，不保证即时断连。每个 API 实例最多 32 个流，发送等待最多 3 秒，慢消费者断开后按游标重连；协议级消息上限 16 KiB、接收队列 2，应用订阅/ping 另限 4 KiB，关闭压缩。

每名玩家保留最近 64 条 outbox，控制事件也计入。历史回放不依赖 outbox 保留长度：用独立历史帧游标分页，只返回请求者当时可见的数据，去掉可操作 decision/控制权字段。第三人即使知道 matchId 或邀请码也无读取权限；没有公开整局种子、核心状态摘要或对手手牌记录接口。timeline 是同一私有回放的轻量摘要，不是未经裁剪的引擎日志。

## 4. 配额、镜像与部署边界

新增配置均使用 `PTCG_ONLINE_V2_` 前缀：

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| MAX_STREAMS | 32 | 单 API 实例同时连接数 |
| OUTBOX_RETENTION | 64 | 每名玩家保留的流事件数 |
| MAX_MATCH_COMMANDS | 2000 | 本局命令推进上限；另作为每名玩家接管回执上限 |

达到命令上限后仍允许当前控制者认输，避免必须继续调用规则才能释放房间。已结束帧、命令回执、会话等历史数据尚无自动归档清理。规则操作仍是每 API 一个计算槽、20 秒超时、8 MiB 结果上限；私有子进程输入最多 16 MiB。长局恢复成本和冷启动成本仍待 P6/P7 优化与测量，以上值不是可承载玩家数结论。

运行依赖新增并固定 `websockets==15.0.1`；Docker 使用新标签 `ptcg-lab-online-api:p4c`，保留此前镜像。它包含 API、共享包和运行依赖，不包含卡图、GUI、AI 或真实卡池资产。生产需单独挂载批准规则，TLS 由专用网关提供；本阶段验收用独立测试证书在 Uvicorn 上运行真实 HTTPS/WSS，并验证证书链/主机名，没有关闭 TLS 校验。

最终本地镜像为 ARM64，ID `sha256:da493624d6c57b6161aba933f57979713ebeba4ffd104e0a220a7c1b309394a1`，Docker 报告大小 190,532,400 字节；该 ID 不是已推送的 registry digest。最终构建同步了包说明文档，运行代码与完整服务验收时相同，并单独复验该镜像。

本机真实规则运行时为 Python 3.14.8，镜像为 3.14.9，执行身份不能混用。镜像验收覆盖安装、非 root、只读根目录、迁移、认证和路由隔离；不代表镜像内的最终生产规则身份或 AMD64 已批准。外部部署、TLS 网关、备份恢复属于后续发布验收。

## 5. 验收记录

本地结果与证据：

| 验证 | 结果与证据 |
| --- | --- |
| 完整服务、PostgreSQL、真实 HTTPS/WSS 与镜像 | **69 项通过，无跳过**；`var/p4c/acceptance-final.log`、`var/p4c/acceptance-final/results.xml` |
| 最终包说明同步后的镜像 | 专用镜像复验，`var/p4c/image-final.log`；运行源码未改变 |
| core/provider/protocol、迁移边界及公开导出回归 | **216 项通过**；`var/p4c/contracts.log` |
| wheel/sdist/npm、仓库外离线安装与 schema/SQL 包含性 | 门禁通过；`var/p4c/build-final/BUILD-REPORT.json`、`var/p4c/build-final.log` |
| npm 仓库外真实消费者 | 3 个包通过；`var/p4c/npm-final.log` |
| 原人机、旧在线 v1、局域网 HTTPS | 均 HTTP 200；`var/p4c/service-health.json` |

冻结核心与协议源哈希、生成协议、依赖边界和公开资产扫描通过。框架在 Python 3.14 下仍有弃用警告，未隐藏。本次未触发远端 CI、push 或外部镜像发布。

具体范围包括：

- PostgreSQL 原子回滚、相同/不同命令竞争、两局隔离、接管/撤销/中止期间的候选结果拒绝、重复回执、跨人/跨局游标、outbox 淘汰后重同步、认输及私有分页。
- 从仅 001 的库升级后保留会话；从 002 的已初始化对局升级至 003 后保留完整初始记录，生成双方首帧且禁止再次初始化。
- 两名真实原生身份经 HTTPS 创建、邀请、准备，执行 8 个真实规则操作并接收 WSS，强制结束该测试 API 后重新启动、查询及重发最后命令、补发 8 条更新、认输及读取 10 帧私有回放。
- 慢发送超时取消、断开的拒绝连接释放容量、活动流撤销鉴权、输入边界和第三人越权。

上述真实对局使用已有批准的 P2 隔离 bundle 只读输入，没有从研究工程重新导出资产。它验证真实命令与传输恢复，**不等于完整自然终局**，P4-D 仍需独立完成长局和故障矩阵。未配置私有规则输入的 CI 会跳过真实资产测试；没有镜像参数时镜像测试也跳过，均不能算作相应验收通过。

复现：

```sh
docker build -f ops/online/Dockerfile -t ptcg-lab-online-api:p4c .
PTCG_V2_REAL_INPUTS_FILE=/path/to/private/approved-inputs.json \
  python scripts/migration/p4a_postgres.py \
  --output var/p4c/new-acceptance \
  --python /path/to/dedicated/venv/bin/python \
  --image ptcg-lab-online-api:p4c
python scripts/migration/gate.py \
  --build-output var/p4c/new-build \
  --dependency-wheelhouse var/p4a/wheelhouse
node scripts/migration/p3_package_smoke.mjs var/p4c/new-build
```

验收脚本沿用 P4-A 名称，输出目录必须不存在，使用自己创建的临时 PostgreSQL 和容器。真实 API 仅绑定系统分配的环回端口，强杀只针对该测试子进程。旧 8765/8770/8443 服务不参与故障注入。

## 6. 回退与下一步

003 只追加新表；旧 P4-A/B 运行时会拒绝带未来迁移的数据库。回退不能仅换旧镜像后继续使用新库，需升级前备份或专用新库。原研究工程、既有 v1 数据库和人机/AI 任务不依赖这个数据库；本次没有切换它们的入口。

P4-D 应完成两个原生客户端的真实自然终局，扩大命令响应丢失、进程故障、规则失败和恢复的一致性矩阵，并核对完整对战的可见性与旧服务隔离。P5 再实现本地 GUI 的原生网络桥、凭证保存、刷新合并、控制权提示、订阅恢复和私有回放显示。P6/P7 再优化连接池、持久 worker、公平调度、差量推送及容量，不把 P4 的每秒轮询当作最终扩容方案。
