# 贡献与维护

main 是产品发布源码分支，功能开发使用 `codex/` 或其他独立分支。提交范围应限于产品代码、依赖、正式操作说明和必要的回归测试源码。阶段计划、实验/压测工具、个人工作日志、环境副本、数据库、凭据及测试输出不进入 main。

遵守 [当前规则约定](AGENTS.md)。不兼容旧规则执行版本；不能移除内容哈希、规则身份、授权、幂等或恢复检查。涉及引擎的修改要同时核对本机产品与共享核心，补充实际机制的回归测试。接口变更同时更新协议、客户端和共享 GUI。

```sh
npm ci --ignore-scripts
npm run product:build
python -m pytest tests/protocol tests/providers tests/packaging tests/portable/test_public_export.py -q
python scripts/public/audit.py --check
```

Python 测试需先安装各包锁定依赖和项目，生产数据相关测试还需配套私有资产。源码 CI 运行不依赖私有资产的合同测试；完整规则回归、浏览器验收和自然终局另行执行，不以跳过测试当作发布证据。维护规则的完整验证入口为 `scripts/sync/regression.py` 与 `scripts/sync/verify_runtime.py`。

提交前按 [构建与发布](docs/构建与发布.md) 检查目标制品。外部签名、最低系统、Windows 原生与干净机验收没有实际证据时保持待完成。不要提交 `release/`、`var/`、`data/`、`.catalog/`、`runtime/`、测试夹具或任何个人数据。
