# PTCG Lab

**开源 PTCG 工具与 AI 对战实验平台。**

面向 Pokémon TCG 的非官方开源工具，集成卡牌管理、卡组构筑、规则模拟、人机对战与 AI 智能体评测，当前以简体中文版为验证目标。项目重点是可复现的规则实现、对战工具和 AI 开发；当前 AI 能力与后续目标见 A2 开发计划。建议公开仓库名为 `ptcg-lab`。

自有代码采用 [MIT](LICENSE)。原简中资料仓库为 [duanxr/PTCG-CHS-Datasets](https://github.com/duanxr/PTCG-CHS-Datasets)，可在“数据与对战更新”配置兼容的公开 GitHub fork。详见 [第三方与版权声明](THIRD_PARTY_NOTICES.md)。

**GitHub 公开源码不附带完整卡牌数据、图片或运行时资产。** 先阅读 [GitHub 发布与隐私](docs/GitHub发布与隐私.md) 配置合法本地资产；下面的启动说明面向已具备完整本地环境的用户。公开源码包不能代替个人迁移包。

当前稳定版本：**1.0.1（2026-10-08）**。当前目标卡池自由对战已完成；下一阶段独立开发 A2 AI。

**首次使用请先阅读：[完整安装与使用手册](docs/启动使用与跨系统迁移.md)**。包含安装包校验、Windows/Linux 安装、上游资料与全部卡图下载、查卡、收藏与卡面、导入导出、组牌、人机练习、回放、备份恢复和故障排查。

支持查卡、同编号多卡面收藏、导入、自由组牌、A1 人机对战、动作与投币动画、恢复、回放和上游卡库同步。冻结环境 G/H/I/J 加八种基本能量的 **5,346 / 5,346** 个目标版本全部支持。资料库保留 **20,345 条上游卡面记录**，卡面与对战身份分别统计。

## 启动

首次安装需要 Python 3.14+、Node.js/npm、Git，以及下载依赖的网络。

Windows PowerShell：

```powershell
.\start.ps1 -Setup
# 以后启动
.\start.ps1
```

Linux：

```bash
bash start.sh --setup
# 以后启动
bash start.sh
```

打开 **http://127.0.0.1:8765/**，Ctrl+C 停止。Windows `-Check`、Linux `--check` 仅检查环境；端口用 `-Port 8766` / `--port 8766` 指定。脚本可从任意目录调用，服务仅面向本机。

## 文档

- [安装、使用、备份与迁移](docs/启动使用与跨系统迁移.md)
- [发布说明](docs/发布说明.md)
- [架构与维护](docs/架构与维护.md)
- [同编号多卡面收藏](docs/同编号多卡面收藏.md)
- [上游卡库同步](docs/上游卡库同步使用与验收.md)
- [A2 AI 独立开发计划](docs/A2-AI开发计划.md)

`RELEASE.json` 记录版本与冻结对战发布。`python scripts/release.py build --include-restricted-data` 生成不含个人数据的 release 包和校验清单；解包后运行 `python scripts/release.py verify`。

个人收藏、卡组与比赛位于 `var/app.sqlite`，上传卡图在 `var/app-card-images/`。普通安装包用户用 `scripts/sync/run.py backup --bundle ../ptcg-user-backup.zip` 备份数据与活动运行时；带根 `.git` 的源码工作区还可用 `scripts/portable/pack.py` 做完整迁移。命令需使用项目 `.venv` 内的 Python，恢复及离线卡图携带步骤见手册。release 包不能替代个人数据备份。

目录按功能命名：`rules`、`collection`、`battle`、`simulation`、`cardpool`、`sync`。Python 环境在 `.venv`，生成引擎在 `runtime/engine`，用户数据和本机临时文件在 `var`。旧发布与里程碑归档已清理；旧对局仅保留已保存帧的回放，不能继续执行。旧卡组自动按当前规则校验，通过即可用于新对局。第三方引擎和卡牌资料遵循原来源的许可证及使用条件。

贡献与开发：[CONTRIBUTING.md](CONTRIBUTING.md)。隐私与安全：[SECURITY.md](SECURITY.md)。
