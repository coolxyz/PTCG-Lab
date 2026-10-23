# P2-B 资源 Provider 与固定资产验收

日期：2026-10-23。P2-A 已提交为 `d99f52b`，本地未推送。P2-B 已完成资源与查询层的本地验收；P2-C/D 未实施，P2 整体仍在进行。参见 [P2 进度](P2共享协议与规则核心实施记录.md)及[分阶段实施](产品化重构分阶段实施.md)。

本阶段全部开发在 PTCG-Lab；原 apps/api、apps/web、packages/rules、engine_adapter、battle、collection、simulation、cardpool 及启动文件均未修改。研究工作区未写入，不重启现有服务，不改用户数据库、模型、训练任务或旧 releaseId。

## 1. 交付与接口

engine-provider 从空骨架升级为 `ptcg-engine-provider 0.2.0.dev0`，只使用标准库。新核心未来单向依赖该包，Provider 不回头导入核心、旧 packages、APP、AI 或上游 ptcg。

| 对象 | 当前实现 |
| --- | --- |
| CatalogProvider | 目录版本、card、normalize、resolve 的结构合同 |
| MemoryCatalog | 只读卡牌映射；合并相同 printingId、排序、数量与输入形状校验；返回不可变条目 |
| EngineProvider | `load() -> RuleBundle` 资源准备合同，不是引擎执行合同 |
| DirectoryEngineProvider | 显式根目录与外部固定的清单 SHA-256；按清单校验、读取并保存资源快照 |
| RuleBundle | 不可变目录、赛制、范围、效果、映射、卡牌规格、引擎源码 bytes；不再次读取磁盘 |
| JsonSnapshot | 原字节哈希、递归只读的 JSON；copy() 返回独立副本 |

包导入与 Provider 构造不读目录、不建缓存、不联网。load() 是明确的 I/O 边界；使用导入时 I/O 拦截、空目录、安装 wheel 的新解释器验证。引擎 bytes 保存在快照中，本阶段不会把它们加入解释器搜索路径或执行。

`normalize` 保持旧收藏领域的规范化范围：最多 500 行、每行整数 1—60、同版本合并不超过 60、总数不超过 600，返回按 printingId 排序的条目。这是目录/草稿查询功能，不是 60 张合法开局校验。真实对战仍需 P2-C 的赛制、效果支持、范围和引擎构建校验。

`resolve_engine_lines` 只允许有审核证据和 verified 标记的单行映射；拒绝换行注入。`scope_support` 和 `effect_support` 使用固定快照查询，结果带对应资产哈希。它们保留旧数据查询行为，不在本阶段修改规则政策。

## 2. 清单、信任与身份

私有 bundle 根目录包含 bundle.json 和它登记的资源。清单结构：

```json
{
  "schema": 1,
  "engineAbi": "ptcg-rules-input-v1",
  "upstreamCommit": "40-character-lowercase-hex-commit",
  "components": [
    {
      "name": "catalog.json",
      "role": "catalog",
      "path": "assets/catalog.json",
      "sha256": "64-character-lowercase-hex-digest",
      "size": 1234
    }
  ]
}
```

上例仅解释结构，省略了必需组件，不能直接加载。完整清单必须具有各一份 catalog、format、scope、effects、mapping、specs，以及 license 和至少 ptcg/__init__.py 引擎源码。引擎组件使用 ptcg/ 下的 `.py` 逻辑名；清单不接受图片、模型、SQLite 等角色。

调用方必须从可信部署配置传入 `expected_manifest_sha256`，不能从下载包自己的旁置文件自动读取并信任。私有准备工具输出的摘要供操作者核对和固定，不是来源认证或签名。目录必须由可信操作者控制并只读部署；本阶段不提供下载、签名验证、在线更新或恶意主机文件系统沙箱。

加载器先检查完整清单，再读取登记文件。拒绝未知字段/角色、重复逻辑名或路径（含大小写冲突）、不兼容 ABI、缺项、绝对/越界/Windows 设备路径、符号链接、非普通文件、哈希和长度不符。限制清单 1 MiB、组件数 4096、单文件 64 MiB、资源总字节 128 MiB、JSON 嵌套 64；这是输入预算，不是进程内存峰值承诺。

资源读取后冻结，源目录后来变化不会改变已有 RuleBundle。再次 load 必须重新核验；不会悄悄切到新目录或未打补丁引擎。未登记文件不读取，也不因它们存在就导入执行。

身份分为三层：

1. **manifest_digest**：清单原始字节的哈希，绑定路径和文件读取计划。
2. **bundle_id**：ABI、上游提交和按逻辑名排序的组件内容哈希/长度/角色；不含物理路径。移动目录或改变受控存储路径不改变资源身份。
3. **候选 rule_release_id**：bundle_id + core wheel SHA-256 + provider wheel SHA-256。执行代码或依赖的资产变化产生新身份，不映射成旧 implementation_id。

当前还没有可执行的新 core 发布，因此没有启用任何新 ruleReleaseId。该计算方法仅供 P2-C 发布装配使用；调用方必须核实所传制品哈希对应实际安装物。旧已冻结对局不迁移、不重写摘要。

## 3. 资产投影与私有准备工具

新增 `scripts/migration/prepare_rule_bundle.py`，只从显式可信目录读取固定文件：

- data/catalog/catalog.json → 规则语义目录。
- rulesets/cn-standard-2026-09-16.json、battle-scope、effects、plain-pokemon → 原字节快照。
- data/engine/catalog.json → 仅规则印刷映射。
- runtime/engine 中旧 overlay-hashes.json 登记的源码 → 逐文件校验后复制；上游许可单独保留。

主目录与引擎映射都去掉图片、展示名、来源页面、预设卡表和收藏字段，仅保留打印身份、同名限制键、赛制属性、效果审核、引擎身份/映射等明确字段。effectEvidence 转成“是否存在”的布尔值，与旧映射准入的判断一致。只改图片或展示名称不会改变这两份投影或 bundle 身份；已有测试覆盖两个来源目录同时换图。

该工具不复制整个工作区，不读取 var 用户库、.catalog 账号、模型或未登记的引擎文件。输出必须是新目录；在源码树内部只能置于 var 下。全部内容通过加载器验证后才写最终目录，失败不留下可用的半成品；不覆盖已有 bundle。

```sh
# 使用已安装新 Provider 的独立验收环境。
var/p2/tools/bin/python scripts/migration/prepare_rule_bundle.py \
  --source /path/to/trusted/frozen-inputs \
  --output var/p2b/new-private-bundle \
  --expected-overlay-sha256 REVIEWED_OVERLAY_MANIFEST_SHA256
```

摘要占位值需替换为已核对的 64 位哈希；不要借此跳过旧 overlay 完整性核验。源资产应固定，避免在更新中的目录上组装跨版本数据。输出包含受限数据与引擎，保留于忽略的私有目录，不进入 Git、公开源码、公共 CI artifact 或公开镜像。保存许可不等于取得额外分发权。

## 4. 实际验收

本地环境沿用 P1/P2 的独立 Python 3.14.8 环境；未安装或升级旧 APP 依赖。当前结果：

| 检查 | 证据与结果 |
| --- | --- |
| 合同与发布门禁 | `var/p2b/contracts.xml`：161 项通过，其中 47 项 Provider/准备工具测试 |
| 真实资源核验 | `var/p2b/asset-bundle-02/PREPARATION.json`：146 个组件、139 个引擎文件，未执行引擎、未复制用户数据库 |
| 查询层对照 | `var/p2b/asset-equivalence.json`：12,807 个印刷版本的规则字段、效果支持、对战范围与旧查询一致；两个已有模板规范化结果一致 |
| 独立制品 | `var/p2b/build-01/BUILD-REPORT.json`：17 个制品，安装到仓库外干净 venv 后通过 Provider 合成加载/查询验证 |
| CI | 已纳入公开合成 Provider 测试和独立 wheel 验证；远程 Ubuntu 工作流尚未执行 |

合成测试还覆盖只读快照、调用方/源文件后续修改、两个不同资源实例交错查询、重新加载拒绝被篡改文件、清单固定、路径拒绝、错误脱敏和未登记私有文件排除。测试中的合成引擎一旦执行就会抛异常，用于验证 Provider 不执行它。

“两个 Provider 实例交错查询”不等于“两局引擎交错推进”；真实核验只覆盖目录和资源层，没有执行完整对局、RNG 隔离、恢复续步或双方视角投影。也没有证明实际 Linux/Windows 安装通过。

复查公开测试和制品：

```sh
var/p2/tools/bin/python -m pip install -r scripts/migration/requirements.lock
var/p2/tools/bin/python -m pip install --no-build-isolation --no-deps \
  ./packages/battle-protocol ./packages/engine-provider
npm ci --ignore-scripts
var/p2/tools/bin/python -m pytest tests/providers tests/protocol \
  tests/migration tests/portable/test_public_export.py -q
var/p2/tools/bin/python -m pip download --only-binary=:all: \
  -r packages/battle-protocol/requirements.lock --dest var/p2/wheelhouse
var/p2/tools/bin/python scripts/migration/gate.py \
  --build-output var/p2b/new-build --dependency-wheelhouse var/p2/wheelhouse
```

沿用已创建的 var/p2/tools；不存在时先用 `python3.14 -m venv var/p2/tools` 创建。公开测试只用源码内原创 Python 夹具，不读取真实卡池；真实查询对照另需可信资产，因此不冒充公开 CI 已验证。

## 5. P2-C 的下一步

资源准备接口已具备，下一步须将实际规则/引擎工厂改为消费这些显式输入，处理旧引擎源码对 packages.rules 的引用、注册表、plain.py 导入时读取和旧路径兼容。不能直接执行这个输入 bundle 并宣称已经完成独立核心。

P2-C 必须一次性明确哪些旧路径只是委托、哪些冻结旧运行时继续保留，并按新执行身份开新局。之后 P2-D 才验证同种子同命令行为、合法选项、双视角投影、恢复后继续执行和多局交错。当前共享 battle-core 仍是 P1 骨架。
