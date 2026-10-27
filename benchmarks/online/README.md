# 在线容量验收

P6-A—E 已完成。P7 已开始：独立总资源组、真实协议驱动、短试跑与基础故障检查；正式容量矩阵与两小时长测尚未完成。以下保留 P6 微基线命令，P7 流程见后文。

使用独立 PostgreSQL 测试容器和新数据库。`PTCG_V2_TEST_DSN_FILE` 指向私有 DSN 文件，数据库名必须为 ptcg_p4a、host 必须为 127.0.0.1；测试为每个用例创建独立数据库并在结束时删除，不能使用用户数据库。

```sh
# 在已安装固定 API 依赖的测试 Python 环境运行；报告目录为新的 var/p4d 子目录。
python -m pytest tests/online_v2/test_p6_baseline.py -q
# 完整证据还需要 PTCG_V2_REAL_INPUTS_FILE、PTCG_V2_TEST_IMAGE、
# PTCG_V2_TEST_DB_CONTAINER、PTCG_V2_REPORT_DIRECTORY（均显式配置）。
mkdir -p var/p4d/your-run
python -m pytest tests/online_v2 -q --junitxml=var/p4d/your-run/results.xml
python scripts/migration/p4d_report.py var/p4d/your-run
# 先审阅并更新 releases/p4d-acceptance.json，再生成 P6-A 聚合报告：
python scripts/migration/p6a_report.py var/p4d/your-run
```

固定两种模式：有界 NullConnectionPool 与 ConnectionPool；每种 3 次，每次 5 局，每局 20 次空闲轮询、4 条命令、4 次同请求重试、双视角回放和隐私核对。没有网络、动画或真实规则计算的容量结论。真实规则另测 5 步冷恢复，结果也不是长局/1000 步 SLA。详细配置、计时语义、回退与后续阶段见 [P6 实施记录](../../docs/P6多局调度与性能实施记录.md)。

P6-B 的 `test_stream_idle_sql_is_constant_and_private_frames_are_not_loaded` 测量同一场合成对局的 1／8／32 条订阅各 10 轮空闲检查；不是多场活跃对局压测。完整验收后先生成并更新 P4-D 报告，再运行 `python scripts/migration/p6b_report.py var/p4d/your-run`。P6-A 报告作为历史检查点保留，不能用新源码摘要覆盖旧结果。

P6-C 的 `test_scheduler_fifo_round_robin_and_duplicate_merge` 用显式屏障控制执行顺序，验证 3 组、7 个不同任务和 3 次合并重试；并发 HTTP 测试验证排队后的撤销／接管／认输、回执与不同调用方身份。完整服务验收后生成 `python scripts/migration/p6c_report.py var/p4d/your-run`；旧 P6-A/B 报告不覆盖。这些是正确性与有界性证明，不输出吞吐、容量或延迟 SLA。


## P7 独立验收工具

使用已批准且与目标镜像执行身份匹配的私有输入 JSON（directory／pin／release／deck1／deck2）。不要直接拿宿主 Python 的执行身份用于容器；解释器 patch 不一致时必须拒绝，不能跳过 pin。生成器不用 AI，只使用授权投影、合法候选与随机思考时间。

```sh
# 从仓库根目录运行；prepare 输出目录必须不存在。
python benchmarks/online/lab.py prepare --directory var/p7/new-one --inputs var/p7/approved-inputs.json --workers 1
python benchmarks/online/lab.py start --directory var/p7/new-one
# 工具试跑：不形成容量通过结论。
python benchmarks/online/lab.py load --directory var/p7/new-one --matches 10 --warmup 10 --steady 60
python benchmarks/online/lab.py sample --directory var/p7/new-one
python benchmarks/online/lab.py stop --directory var/p7/new-one
```

API／PostgreSQL 置于新建 cgroup v2 父组，总上限 2 vCPU／2 GiB，禁用 swap；API 1.25 CPU／1 GiB、数据库 0.5 CPU／512 MiB，余量留在父组。TLS 在 API 进程内。生成器单独限为 1 CPU／512 MiB，不在目标组内。本轮工具配置使用 Docker VM 核心 6—7 跑服务、核心 8 跑生成器，因此本机执行需这些核心可用；移到其他机器前明确适配 cpuset 配置，不把配额当作物理小机结论。

父组创建／移除需要 Docker VM 的受控 cgroup 管理权限，只创建随机 `ptcg-p7-` 名称；不修改根组或既有容器的 CPU／内存配置。服务仅连接专属 internal 网络，不需要暴露到 LAN；就绪通过容器内 TLS 检查，驱动固定访问测试别名。原服务只做健康与启动身份检查，异常时停止测试驱动。工具不提供任意目标 origin 参数，避免把压力发送到正在使用的 LAN API。

故障检查必须使用**另一个全新测试数据库**，其 SQL 证明要求只有该检查的一局：

```sh
python benchmarks/online/lab.py prepare --directory var/p7/new-fault --inputs var/p7/approved-inputs.json --workers 1
python benchmarks/online/lab.py start --directory var/p7/new-fault
python benchmarks/online/lab.py fault --directory var/p7/new-fault
python benchmarks/online/lab.py stop --directory var/p7/new-fault
```

覆盖空闲规则进程死亡、API 重启、专用 DB 暂停、未知结果与原 commandId 恢复，最终核对一局 6 次推进／6 条回执／14 帧。不是提交中精确故障或满负载矩阵。

正式样本必须用 `--label matrix --warmup 300 --steady 1200`，每档 10／25／50／100 局、至少 3 次新数据库重复；`--label soak` 至少 300 秒预热＋7200 秒稳态。CLI 拒绝将短时长标为正式样本。驱动目前提供正常思考基线与基础故障；复杂效果、长局固定输入、重连风暴、慢消费者、混合维护和精确提交边界仍须在 P7-B/C 补齐，不要把可传入这些时长当作已完成场景。

`stop` 先保存私有 PostgreSQL 自定义备份及最终资源计数，再只删除本次项目／卷和空资源组。私有 `var/p7/` 含卡组、原请求、token、seed／恢复记录，不能原样公开。`scripts/migration/p7a_report.py` 只导出 allowlist 聚合，绑定 P6 源码、实际观测／驱动闭包和工具摘要；正式矩阵未完成时始终 `capacityValidated=false`。详细进度见 [P7 实施记录](../../docs/P7容量与故障验收实施记录.md)。

## P7-B 正式矩阵

```sh
# 独占一个测试目标，串行执行；不要和另一组容量测试同时占用相同核心。
python benchmarks/online/matrix.py --directory var/p7/new-matrix \
  --inputs var/p7/approved-inputs.json --levels 10 25 50 100 --workers 1 2 --repeats 3 --publish
```

每个样本使用全新数据库，300 秒预热、至少 1200 秒实际稳态，保存备份后清理。单 worker 各档完成后再开始双 worker；某档延迟／CPU 不达标时保留该档三次重复，停止向更高档递增，随后允许另一 worker 配置从 10 局开始。任一首次通信／一致性／隔离错误则停止整组，不自动重试失败请求。`--levels` 只允许从 10 开始连续取档，`--repeats` 不得小于 3；完整无失败矩阵最少 24 个正式样本，另计开局和回放核对时间。

每种配置的缓存总额统一为 16（单 worker 16，双 worker 每个 8），其余 API／DB／TLS 配额相同。测试镜像可通过私有 `--fixture-seed` 固定初始开局种子和公开对局 ID；认证 token、邀请码保持原随机来源。相同档位／重复号使用相同初始输入和驱动抖动，SQL 仅导出初始开局指纹供核对。终局后的替换顺序受执行时序影响，属于重复抽样比较，不能宣称逐命令完全相同的轨迹 A/B。

`matrix.json` 在私有目录记录当前样本与逐次结论；`--publish` 在每个完整样本后只更新 `releases/p7b-matrix.json` 的允许字段。该进度报告不关闭 P7-B 或 P7，不发布容量承诺。开始前核对工具和运行闭包；矩阵中更改工具会阻止继续下一个样本。P7-A 报告作为历史证据原样保留，由 P7-B 固定其摘要并验证当前测量工具。

分段观测只装在测试镜像的 app 实例中，记录命令的排队、SQL 类别、事务提交退出、规则耗时和事件循环停顿，以及整轮 DB／WAL 增长；不记录 SQL 内容或身份。`waits.py` 是额外的只读等待采样器，只用于诊断，不随正式容量矩阵运行。所有正式样本保留 `fsync=on`、`synchronous_commit=on`，不能用关闭持久化来满足延迟门槛。

### 驱动修复后的正式重启

驱动的回放校验按服务端原有的 2 个回放名额排队，命令路径不受该队列阻塞；不自动重试命令或失败的回放 GET。全部请求按阶段统计首次失败，初始化期间已有的 403/FORBIDDEN 有限轮询单独记为 pending。实际请求错误、回放不一致、重复游标或收尾校验失败均阻断样本。报告新增回放队列等待和操作分类，不把回放 503 混同为命令失败或丢弃错误码。

`releases/p7b-before-driver-repair.json` 保留旧运行闭包的诊断与失败记录；当前报告保留其固定摘要和历史数据，新运行只使用修复后的闭包。主目录有并行产品改动时，应在固定基线工作树中执行，避免用新源码描述旧镜像的性能。
