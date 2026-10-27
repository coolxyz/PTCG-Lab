# P2-D 等价与隔离验收

2026-10-27 main 整合复验：规则／协议修复后，重新安装新 wheel 并执行 4 场自然对局、825 步、8 个定向场景、32 个检查点和 5 次跨进程恢复，166 项规则回归通过。当前公开报告绑定本次源码与实际证据；下文描述原阶段历史验收，详见 [main 整合记录](main整合与复验记录.md)。

日期：2026-10-24。P2-C 已提交为 `1a7cc47`，是本阶段的回退点。本阶段在 PTCG-Lab 验收共享核心与协议，不切换旧 APP、局域网服务、用户数据库或 AI 入口。P3—P8 仍未实施。

## 1. 验收方法

- 同一固定 bundle、原始卡组与种子，先在独立的冻结旧运行时生成命令轨迹，再用新核心逐条执行。Provider 的规范化顺序与 P2-C 相同，双方实际引擎输入一致。
- 每个状态点比较完整双方投影的哈希（包括合法选项、候选及顺序）、私有规则状态/RNG 摘要、公开事件动画和胜负说明。没有只比较终局或只验证“能跑完”。
- 对同样四局再随机交错调度，逐点与单独运行对照；每一步检查其他三局的摘要不变。各局执行到第 25 步后独立恢复，继续同一轨迹。
- 对自然对局中首次出现的不同暂停类型恢复并继续；另从真实检索、领赏、替补、非回合方和摆放选择记录，在全新进程恢复并继续到终局。定向注入局面不伪装成可持久化的生产恢复记录。
- 八个定向场景：隐藏领赏、明示奖赏、昏厥后替补、完整牌库检索、私有顶部选择、非回合方隐藏选择、动态效果显示、硬币 RNG。执行相同场景和选择的新旧结果逐点一致；暂停时推进另一个对局不改变本局。
- 每步经实际 v2 校验，再反向映射回旧 DTO；与原投影精确相等。动画与胜负结果也进入 v2 的明确字段。回放页只从同一局同一成员的已授权快照构造，删除操作能力。

工具：`scripts/migration/p2d_worker.py` 是独立 worker，`p2d_acceptance.py` 负责干净 venv、离线 wheel 安装及跨进程验收；`core_regressions.py` 将原纯规则测试复制到临时目录，使用 fixture-only Adapter 提供显式预设行，不修改生产兼容层。

## 2. v2 的补全与有意差异

Python core/protocol 包为 `0.2.0.dev1`，TS 为 `0.2.0-p2d`。协议仍是未对外稳定发布的 `2.0` 开发合同；正式发布后不能照此方式随意改变合同。

`ptcg_protocol.projection` 不导入引擎、APP、数据库或 AI，只接收已经按成员视角过滤的投影。它提供 snapshot、legacy_view、replay_page、core_command；宿主仍负责认证、所属对局、持久化命令幂等、租约和序号，不从规则 stateVersion 推导这些服务端事实。

| 补全 | 约束 |
| --- | --- |
| CardIdentity.presentation | 明确的 VisibleCardPresentation，封闭字段；保留攻击/特性、能量/附着/进化、最大 HP、异常状态、封锁、伤害修正、保护与恢复效果等公开内容；不接受任意 raw 对象 |
| 动态视角 | turnFlags、timestep、选择状态、终局原因、公开设置事件/揭示、startingPlayer、catalogVersion；保持旧值及缺省字段语义 |
| 操作与检索 | 保留旧选项描述、攻击信息和公有引用；label 按旧语义可选，不生成一个 label 后再覆盖旧值；DeckInspection 仍无序、只向合法行动方提供 |
| 明示奖赏选择 | 隐藏选择中的公开卡必须带 revealedPrizeIndex/Side，并与视角内同位置的公开奖赏卡内容一致；其余隐藏候选仅 ref |
| 动画与终局 | visualEffects 与 result 是封闭结构，和公开帧一起进入只读回放 |
| 新旧投影差异 | 新核心仅给已明示的奖赏候选增加上述两个公开绑定字段；等价比较只剔除这两个新增字段，协议校验另行逐项验证绑定。没有忽略其他字段或改写历史摘要 |

普通快照、事件和本人回放均无种子、私有恢复记录或对手隐藏区。终局仍不自动公开对手手牌。公开的起手失败牌、效果明确揭示的手牌、已翻面的奖赏可以出现在公开事件中，这是规则授权的信息。卡图及图片下载依然不进入服务协议。

本阶段为确保 GUI 迁移不丢信息，保留了明确的丰富展示字段；静态卡文的本地解析、增量传输和带宽优化留给 P3/P5/P6。本阶段没有接通新 GUI 或 API 服务。

## 3. 隔离与负向验证

- 对手手牌的身份、双方未明示奖赏的身份、双方牌库顺序改变时，无相应查看授权的视角必须不变。当前合法查看选择不被错误当成秘密。篡改返回 DTO 也不能改变规则状态。
- 隐藏候选伪造公开奖赏位置、另一侧位置、不同内容或缺少绑定均拒绝；额外 recoveryRecord 字段在根、观察、卡牌、选项与回放中拒绝。
- 每条合法命令前检查错误行动方、过期版本、非法选择均不能改变状态；合法重试回执一致，冲突复用 commandId 被拒绝。
- 命令权限依据 decision.actor，不依据回合方。非回合方决策能够继续，另一个成员不能代为操作。
- 共享定义与注册表：整个验收前后对固定 specs 和所有注册类新建卡牌的公开定义做指纹对照。测试还检查全局 random 状态不变，所有随机性由局内 RNG 驱动。

| 共享项 | 本阶段结论与边界 |
| --- | --- |
| 引擎 registry、已编译规则类、Plain specs | 一个 worker 固定 bundle，激活时预加载；不得在运行中热更新。验收前后定义指纹不变，各对局使用独立卡牌实例 |
| final_trainers.evolution_links 的缓存 | 只从固定 specs 构建演化查询表，不存对局状态；与当前单 bundle worker 约束一致 |
| setup_doll 的生成类型、overlay 修正 | 激活阶段建立，绑定固定核心与资源身份；不在每局修改模块代码 |
| commands、receipts、状态、RNG、生成器 | 局实例所有；逐步交错、重试及各自恢复检查通过 |
| 调度边界 | 同一局必须串行执行；不同规则版本分进程。没有声称同一局支持任意线程并发，也没有做吞吐/服务器规格承诺 |

## 4. 本地证据与复现

最终本地结果及公开源码绑定记录在 `releases/p2d-acceptance.json`。真实输入、命令轨迹和私有恢复记录只在忽略的 var 目录；公开报告不包含卡组、种子或隐藏视角。

最终验收结果（2026-10-24）：

| 验收项 | 结果 | 本地证据 |
| --- | --- | --- |
| 公共合同与发布门禁测试 | 199 项通过 | `var/p2d/contracts.xml` |
| 冻结旧产品与规则回归 | 169 项通过 | `var/p2d/legacy-regression.xml` |
| 原纯规则测试迁至新核心 | 166 项通过 | `var/p2d/rule-regression-final/tests.xml` |
| 完整自然对局等价 | 4 局、825 条命令、829 个状态点，双方投影、私有摘要、动画及结果一致 | `var/p2d/outside-01/ACCEPTANCE.json` |
| 多局交错隔离 | 同 4 局随机交错 825 条命令，与各自独立运行一致；每步其他局不变 | 同上 |
| 恢复与定向局面 | 32 个自然暂停点恢复、5 次全新进程续跑至终局、8 个定向场景通过 | 同上 |
| 独立安装及产物 | 7 个 wheel、7 个 sdist、3 个 npm 包构建和审计通过；仓库外安装及加载通过 | `var/p2d/build-final/BUILD-REPORT.json` |
| 既有服务只读检查 | 练习、本地对战、局域网 TLS 入口均为 HTTP 200，三个已知原进程仍存活 | `var/p2d/service-health.json` |

真实对局验收使用独立临时 venv，只安装 core/provider/protocol 及其运行依赖，无 GUI、AI 或 Web 服务依赖。验收身份为 `rules-core-v2-716c950da3db3b95b2cda3801681fdda579ddeb3674eba9180a9bd3c2f3b7a1e`。完成 README 后重新构建的三个关键 wheel，与已执行真实对局的 wheel 逐项核对了所有非 dist-info 文件，运行代码与资源完全相同；产物哈希和对照保存在 `var/p2d/final-wheel-equivalence.json` 及公开报告的 `finalWheelRuntimeEquivalence`。构建元数据变化不作为重新执行完整对局的证据。

本轮只写入 PTCG-Lab 与临时验收目录，没有重启既有服务、切换生产入口、修改研究项目或 AI 任务配置。HTTP 与进程存活检查证明检查时服务可用，不等于本轮重做了浏览器端完整交互验收。

```sh
# 在独立工具环境构建，禁止使用正在提供服务的 Python 环境。
python scripts/migration/gate.py --build-output var/p2d/new-build \
  --dependency-wheelhouse var/p2/wheelhouse
python scripts/migration/p2d_acceptance.py \
  --bundle <已核验资源目录> --pin <manifest-sha256> \
  --cases <私有案例JSON> --legacy-root <冻结基线副本> \
  --wheels var/p2d/new-build --wheelhouse var/p2/wheelhouse \
  --output var/p2d/new-acceptance
python scripts/migration/core_regressions.py \
  --source <冻结基线副本> --bundle <已核验资源目录> \
  --pin <manifest-sha256> --output var/p2d/new-rules
```

case 文件是数组，每项含 config：seed、deck1/deck2 明确引擎行，以及 provenance 的 catalogVersion、formatAsOf、printing 条目。实际案例只在私有目录准备，不能让玩家提交这些测试接口或恢复记录。

原规则的 test_catalog 依赖未随公开基线导出的历史 engine-catalog 夹具。本轮从研究目录只读取得该历史 JSON，核对结构并记录 SHA，再复制到隔离副本；没有读写研究数据库或执行研究代码，夹具不会进入公开源码。

公开 CI 运行合成合同、发布审计和源码绑定门禁；真实资源验收是本地独立执行，并非远程 CI 已重跑。`check_p2d.py` 在 P2-D 标记完成时检查证据覆盖和已验收 core/provider/protocol/测试工具的源码哈希。改动这些输入后必须更新受影响验收，不能只保留 completed 标记。

## 5. 完成条件与后续

P2-D 的完成范围是当前固定资源包下，本节记录的规则回归、完整对局等价、恢复、隐藏信息及交错隔离验收，不是所有卡牌组合的形式化证明，也不是 P6 的容量压测。已有服务继续用冻结旧运行时；新身份仅用于明确新建的局。

下一阶段是 P3 的共享 GUI 与本地产品接入；P4/P5 处理纯 API 服务、网络会话、持久化与客户端连接；P6 才验证多进程调度和容量。回退本阶段只涉及新包、测试与文档，原用户数据和在线对局不需迁移。
