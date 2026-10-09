# 开发与贡献

请先阅读 README、THIRD_PARTY_NOTICES.md 和 SECURITY.md。提交项目原创代码时按根 MIT 许可证贡献；不要提交无权许可的第三方素材或私人记录。

Python 3.14+、Node.js/npm、Git 为基础依赖。公开源码不含卡牌数据和引擎运行资产；先按 `docs/GitHub发布与隐私.md` 配置合法、可信、兼容的本地资产，再运行 `bash start.sh --setup --check`（Windows：`start.ps1 -Setup -Check`）。不要通过放宽完整性检查强行加载不兼容引擎。

```bash
PYTHONPATH=runtime/engine:.:tests/rules .venv/bin/python -m pytest tests/collection tests/battle tests/sync -q
npm --prefix apps/web run build
```

某些目录/来源测试依赖独立保管的第三方测试资料，不包含在公开源码包。缺少资料时请只运行不依赖它们的测试，不应伪造全量测试通过。新测试尽量使用自创的合成夹具，避免把整份上游资料纳入仓库。

代码目录及运行边界见 `docs/架构与维护.md`。规则版本和对局快照须保持可追溯。可修改本机草稿，不能为修复界面而改写历史比赛结果。

提交前检查 `git diff --cached` 和公开源码审计。PR 请写明问题、行为变化、测试及剩余限制。不要附私人迁移包、截图中的个人记录或完整日志路径。
