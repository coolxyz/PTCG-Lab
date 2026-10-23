# ptcg-engine-provider

P2-B 的显式规则资源输入、不可变目录与哈希核验。仅使用 Python 3.14 标准库，没有 APP、数据库、GUI、AI 或上游引擎运行依赖。包和子模块导入不读取工作区数据；仍未接入产品，`IMPLEMENTATION_READY=False`。

- `bundle.EngineProvider` 定义资源准备合同；`DirectoryEngineProvider(root, expected_manifest_sha256).load()` 校验调用方固定的清单，返回不可变 `RuleBundle`。
- `RuleBundle` 包含核验后的引擎源码 bytes、赛制/范围/效果/映射/卡牌规格快照及目录；不修改 sys.path、不导入或执行引擎、不搜索默认目录、不回退到原版引擎。
- `catalog.CatalogProvider` 定义查询、规范化与解析接口；`MemoryCatalog` 将查询绑定到一份快照。`resolve_engine_lines` 只检查映射和效果审核标记，不代替卡组赛制合法性、效果准入或开局。
- `scope_support` / `effect_support` 对规范化后的卡表查询固定范围/效果清单。不会自动读取历史清单或当前磁盘上的最新版本。
- `rule_release_id(core_sha256=..., provider_sha256=...)` 按资源身份和两项制品哈希计算新的候选执行身份；调用方须核实制品来源。它不是旧 implementation_id 的别名，也不意味着已有可执行核心。

清单是私有构建输入：schema=1、engineAbi=`ptcg-rules-input-v1`、upstreamCommit、components。每个 component 明确 name/role/path/sha256/size；只读取登记的普通文件，拒绝符号链接、越界路径、大小不符、哈希不符及缺少必需角色。root 必须来自可信部署配置，目录应保持只读；内容哈希不能证明许可或建立来源信任。类不是针对恶意同进程 Python 代码的安全沙箱。

详细清单格式、信任约定、限制、私有准备工具与实测范围见仓库 `docs/P2B资源Provider与固定资产验收.md`。实际引擎工厂、规则提取和旧路径适配属于 P2-C。
