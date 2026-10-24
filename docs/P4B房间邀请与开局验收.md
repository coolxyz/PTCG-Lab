# P4-B 房间邀请与开局验收

日期：2026-10-24。基线：P4-A 提交 `9067663`。本阶段完成独立 API 的卡组 revision、房间邀请、双方准备和可靠初始化；完整可操作对局、控制权、WSS 和私有回放继续在 P4-C 实现，P4-D 进行自然终局验收。默认 APP、旧 v1、局域网、人机与 AI 运行入口保持原样。

## 1. 已实现范围

`ptcg-online-server` 更新为 `0.4.0.dev2`。共享 core/provider/protocol 版本与冻结代码未改；`DeckSubmission` 继续使用共享 schema，包括 60 张总量、唯一 printingId、字段边界等校验。真实合法性、效果支持、规则范围和归一化通过批准的 CoreFactory 完成，不相信客户端给出的校验结论。

新增服务端 `lobby.schema.json` 定义原生大厅的输入和输出，随 wheel 分发。P2 的共享协议未定义大厅合同，本阶段保留在服务内以避免改写冻结对战 DTO；P5 客户端接入时必须消费这份合同，并在解除冻结的独立步骤决定共享发布，不能另写一套语义不同的大厅 DTO。

| 接口 | 请求与结果 |
| --- | --- |
| POST `/api/online/v2/decks` | 共享 DeckSubmission；返回不可变 deckRevision、ruleReleaseId、formatAsOf |
| POST `/api/online/v2/rooms` | requestId、ruleReleaseId、本人 deckRevision；返回房间码和 PLAYER1 视角 |
| POST `/api/online/v2/rooms/join` | requestId、inviteCode、本人 deckRevision；返回 PLAYER2 视角 |
| GET `/api/online/v2/rooms/{roomId}` | Bearer 成员身份读取当前房间 |
| POST `/api/online/v2/rooms/{roomId}/deck` | requestId、expectedRoomVersion、本人 deckRevision；取消双方准备 |
| POST `/api/online/v2/rooms/{roomId}/ready` | requestId、expectedRoomVersion、ready；双方准备后原子生成唯一初始化任务 |
| POST `/api/online/v2/rooms/{roomId}/leave` | requestId、expectedRoomVersion；等待阶段离开并关闭房间 |

所有 POST 仍包含 `protocolVersion: "2.0"`，除创建游客身份外均需 Bearer access token。客户端不得提交 userId、seat、seed、引擎局面或伤害。房间输出只含本人 revision、座位、双方准备状态和公共房间元数据；不含对方卡表/revision、种子、恢复记录、摘要或租约。

已批准规则输入且真实规则进程启动成功时，capabilities 可返回 `features: ["invite"]`。`acceptingMatches` 仍为 false，表示尚未开放可操作的实时对局；本阶段可完成大厅和初始化验收。`initialized` 仅表示初始私有记录已保存，不表示 P4-C 命令/推送已可用。

## 2. 幂等、授权和房间生命周期

requestId 在用户全部大厅写入中统一命名空间，摘要绑定操作、目标房间和完整正文。同 ID 同内容返回原始回执；同 ID 不同正文或跨操作复用返回 `IDEMPOTENCY_CONFLICT`。回执与业务变更同一事务提交。回执是原始提交的确认，可能不是最新房间版本；客户端收到它后可 GET 最新状态。

房间修改必须提供当前 expectedRoomVersion。两人同时对同一版本准备时，一方成功，另一方收到 `MATCH_VERSION_CONFLICT`；后者读取最新房间后用新 requestId 重试。同一准备请求重复发送可直接取得原始回执，不重复创建任务。失败请求不生成成功回执。

身份先由令牌确定，事务内持有会话共享锁并再次检查访问令牌，随后锁定用户请求序列和房间。撤销/刷新持有会话排他锁，避免等待期间被替换的旧令牌继续执行写入；已完成撤销不能通过历史回执重新授权。不同用户的不同房间不共享房间修改锁。全局锁只用于新房间容量准入，不包围规则计算。

邀请码为 10 位无易混淆字符的随机码，碰撞时最多重试 5 次。邀请码只用于加入，不授权读取房间；第三人即使持有 roomId 也不能查看或修改。房间最多两个座位，同一用户最多参加一个未结束房间。

等待房间默认 15 分钟到期；到期不能加入/准备，GET 将其标为 closed。任一成员在等待阶段离开都会关闭该房间，另一位需要新建房间；这与旧 v1 的“客人离开后保留房间”行为有意不同。初始化以后不能使用大厅 leave 代替认输；认输和终局释放属于 P4-C。

默认最多 100 个活跃房间、每用户 100 个卡组 revision、2000 条大厅回执。新增 `JOIN_PER_MINUTE` 默认每来源 IP 每分钟 20 次，持久化到数据库且不信任转发头。配置均使用 `PTCG_ONLINE_V2_` 前缀：MAX_ROOMS、MAX_DECKS_PER_USER、MAX_LOBBY_RECEIPTS、ROOM_SECONDS、JOIN_PER_MINUTE。回执、revision 和结束记录当前不自动删除；保留/清理策略仍需后续阶段实现。

## 3. 唯一初始化与故障语义

新增 `002_lobby.sql`，不改 `001_sessions.sql`。包含不可变卡组 revision、房间、成员、用户幂等回执和初始化任务。迁移仍显式运行；测试证明从仅 001 的数据库升级到 002 后，旧会话令牌仍有效、001 校验和不变。

双方准备的同一短事务固定 matchId、roomId、规则身份、formatAsOf、双方身份、revision、归一化卡表和 53 位安全整数随机种子，同时保存 ready 回执与 initializing 房间状态。数据库对 roomId 和 matchId 设置唯一约束。没有事务提交就不会派发规则任务；中途失败回滚准备状态、任务和回执。

初始化流程：

1. 用 `FOR UPDATE SKIP LOCKED` 领取匹配规则身份的 pending 或过期任务；写入随机 lease token、60 秒期限和尝试次数。
2. 关闭数据库事务，在独立规则子进程使用已持久种子/卡组创建初局。
3. 重新开启短事务，核对 lease token、状态与期限；仅当前有效持有者能提交恢复记录，并把任务和房间一起改为 initialized。
4. 崩溃后等租约到期接管；失败重试沿用同一 matchId、seed 和 revision。旧持有者晚到的结果被丢弃，已成功任务不会再次领取。

规则失败最多尝试 3 次，间隔至少 2 秒，随后 technical_abort，不给任何玩家记输赢。仅因计算槽忙导致的延后不消费失败次数。API 启动后台循环扫描任务；数据库不可用时不先响应初始化成功。两个 API 实例可各自持有规则进程，但提交权始终由数据库租约确定。

P4-B 只保存初始服务端私有恢复记录，不提供网络下载入口。双方可见帧、命令回执和 outbox 原子写入在 P4-C 增量迁移中加入，不能把这里的 initialized 状态直接当作可玩对局。

## 4. 规则进程与镜像

API 进程不导入上游 `ptcg`，不运行真实引擎。启动时先验证批准 bundle/hash/release，再执行真实 CoreFactory 启动探测；探测失败 readiness 返回 503。每次卡组校验和初始化都使用一次性子进程，在子进程内重新校验固定身份。

每个 API 实例同时最多运行一次规则操作；超额返回可重试容量错误。父进程设置 20 秒超时，子进程输出限定 8 MiB，数据库事务不跨越规则执行。子进程工作目录是安装包目录，只接收最小环境、规则位置/校验值和本次任务输入，不继承数据库凭据或其他环境秘密。支持的 POSIX 环境下监测父进程存活，避免 API 被强杀后留下失去超时管理的规则进程。日志不输出请求、私有恢复记录和异常堆栈。

这种冷启动方式优先保证隔离与失败可恢复，没有宣称低延迟或高并发。持久 worker、公平队列、连接池和负载实测仍属于 P6/P7。当前规则进程没有独立于容器的内存配额；正式部署需计入 API/规则子进程合计峰值。

新镜像 `ptcg-lab-online-api:p4b` 已单独构建，未覆盖 p4a 或旧 v1 镜像。ARM64 / Python 3.14.9，本地 image ID：`sha256:99ff97000ed71251113efbb5c3273da4f1ecc936e63963f5e8005c4c8af910e2`，Docker 报告大小 188,923,561 字节。基础镜像仍不含规则资产、卡图、GUI/AI；实际运行验收使用非 root、只读根目录与专用 PostgreSQL。

本机真实规则测试为 Python 3.14.8，使用已有 P2 隔离 bundle 的只读引用；没有重新导入研究数据。镜像内 Python patch 不同，不能照搬本机 ruleReleaseId。本次镜像验收覆盖安装、认证、迁移和隔离，不宣称镜像内真实卡池开局已验证；生产规则镜像、AMD64 和对外部署仍需后续验收。

## 5. 验收与复现

| 验证 | 结果与本地证据 |
| --- | --- |
| 合成 PostgreSQL 大厅测试 | 卡组所有权、第三人权限、猜码限流、重复/并发准备、两个 joiner 竞争、版本冲突、换卡组、离开/过期、容量、事务回滚、租约抢占/过期结果拒绝、双任务隔离、技术中止均通过 |
| 真实规则 | 子进程启动、合法/无效卡组、同 seed 两次独立初始化恢复记录相同、两个 HTTP 客户端与强制重启后初始化通过 |
| 完整服务 + 真实规则 + API 镜像 | **51 项通过**；`var/p4b/acceptance-final.log`、`var/p4b/acceptance-final/results.xml` |
| core/provider/protocol/迁移门禁/公开导出回归 | **216 项通过**；`var/p4b/contracts.log` |
| 制品/公开源门禁 | `var/p4b/build-final.log`、`var/p4b/build-final/BUILD-REPORT.json` |
| npm 制品仓库外消费者 | `var/p4b/npm-final.log` |
| 既有服务只读检查 | 人机、旧在线 v1、局域网 HTTPS 均 HTTP 200；`var/p4b/service-health.json` |

真实规则测试必须显式提供私有 `PTCG_V2_REAL_INPUTS_FILE`，包含 directory、pin、release、deck1、deck2；不进入公开源码或镜像。未提供时两项测试跳过，不能用合成数据测试代替真实规则验收。CI 继续自动执行无资产部分，镜像和真实规则为本地显式验收；本次未触发远端 CI。

```sh
docker build -f ops/online/Dockerfile -t ptcg-lab-online-api:p4b .
PTCG_V2_REAL_INPUTS_FILE=/path/to/private/approved-inputs.json \
  python scripts/migration/p4a_postgres.py \
  --output var/p4b/new-acceptance \
  --python /path/to/dedicated/venv/bin/python \
  --image ptcg-lab-online-api:p4b
python scripts/migration/gate.py \
  --build-output var/p4b/new-build \
  --dependency-wheelhouse var/p4a/wheelhouse
node scripts/migration/p3_package_smoke.mjs var/p4b/new-build
```

沿用验收脚本文件名以兼容 P4-A，新增输出仅写 `var/p4b`；使用独占容器、55484/8784 或自动分配的本机端口。API 强杀测试只杀自身子进程；为缩短等待，仅在该测试独占库中提前过期 lease，生产接管仍等待真实 60 秒期限。所有临时容器完成后清理，没有启动长期测试部署。

## 6. 来源核对、差异和回退

只读参考旧在线实现的房间/准备/初始化边界；源 `packages/online/service.py` SHA-256 为 `4f823dd3b57182352dc21ab91bda444b0b05d7fe9b47fd1c70ee29f6465d6485`，repository 为 `671d38c3a05d850c9295d55514397b2b30e5dd0c1b4232475a57c4d1b4c46492`。没有修改来源文件，也没有复制旧网页认证、静态资源或 AI 依赖。

保留成员授权、revision 和预持久种子的原则；v2 明确采用独立 schema、原生令牌、统一 requestId 命名空间、10 位邀请码、双方换卡组取消准备、离开即关闭和带 fencing token 的初始化租约。上述差异为新 v2 合同，不宣称与 v1 的大厅 wire 行为完全等价。

数据库只向前追加 002。P4-A 会拒绝带未来迁移的库，因此不能仅把镜像换回 p4a 后继续使用升级后的库；需要升级前备份或独立新库。当前旧产品完全不依赖此库，回退产品入口无需迁移现有收藏、人机或局域网记录。

下一步 P4-C 从已固定的初始化记录恢复，对双方可见帧、命令回执、控制权及 outbox 建立同一提交边界，再接 WSS 和本人回放。保留 P4-A 的刷新严格重用策略与凭证丢失限制，客户端刷新合并仍需 P5 实现。
