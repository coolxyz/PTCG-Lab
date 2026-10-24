# P4-D 完整对局与故障恢复验收

日期：2026-10-24。基线：P4-C 提交 `6801d38`。本阶段按 [分阶段实施](产品化重构分阶段实施.md) 完成纯 API 对战的自然终局、进程故障、未知提交与身份生命周期验收。**P4-A/B/C/D 已完成本地验收**；不改变研究工程、旧局域网服务或人机/AI 任务。

## 1. 验收范围与实现

P4-C 已提供权威命令、事务提交、WSS 和私有回放。P4-D 增加真实协议客户端、测试专用故障进程、凭证生命周期测试及可复现报告。服务版本与包说明更新为 `0.4.0.dev4`，业务运行逻辑和运行依赖不变。测试客户端仅使用 HTTP/WSS 接收的本人快照来选择动作，不直接调用引擎、不写局面、不指定生产开局种子。

`tests/online_v2/native_pair.py` 使用两个独立游客身份完成卡组提交、房间邀请、双方准备、控制权获取与原生 WSS 订阅。它是验收工具，不是 P5 的正式客户端，不包含持久凭证、自动刷新合并、完整重试退避或 GUI 集成。

`fault_server.py`、`fault_worker.py` 仅位于测试目录，不打包进 API wheel 或 Docker。测试启动器可显式选择该工厂；生产工厂没有故障开关或调试路由。运行服务的命令、数据库 schema、核心、Provider、协议和 AI 算法不因故障注入而改写。

## 2. 自然终局与私有视角

两名客户端使用批准的真实规则和已有隔离卡池，经验证证书的 HTTPS/WSS 完成完整对局。测试策略在需要选牌时选择合法最小集合，在可结束回合时选择 PassTurn；所有动作均经过服务端正常校验和规则裁定，直到核心宣布牌库耗尽的自然终局。测试没有认输、截断、强写胜负或改牌库；上限内未达到自然终局会失败。

双方同时订阅实时流，每次收到更新均验证共享 MatchSnapshot、成员视角、隐藏的对手手牌及决策所属座位。每 10 步及终局核对 HTTP 最新快照；第 20 步断开 PLAYER2 的流并以原游标重连。结束后分别分页读取两人的完整 ReplayPage，与各自曾收到的每一帧逐项比较，仅按共享转换去除 decision/控制权等不可回放字段。房间必须已关闭，双方胜方与终局原因必须一致。

首轮实测 97 个合法操作，PLAYER1/2 分别 48/49 次，双方共 196 帧逐项一致，终局原因为 `deck_out`。实际种子由服务生成，不固定该次数作为测试断言；后续运行的次数以对应汇总报告为准。认输、重启后认输及其私有回放沿用 P4-C 真实 HTTPS/WSS 测试。

这里证明真实自然终局及全程协议/持久化一致性，不代表覆盖所有卡包、攻击和特殊胜负规则。规则等价与专项规则覆盖仍以冻结的 P2-D 证据为准；本阶段不通过调整核心来让测试完成。

## 3. 故障矩阵

| 故障位置 | 注入方式 | 必须成立的恢复结果 |
| --- | --- | --- |
| 真实规则已算出候选、尚未返回 API | 暂停测试 worker 输出，强制结束本次 API 子进程 | 原事务未推进，无成功回执；规则进程发现父进程消失后退出；重启后原请求可成功一次 |
| 同一计算边界 | 强制结束标记了 PID 且父 PID 匹配本次 API 的规则子进程 | API 返回脱敏 RULE_RELEASE_UNAVAILABLE；原状态未推进；重新启动后同一请求只提交一次 |
| 数据库提交成功、HTTP 响应头尚未发送 | 在测试 ASGI 发送层暂停，再强制结束 API | 客户端未获得确认，但重启后可查到原回执；相同请求返回原回执，不增加版本或帧 |
| 规则计算期间令牌或会话到期 | 仅在该项测试独占数据库中推进到期状态 | 第二次鉴权拒绝候选结果，核心恢复记录和 matchSeq 均不变，无成功回执 |
| 运行中的流刷新令牌 | 调用真实会话 API 轮换 | 旧流和旧 access 被拒绝；同一 session 的新 access 保持控制权，可以继续提交 |
| 再次使用已消费的 refresh token | 调用真实会话 API 重放旧 refresh | 当前会话及新 access 撤销；新流关闭，快照、历史回执和回放也拒绝访问；已提交动作仍保留 |

三个真实进程故障用例都核对：固定种子、revision 与初始恢复记录不变；恢复后 matchSeq/stateVersion 为 1；数据库只有一条成功命令回执和四个成员可见帧（两人各初始帧与更新帧）；双方原游标各补发一条更新。测试屏障有 12 秒上限，测试进程有明确所有权，结束时只清理自己的容器和子进程。

P4-B/C 已有的并发准备、初始化租约、半途事务回滚、同 ID/不同 ID 竞争、控制权接管、撤销与技术中止期间候选拒绝、非法选项、错误座位、第三人访问、跨人/跨局游标、过期 outbox、慢发送和多局隔离，均在完整回归中继续执行。

未注入数据库主机掉电、磁盘损坏、主从切换或跨机网络分区；本次“进程恢复”指测试 API/规则进程，不能外推为数据库灾备结论。部署备份恢复属于 P8，多实例调度和容量属于 P6/P7。

## 4. 验收证据

| 验证 | 结果与本地证据 |
| --- | --- |
| 完整服务 + PostgreSQL + 真实 HTTPS/WSS + 原 P4-C 运行镜像 | **76 项通过，无跳过**；`var/p4d/acceptance-final-02.log`、同名目录的 `results.xml` |
| 自然对局 | **104 操作，双方各 52 次，210 帧逐项相同**；原因 deck_out；`var/p4d/acceptance-final-02/natural-match.json` |
| 三类真实进程故障 | 全部通过；同目录 `api-before-commit.json`、`rule-before-commit.json`、`api-after-commit.json` |
| 自动完整性汇总 | `var/p4d/acceptance-final-02/P4D-REPORT.json`；报告生成器拒绝缺项、失败或跳过 |
| P4-D 新版本镜像单独复验 | `var/p4d/image-final.log`、`var/p4d/image-final/results.xml` |
| core/provider/protocol、迁移边界及公开导出回归 | **220 项通过**；`var/p4d/contracts-final.log`，包括新增完成状态反例 |
| wheel/sdist/npm、仓库外离线安装与 API 包内 schema/SQL | 通过；`var/p4d/build-final/BUILD-REPORT.json`、`var/p4d/build-final.log` |
| npm 仓库外消费者 | `var/p4d/npm-final.log` |
| 原人机、旧 v1、局域网 HTTPS | 均 HTTP 200；`var/p4d/service-health.json` |

最终独立镜像为 `ptcg-lab-online-api:p4d`，ARM64 / Python 3.14.9，本地 ID `sha256:514bc0bf6a187796caf9318466c54fa72e2fe5545323bf8e1704f810faa16025`，大小 190,532,570 字节。它只更改阶段版本与说明；完整测试使用的 P4-C 镜像业务代码相同，P4-D 镜像另做非 root、只读根目录、安装、认证及路由隔离验证。此 ID 不是已推送的 registry digest。

真实规则在本机独立 Python 3.14.8 环境执行；镜像的 Python patch 不同，仍需针对最终生产运行时批准 ruleReleaseId，不能直接复用本机批准身份。没有验证镜像内真实卡池或 AMD64。本次没有触发远端 CI；Python 3.14 下框架弃用警告保留，未宣称支持未来版本。

首次完整回归出现一次 WSS 重连在测试客户端 5 秒握手时限内未完成，其余 75 项通过；此前同一路径的独立自然终局测试已通过。测试把握手时限改为有界 15 秒，覆盖握手中的持久限流和会话数据库操作，并记录实际耗时，不增加自动重试。最终通过运行的最长握手为 0.047 秒。保留失败日志 `var/p4d/acceptance-final.log`，不将该次运行算作完整通过；这也不构成生产握手延迟保证或已定位瞬时超时根因的声明。

真实资产输入仅通过私有 `PTCG_V2_REAL_INPUTS_FILE` 引用已有批准 bundle，没有重新从研究工程导出数据。没有此输入时真实规则测试会跳过；没有镜像参数时镜像测试会跳过。普通开源 CI 可以执行无资产部分，但不能以跳过结果宣布 P4-D 完整验收通过。

`scripts/migration/p4d_report.py` 检查完整 JUnit 中没有失败/错误/跳过、关键案例存在，并校验自然对局和三个故障汇总，才生成 `P4D-REPORT.json`。该报告仅包含测试数、操作/帧数量、终局原因、故障结果与服务源码摘要，不包含卡表、令牌、种子、恢复记录或完整对局数据。

去敏摘要登记为 `releases/p4d-acceptance.json` 并进入公开源白名单。发布门禁从原先无条件禁止声明 P4 完成，改为要求 A/B/C/D 全部完成、验收文档和有效摘要存在、摘要中的服务源码哈希与当前源码一致。缺失子阶段/文档/报告、无自然终局或服务源码未重新验收均会失败。该摘要是本地验收登记，不是远端 CI 重新执行真实资产的证明；后续修改服务源码须重新验收并刷新摘要。

## 5. 复现和隔离

```sh
docker build -f ops/online/Dockerfile -t ptcg-lab-online-api:p4d .
PTCG_V2_REAL_INPUTS_FILE=/path/to/private/approved-inputs.json \
  python scripts/migration/p4a_postgres.py \
  --output var/p4d/new-acceptance \
  --python /path/to/dedicated/venv/bin/python \
  --image ptcg-lab-online-api:p4d
python scripts/migration/p4d_report.py var/p4d/new-acceptance
cp var/p4d/new-acceptance/P4D-REPORT.json releases/p4d-acceptance.json
python scripts/migration/gate.py \
  --build-output var/p4d/new-build \
  --dependency-wheelhouse var/p4a/wheelhouse
node scripts/migration/p3_package_smoke.mjs var/p4d/new-build
```

输出目录必须不存在。验收使用临时 PostgreSQL、环回 55484、镜像检查 8784；真实 API 绑定系统分配的环回端口。数据库容器限定 0.5 CPU/256 MiB，用于功能验收，API 测试进程在宿主独立虚拟环境中；不能把本次耗时解释为某种服务器配置的容量指标。

旧 8765/8770/8443 服务只做只读存活检查。P4 v2 的数据库与旧局域网数据库分离；不重启旧服务、不迁移旧记录、不启动长期 Compose，不推送镜像。共享规则/协议冻结门禁与公开资产扫描仍必须通过。

## 6. 完成边界与下一阶段

P4-D 完整门槛已通过，P4 标记为“本地功能与恢复验收完成”，但 `productionRoutingChanged=false` 和 `IMPLEMENTATION_READY=False` 继续保留；后者表示尚未通过完整产品发布与 P8 运维门禁。不把 P4 验收完成解释为已面向公网部署。

下一阶段 P5 实现正式 online-client 与本地原生网络桥，处理能力协商、服务器绑定、凭证保存、刷新合并、待确认命令、重连及多窗口控制权，再让独立 GUI 使用网络模式。P4-D 的测试客户端不能直接充当 GUI 的凭证层。P6/P7 才优化单计算槽、每秒轮询、连接池和公平调度，并实测小配置的并发容量。

P4-D 不新增数据库 migration。回退至 P4-C 使用相同的 001—003 schema；回退到 P4-A/B 仍需要旧版兼容数据库或升级前备份，不能把带未来迁移的库直接交给旧服务。
