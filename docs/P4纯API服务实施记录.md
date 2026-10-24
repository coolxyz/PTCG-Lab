# P4 纯 API 服务实施记录

更新：2026-10-24。基线：P3-C 提交 `097ee99`。当前 **P4-A/B/C 已完成本地验收，完整 P4 仍在开发中**。P4-B 以 `9067663` 为基线，详见 [房间邀请与开局验收](P4B房间邀请与开局验收.md)；P4-C 以 `577a6c1` 为基线，详见 [对局提交与私有推送验收](P4C对局提交与私有推送验收.md)。本篇第 2—7 节保留 P4-A 当时的实施与验收范围。新代码只在 PTCG-Lab 内实现，没有切换默认启动入口或部署到原局域网服务。

依据：[分阶段实施](产品化重构分阶段实施.md)、[总体设计](产品化重构设计.md)。独立容器操作见 [部署入口](../ops/online/README.md)。本记录替代“P4 尚未实施”的旧状态，不改变 P4 原有双客户端完整对局验收门槛。

## 1. 分阶段交付

| 子阶段 | 交付与完成门槛 | 状态 |
| --- | --- | --- |
| P4-A | 独立可安装 API、游客会话与轮换/撤销、数据库迁移、输入边界、限流、批准规则输入和无 GUI/AI 镜像；重启和竞争测试通过 | 已完成本地验收 |
| P4-B | 卡组校验与 revision、邀请码、成员权限、房间版本、准备幂等、唯一开局任务与固定种子；并发准备不能重复开局 | 已完成本地验收，见 P4-B 记录 |
| P4-C | 操作权与 epoch、命令回执、双方帧/私有恢复记录/定向 outbox 原子提交、WSS、本人回放和进程恢复 | 已完成本地验收，见 P4-C 记录 |
| P4-D | 两个独立原生身份通过 HTTP/WSS 完成真实自然终局及认输；重复提交、断连、进程故障、越权与旧服务隔离验收 | 待实施 |

P4-A 始终返回 `acceptingMatches=false`、空 features。它可以创建和验证游客身份，不能创建房间或进行对战。`IMPLEMENTATION_READY=False` 保留完整阶段未验收的含义。P5 才接本地 APP 网络模式；P6/P7 才有连接池、公平调度和小配置容量结论。

## 2. 项目与依赖边界

`apps/online-server` 发布包 `ptcg-online-server==0.4.0.dev1`，仅从公开包入口使用：

- `ptcg-battle-core==0.2.0.dev1`：计算规则执行身份。
- `ptcg-engine-provider==0.2.0.dev0`：校验显式规则 bundle 和清单哈希。
- `ptcg-battle-protocol==0.2.0.dev1`：校验 v2 capabilities 与错误 envelope。
- FastAPI/Uvicorn、psycopg 和 JSON Schema：HTTP、PostgreSQL 与边界校验。

运行依赖闭包固定在 `apps/online-server/requirements.lock`。服务不导入 `apps.api`、旧 `packages`、SQLite、本地收藏、AI、训练或图片处理包。依赖边界门禁加入反例测试，防止这些依赖重新混入。核心、Provider、共享对战 schema 及其冻结源哈希均未修改。

本阶段只读核对了迁移清单中的旧在线入口、repository/service 和迁移模式；v2 会话与数据库边界按新原生客户端合同独立实现，没有把旧 Cookie/静态页面入口直接复制为 v2。房间和裁定的选择性移植留在 P4-B/C，届时逐项补来源与等价证据。

## 3. 已实现接口与认证合同

| 方法和路径 | 行为 |
| --- | --- |
| GET `/healthz` | 进程存活；不依赖规则和数据库 |
| GET `/readyz` | 独立数据库版本、部署身份以及批准规则输入均通过才返回 200；否则 503 |
| GET `/api/online/v2/capabilities` | 共享 v2 合同、批准的规则身份、目录版本和关闭的开局能力 |
| POST `/api/online/v2/sessions` | 创建游客、设备与会话，返回一次可见的访问/刷新令牌 |
| POST `/api/online/v2/sessions/refresh` | 单次使用刷新令牌，原子替换令牌对 |
| GET `/api/online/v2/sessions/current` | Bearer access token 查询本人身份 |
| POST `/api/online/v2/sessions/revoke` | Bearer access token 撤销本会话；尚未到期的同一 access token 可重复调用 |

POST 正文必须包含 `protocolVersion: "2.0"`。创建可带 name/deviceName，刷新只额外接受 refreshToken，撤销不接受自称的用户或会话 ID。所有用户、设备和会话 ID 均由服务器生成。服务自己的 `schemas/session.schema.json` 随 wheel 分发；它是原生认证 envelope，不另建一套对战 DTO。

随机令牌使用 32 字节熵，数据库只保存 SHA-256 摘要。访问令牌默认 15 分钟，刷新令牌使用绝对 30 天截止时间，不随刷新无限延长。刷新在会话行锁内重新检查旧令牌，一次提交同时消费旧 refresh、替换 access 并产生新 refresh；事务失败则全部回滚。

已使用 refresh 再次出现时，提交整条会话撤销后返回 401。并发刷新只有一个请求能获得新令牌，另一个请求会触发重用检测并撤销该会话；胜出的新令牌随后也无效。这是当前明确采用的严格策略。P5 必须合并并发刷新；刷新响应丢失不能盲目重试旧 refresh，目前需重新建立游客会话，尚无账号找回或刷新响应恢复机制。客户端必须妥善持久保存令牌；不把令牌交给 GUI。

## 4. 请求边界与资源限制

仅接受配置的 Host，不接受 Origin、Cookie、URL 查询参数或压缩正文，不提供 CORS 许可。POST 必须使用 JSON；重复身份相关头、重复 JSON 键、非有限数字、未知字段和名称控制字符被拒绝。正文以流式方式累计，最多 16 KiB，不能通过伪造较小 Content-Length 绕过。慢正文有超时，在途请求有上限。

错误只返回共享协议错误码，不返回请求正文、令牌、数据库连接串或规则路径。响应设置 no-store、nosniff 和 no-referrer；Uvicorn 关闭 access log。未提供网页、卡图、收藏、人机练习、同步、模型、训练及旧 v1 路由。

限流状态写入 PostgreSQL，API 重建不能重置。默认：同一 IP 每分钟 240 次 API 请求、10 次游客创建；全局每分钟 1200 次；最多 4096 个活跃限流键、1000 个未过期未撤销会话、32 个在途请求。超额返回 429/503。IP 键散列保存，不信任 X-Forwarded-For。

这是一套保守的基础准入配置，**不是实测可支持对局数**。P4-A 仍按请求建立短连接，限流和游客准入使用各自的全局事务锁；未来对局提交不能复用这些全局锁。代理后 IP 会合并计数，需要在后续部署阶段明确可信代理边界后再调整。已使用 refresh 摘要、过期会话和身份目前保留，长期清理策略及会话撤销后对在途命令的原子鉴权属于后续实现。

## 5. 独立数据库和规则准备

API 启动不会迁移数据库。部署方必须显式执行 `python -m ptcg_online_server.migrate`。数据库只用于 v2，schema 为 `ptcg_v2`；包含部署身份、迁移版本与校验和、用户、设备、会话、令牌摘要和限流表。发现其他业务 schema 的表、未知版本、修改过的历史迁移或不匹配 serverId 时拒绝操作。

迁移在事务和专用迁移锁下执行，重复调用无副作用。运行时也验证数据库部署身份和迁移校验和。短事务设置连接、语句、锁等待和事务空闲超时。房间、种子、对局帧及 outbox 尚未加入；后续以新 migration 增量添加，不修改已应用的 001 文件。

规则目录、bundle 清单 SHA-256 和批准的 ruleReleaseId 必须三者都显式指定。Provider 校验 bundle 后，使用共享核心计算 execution identity，并与批准值比较；缺失或不匹配均不就绪，不搜索研究目录或回退到其他规则包。

P4-A 的 rules readiness 仅代表规则**输入校验**通过，没有加载运行引擎或证明 worker 能开局；`acceptingMatches` 仍关闭。P4-B/C 必须补真实 worker 激活、固定种子、隔离执行及恢复验证，P4-D 才形成完整对战可用结论。

主要部署变量均以 `PTCG_ONLINE_V2_` 为前缀：

| 配置 | 含义 |
| --- | --- |
| DATABASE_URL / DATABASE_URL_FILE | PostgreSQL 秘密输入，必须二选一 |
| SERVER_ID | 固定部署身份，与数据库绑定 |
| PUBLIC_ORIGIN | 精确外部 origin，默认要求 HTTPS |
| ALLOW_LOOPBACK_HTTP | 显式设为 1 才允许环回 HTTP 验收 |
| BIND / PORT | 默认 127.0.0.1:8784；CLI 禁止 8765/8770/8443 |
| RULE_DIRECTORY / RULE_MANIFEST_SHA256 / APPROVED_RULE_RELEASE_ID | 批准且只读的规则输入 |
| ACCESS_SECONDS / REFRESH_SECONDS | 令牌生命周期 |
| MAX_SESSIONS / CREATE_PER_MINUTE / REQUESTS_PER_MINUTE / GLOBAL_PER_MINUTE / MAX_RATE_KEYS / MAX_INFLIGHT | 有界准入配置 |

## 6. 镜像与实际验收

`ops/online/Dockerfile` 使用多阶段 wheel 构建及专属白名单上下文，只包含三个共享 Python 包、API 和固定运行依赖。默认 Python 基础镜像摘要已锁定。运行镜像 UID 为 10001，验收时根文件系统只读、无额外 capabilities，且没有规则资产、GUI 或 AI 包。

本地构建结果：`ptcg-lab-online-api:p4a`，ARM64，Python 3.14.9，镜像 ID `sha256:50672b0cc1718eb653dc2be1aa125db6c669484cbf4984620d6b3df55d918d92`，Docker 报告大小 188,829,583 字节。该 ID 是本地镜像身份，不是已推送的 registry digest。基础 Python patch 与本机测试环境 3.14.8 不同，执行身份不能直接混用；真实规则需针对最终镜像批准。

Compose 提供独立项目、独立数据库卷和只发布环回 8784 的 API；数据库不发布端口。秘密生成器创建新 0700 目录和 0600 文件，拒绝覆盖。规则 overlay 使用显式只读挂载。此次只检查 Compose 展开有效，真实运行使用专用临时容器；未启动长期 Compose 部署，未测试 AMD64、TLS 网关、备份恢复或外部服务器发布。

| 验收 | 本地结果与证据 |
| --- | --- |
| PostgreSQL 事务、认证、刷新竞争、撤销、容量、输入边界、规则校验、真实 HTTP 强制重启 | 32 项通过，`var/p4a/acceptance-02.log` |
| 上述测试加 ARM64 镜像真实运行、依赖和路由隔离 | **33 项通过**，`var/p4a/acceptance-image-02.log`、同名目录的 results.xml |
| core/provider/protocol/边界/公开导出回归 | **216 项通过**，`var/p4a/contracts.log` |
| wheel/sdist/npm、仓库外离线安装和 API 包内 schema/SQL 冒烟 | 门禁通过，`var/p4a/build-final/BUILD-REPORT.json` |
| 源码/未跟踪文件/导出制品检查、共享协议生成与冻结代码核验 | `var/p4a/build-final.log` |
| npm 制品仓库外消费者 | `var/p4a/npm-final.log` |
| 原人机入口、旧 v1 capabilities、局域网 HTTPS capabilities | 均为 HTTP 200，`var/p4a/service-health.json` |

首次镜像测试遇到 Docker 端口已接通但应用尚未监听造成的 RemoteDisconnected，修正测试启动等待后重新执行完整 33 项；没有把未就绪容器记为通过。框架在 Python 3.14 下有弃用警告，未隐藏；这不代表已兼容未来 Python 版本。GitHub Actions 已加入独立 PostgreSQL 测试服务，本次未触发远端 CI；镜像测试仍是显式本地验收。

## 7. 复现与回退

创建独立 Python 3.14 虚拟环境，安装迁移测试工具、服务固定依赖、httpx 0.28.1 和三个共享包及 online-server 后，可执行：

```sh
docker pull postgres:17
docker build -f ops/online/Dockerfile -t ptcg-lab-online-api:p4a .
python scripts/migration/p4a_postgres.py \
  --output var/p4a/new-acceptance \
  --python /path/to/dedicated/venv/bin/python \
  --image ptcg-lab-online-api:p4a
```

输出目录必须不存在。脚本创建自己的 PostgreSQL 容器，使用临时内存数据目录、环回 55484 和 8784；每个数据库测试另建独占数据库。测试结束只移除自己创建的容器和凭据文件，保留去敏测试结果。没有显式配置专用测试 DSN 时数据库测试会跳过，不能据此宣称认证验收通过。

完整制品门禁与 npm 消费验证：

```sh
python scripts/migration/gate.py \
  --build-output var/p4a/new-build \
  --dependency-wheelhouse var/p4a/wheelhouse
node scripts/migration/p3_package_smoke.mjs var/p4a/new-build
```

新 API、数据库和镜像均独立。代码回退到 P3-C 或只停止自己的 v2 Compose 项目即可恢复到实施前的产品入口；不会把新数据库合并回旧服务。此次没有重启现有人机、局域网或 AI 进程，没有改写研究工程、冻结规则、AI 策略或运行资产，也未执行 push 或发布镜像。

## 8. P4-B 实施约束（已完成，详见独立验收记录）

先建立服务端卡组 revision 和房间成员授权，再实现房间版本、准备幂等与唯一开局任务。邀请码只负责发现房间，不等于对局或私有回放的授权。用户 ID 与座位由已认证的持久成员关系推导，不能接受客户端自报。

对局 ID、种子、固定规则身份和双方卡组 revision 在调度执行前持久化；并发准备、初始化失败重试和 API 重启不能重抽起手。房间初始化与引擎运行使用独立边界，不在数据库事务内运行耗时规则。无合法批准规则或 worker 不就绪时拒绝开局。双人快照、原子命令、WSS 和完整自然终局分别在 P4-C/D 完成，不提前将房间功能标为完整在线对战。
