# ptcg-battle-core

P2-C 显式激活的规则核心，版本 `0.2.0.dev0`。未切换现有人机、局域网或 AI 的入口；`IMPLEMENTATION_READY=False` 表示产品迁移尚未完成。

```python
from pathlib import Path
from ptcg_engine_provider.bundle import DirectoryEngineProvider
from ptcg_core.factory import CoreFactory

# 路径与 pin 由部署配置提供，不能接受玩家提交的代码或资源路径。
bundle = DirectoryEngineProvider(Path(asset_directory), manifest_sha256).load()
factory = CoreFactory(bundle)  # 一个工作进程固定一个 bundle；应在接收请求前完成
match = factory.create_match(deck1_entries, deck2_entries, seed=seed, as_of=format_date)
view = match.project_view("PLAYER1")
receipt = match.apply_command("PLAYER1", command)
private_record = match.export_recovery_record()  # 仅服务端持久化，不能发给客户端
restored = factory.restore_match(private_record)
```

- 输入卡组为 printingId/quantity 数组，经目录、赛制、支持范围与效果准入；不接收卡图、伤害或卡牌实现。规范化后按 printingId 排序，同 ID 合并。
- 显式装载已核验的引擎源码字节；不改 sys.path，不扫描工作区。核心 wheel 不包含上游引擎、真实卡组、目录、图片、模型或数据库。另行提供有权使用的资源包。
- 根包与 factory 导入不启动引擎。引擎全局注册表固定在一个规则版本，初始化失败则替换进程；不同版本使用不同进程。宿主负责同一对局的串行访问。
- 当前 project_view/command 使用完整旧 GUI 数据形状；不是 P2-A v2 网络 envelope。传输映射、权限、控制租约、限流和持久化由后续服务层负责。
- 新恢复记录为 `core-recovery-v2`，绑定安装代码、依赖、Python 版本和资源内容。拒绝旧 `p01-replay-v1`，不能修改旧记录身份强行恢复。
- 新进程可先调用 `ptcg_core.compat.install_legacy_imports(factory)`，再导入受支持的 `packages.rules.*`、`packages.battle.runtime`、`packages.engine_adapter.base/cn_format`。这些旧名称仅指向新核心。SQLite session、AI agents、旧 catalog/UI 不在兼容范围。`Adapter` 的显式引擎行输入只适合可信内部调用；API 须使用 factory 准入。

完整提取边界、旧运行时冻结规则和验收命令见 `docs/P2C核心提取与兼容层验收.md`。P2-D 专项等价/并发隔离尚待完成。
