# P2-C 核心提取与兼容层验收

日期：2026-10-23。P2-B 已提交为 `ba6d720`；此前 P2-A 为 `d99f52b`、P1 为 `83e3fb2`。本阶段只修改 PTCG-Lab 的新包、验证工具和文档，不修改研究项目，也不切换任何正在运行的服务。P2-D 尚未完成，不能把本阶段的提取冒烟验收当作完整规则与并发认证。

## 1. 提取范围

`ptcg-battle-core` 版本提升至 `0.2.0.dev0`，`src/ptcg_core` 是新规则演进的唯一位置。

| 原职责 | 新位置 | 边界 |
| --- | --- | --- |
| 纯规则效果、RulesEngine、规则 Adapter | `ptcg_core.rules` | 保留效果逻辑；导入改为新包；不读取工作区 |
| 命令幂等、选择绑定、状态摘要 | `ptcg_core.base` | 不包含尾部开发用弱策略；新 Match 入口增加严格命令形状检查 |
| 赛制准入 | `ptcg_core.cn_format`、`factory` | rules、日期、CatalogProvider 均来自显式参数/固定 RuleBundle |
| 普通卡、训练家、特殊能量规格 | `rules.plain` | 由固定规格快照提供；不寻找 data/cardpool |
| 双方视角、操作引用、提示文字 | `projection`、`prompts` | 保留旧界面的可见效果字段，不为了套入最小 v2 类型而丢字段 |
| 公开事件、结果说明 | `events`、`results` | 只处理可见视角，不引入持久化或传输 |
| 引擎模块与卡牌注册表 | `engine_loader`、`engine_registry` | 从核验后的显式模块清单加载；没有目录扫描或失败后静默跳过 |
| 卡组实例化 | `engine_deck` | 只接受内存中的明确引擎行；禁止文件名、预设名和当前目录回退 |
| 工厂与恢复接口 | `factory.CoreFactory`、`Match` | create_match / validate_deck / apply_command / project_view / export_recovery_record / restore_match |

不提取 `packages.rules.session`（SQLite）、`agents`（策略）、旧 catalog 中的卡图选择，以及 AI 搜索/训练、HTTP、GUI、收藏、个人数据库。新 wheel 的运行依赖仅为同仓库 provider wheel 与锁定的 loguru 0.7.3；不安装 AI/GUI/Web 框架。

## 2. 显式加载与进程边界

1. 部署方提供资源目录和 manifest SHA-256，DirectoryEngineProvider 全量核验后产生固定 RuleBundle。
2. CoreFactory 在接收对局前激活。导入 `ptcg_core` 或 `factory` 本身不读取工作区、执行引擎或启动进程。
3. 专用导入器只处理 `ptcg` 命名空间，源码来自已核验的内存字节；隐式 namespace 仅从该清单推导。未列出的模块直接拒绝，不能回退到已安装的另一套引擎。
4. 上游 overlay 中的静态 `packages.rules.*` 导入在编译前映射至 `ptcg_core.rules.*`。两个固定替代模块处理卡牌注册和卡组加载；它们属于核心身份，避免沿用上游扫描目录/寻找卡组文件的行为。
5. 所有卡牌模块在激活时装载并实例化校验。错误使该 worker 不可再次激活，必须替换进程。已装载旧 ptcg 的进程不能混用新核心。
6. 一个 worker 固定一个 bundle，重复使用同 bundle 允许，不同 bundle 拒绝。多版本由部署层分进程；多局可使用同版本工厂创建各自状态与 RNG，但同局串行调度以及完整交错隔离证据仍属于 P2-D/P6。

这是执行部署方信任并固定哈希的代码，并非运行不可信卡包代码的沙箱。不得让玩家指定 bundle 路径、pin 或 Python 模块。核心不修改 sys.path，也不导入研究工作区。

## 3. 兼容层与冻结旧运行时

直接改动旧路径会改变原 `implementation_id`，因此本阶段采用明确的双边界：

- 正在使用的旧 APP 继续读取原文件；`releases/legacy-core-freeze.json` 固定旧规则、适配器、投影、提示、公开事件/结果、构建脚本和受版本管理的规则身份输入。门禁拒绝这些文件变化或向旧 rules 目录添加新规则。
- 新 worker 显式执行 `install_legacy_imports(factory)` 后，受支持的旧名称只引用同一份 `ptcg_core` 模块/类。`packages.battle.runtime` 提供薄委托的 Adapter、view、PlayerId、RejectedCommand 和新 ENGINE_VERSION。没有复制效果实现。
- 兼容安装前发现旧 rules/engine_adapter/battle 已导入则拒绝；不热替换运行中服务。旧 namespace 不支持的 session、agents、catalog 也不会回退读取仓库文件。
- 新规则修复写入 `ptcg_core`。原文件是冻结历史运行时，不是第二个开发目标。P3/P4 接入新产品时主动选用核心；P2-D 完成前不切换原 APP 默认导入。

此决策把“旧导入薄兼容”和“现有服务不受影响”分开验证，更新了 P2 工作项的落地方式；不声称已经迁移旧 APP 的所有入口。研究项目仍可在自己的仓库继续 AI 演进，不受 PTCG-Lab 冻结门禁影响。

## 4. 新旧身份与恢复

新执行身份为 `rules-core-v2-<hash>`，包含固定 bundleId、安装的 core/provider/loguru 全部 Python 源文件内容、Python 实现及精确版本。只使用包内相对文件名，不包含仓库绝对路径或 wheel ZIP 时间戳。资源目录搬迁不改变身份；代码、依赖、Python 或资源变化均要求新身份。部署应将安装目录和资源视为只读。

P2-B 的 `RuleBundle.rule_release_id(core_sha256, provider_sha256)` 仍是发布组装用的 wheel 候选身份，**不是运行时身份的别名**。后续发布 manifest 须同时记录 wheel SHA 和实际执行身份，并验证对应关系；不能把任意调用方给的 wheel SHA 当作已经验证的执行环境。

新私有恢复记录 schema 为 `core-recovery-v2`，携带新身份、上游 commit、冻结引擎行、种子、来源、已接受命令和状态摘要。恢复先核对新身份，再重放并检查摘要。旧 `p01-replay-v1` 被拒绝；旧记录仍由原冻结运行时解释，不重写旧 releaseId/implementation/stateDigest。

恢复记录只用于受信任的服务端存储，不能暴露到网络回放接口。GUI 使用的本人可见回放将由后续适配层构建。本阶段 project_view 保留旧 DTO，尚未无损映射到 P2-A 的最小 MatchSnapshot；没有把旧 DTO 冒充成 v2 wire。新 Match 命令入口拒绝额外字段、bool 版本、混用两种 choice 和不匹配的选择类型；历史有效命令行为保持一致。

卡组准入先使用 Provider 的规范化顺序：按 printingId 排序、同 ID 合并，再实例化。本阶段新旧对照使用完全相同的冻结引擎行和 provenance；不声称原始 JSON 任意排列在旧 Catalog 和新工厂下都产生相同洗牌。

## 5. 验证方式与证据

公开 CI 只使用原创合成输入，运行包导入/受限模块装载、无路径注入、命令拒绝、固定策略准入、身份绑定、初始化失败封锁和旧运行时冻结测试。完整真实资源不会进入源码、wheel、sdist 或 npm 包。

```sh
# 独立开发工具环境，不能使用正在服务的人机/在线进程环境。
python -m pip install --no-build-isolation --no-deps \
  ./packages/engine-provider ./packages/battle-protocol ./packages/battle-core
python -m pip install -r packages/battle-core/requirements.lock
python -m pytest tests/core tests/providers tests/protocol tests/migration \
  tests/portable/test_public_export.py -q
python -m pip download --only-binary=:all: \
  -r packages/battle-protocol/requirements.lock \
  -r packages/battle-core/requirements.lock --dest var/p2/wheelhouse
python scripts/migration/gate.py --build-output var/p2c/new-build \
  --dependency-wheelhouse var/p2/wheelhouse
```

真实资源验收脚本为 `scripts/migration/verify_core_runtime.py`。它应复制到仓库外，在只安装新 wheel 的干净 venv 中以 `-I` 运行，显式传入 `--bundle`、`--manifest-sha256`、`--case`、`--legacy-root` 和 `--output`。case 包含 deck1/deck2 printing 条目、seed、asOf、steps，仅保存在忽略的 var 中。旧基线在独立子进程中运行，新核心进程不加载旧源码。报告不输出完整牌组、种子、视角或恢复记录。

本地结果（macOS / Python 3.14.8；Ubuntu CI 配置已更新但尚未远程运行）：

| 验证 | 结果 | 私有证据位置 |
| --- | --- | --- |
| core/provider/protocol/迁移/公开导出合同 | 182 passed，4.47 秒 | var/p2c/contracts.xml、contracts.log |
| 旧产品隔离副本回归 | 75 passed，77.27 秒；有原依赖弃用警告 | var/p2c/legacy-regression.xml、legacy-regression.log |
| 完整发布构建 | 7 wheel、7 sdist、3 npm；干净环境离线安装通过 | var/p2c/build-01/BUILD-REPORT.json |
| 仓库外实际对局 | 新 venv 仅 core/provider/loguru；203 条命令到终局、204 个状态点，双方完整旧投影与私有摘要逐点等价 | var/p2c/outside-wheel-runtime.json、outside-wheel.log、outside-installed.txt |
| 恢复、重试与兼容 | 第 25 步恢复后继续到终局；重试回执一致；旧名 Adapter 为同一类；旧 schema 拒绝；异 bundle 激活拒绝 | 同上 |
| 原服务只读健康检查 | 人机、本地在线、局域网 HTTPS 均 200；原 PID 保持运行，无重启 | var/p2c/service-health.json |
| 旧代码与公开边界 | 冻结检查、tracked/untracked 扫描、协议漂移、TS 合同、源码导出审计通过 | var/p2c/build-01.log |

本轮实际执行身份为 `rules-core-v2-6df21e0ae593b4925bfa931a8a7f2520723c2ab841981d7631a8ad15255f4cac`；对照旧 implementation 为 `243a0dbb3059c4c53c48cf4dd94fce562f4dcc2fed806956f504d333441cf4d4`。这是映射记录，不代表两者可互相恢复。bundleId 为 `bundle-v1-09d93abad7324701239ad1e69719709ab4d3330b9e3a1ffe6a926178dedadce3`。后续修改核心代码须重新构建、记录新身份。

没有重复运行浏览器基线；本轮 GUI/路由字节未变。P2-D 仍需更多定向场景，不能因为一局完整对照通过就结束整个 P2。

## 6. 剩余与回退

P2-D 需扩展真实规则等价覆盖、对手隐藏信息检查、完整可见字段到 v2 的映射、领赏/替补/检索/非回合方决策，以及两局交错推进和各自恢复。P3/P4 才切换产品入口。服务器容量优化、排队、持久化与水平扩展仍按 P6 设计执行，本轮未做容量承诺。

撤回本阶段的新包、兼容层、门禁和文档即可；不必停止现有服务、不迁移用户库，也不触及研究项目或已运行的 AI 任务。P2-C 尚未提交时，P2-B 的 `ba6d720` 是本轮明确的提交回退点。
