# PTCG Lab

面向 Pokémon Trading Card Game（PTCG）的非官方开源工具，提供简体中文卡牌查询、收藏管理、卡组构筑和本地人机对战，并为规则模拟与 AI 策略研究提供开发基础。

应用通过浏览器使用，由本机服务和 SQLite 数据库保存资料。

## 功能

- **卡牌查询**：搜索卡牌，查看规则文字、扩充包与卡面版本。
- **收藏管理**：记录持有数量，管理同编号的不同卡面，导入和导出收藏。
- **卡组构筑**：创建、编辑和校验卡组，管理可用于对战的卡组版本。
- **人机练习**：使用内置 AI 对战，查看动作与投币动画，恢复对局并观看回放。
- **资料同步**：从兼容的公开 GitHub 数据仓库更新卡牌资料和图片。
- **模拟与研究**：使用规则执行、局面分叉、对局回放和评测工具开发 AI 策略。

AI 包含用于练习的 A1 策略和实验性的 A2 搜索原型。

## 快速开始

### 环境要求

- Python 3.14 或更新版本
- Node.js 与 npm
- Git
- 现代浏览器
- 首次安装时用于下载依赖的网络连接

### 准备本地资产

**本仓库不包含完整卡牌数据、图片和对战引擎运行时，克隆源码后需要另行配置本地资产。**

如果已有合法、可信且与当前代码兼容的完整本地环境，可在项目根目录导入所需资产：

```bash
python3 scripts/public/import_local_assets.py /path/to/your/trusted-private-project
```

Windows 使用 `python` 执行相同脚本，并将路径替换为实际目录。导入工具复制卡牌数据、引擎运行时和完整性清单；目标文件已存在时会拒绝覆盖。资产来源与配置详情见 [本地资产与公开源码说明](docs/GitHub发布与隐私.md#公开源码如何启动)。

### 安装与启动

在项目根目录执行以下命令。

**Windows PowerShell**

```powershell
.\start.ps1 -Setup
# 后续启动
.\start.ps1
```

**Linux / Bash**

```bash
bash start.sh --setup
# 后续启动
bash start.sh
```

启动后访问 **http://127.0.0.1:8765/**，保持终端运行，按 `Ctrl+C` 停止服务。默认服务仅监听本机地址。

| 操作 | Windows PowerShell | Linux / Bash |
| --- | --- | --- |
| 检查运行环境 | `.\start.ps1 -Check` | `bash start.sh --check` |
| 使用其他端口 | `.\start.ps1 -Port 8766` | `bash start.sh --port 8766` |

详细安装步骤、使用方法与故障排查见 [安装与使用手册](docs/启动使用与跨系统迁移.md)。

## 技术栈与目录

前端使用 React、TypeScript 和 Vite，后端使用 Python 和 FastAPI，数据存储使用 SQLite。

```text
apps/
  api/            后端接口
  web/            浏览器界面
packages/         收藏、卡组、规则、对战、模拟与同步逻辑
rulesets/         规则配置
scripts/          安装、数据同步、构建与开发工具
tests/            自动化测试
docs/             项目文档
```

本地运行时使用 `.venv/` 保存 Python 环境、`runtime/engine/` 保存引擎、`var/` 保存用户数据。这些目录由 Git 忽略。备份和恢复方法见 [安装与使用手册](docs/启动使用与跨系统迁移.md#备份恢复与升级)。

## 文档

- [安装、使用、备份与迁移](docs/启动使用与跨系统迁移.md)
- [架构与维护](docs/架构与维护.md)
- [同编号多卡面收藏](docs/同编号多卡面收藏.md)
- [上游卡库同步](docs/上游卡库同步.md)
- [公开源码、本地资产与隐私](docs/GitHub发布与隐私.md)

## 参与贡献

欢迎通过 Issue 报告问题、提出建议，或通过 Pull Request 提交改进。开发环境与测试说明见 [贡献指南](CONTRIBUTING.md)，安全问题的报告方式见 [安全说明](SECURITY.md)。

## 许可证与致谢

项目自有代码和文档采用 [MIT 许可证](LICENSE)。第三方代码、卡牌数据、卡图及商标遵循各自的许可与使用条件。

感谢 [ptcg-engine](https://github.com/gemelom/ptcg-engine) 提供规则引擎基础，以及 [PTCG-CHS-Datasets](https://github.com/duanxr/PTCG-CHS-Datasets) 提供简体中文资料来源。完整来源和版权信息见 [第三方声明](THIRD_PARTY_NOTICES.md)。

本项目与 Pokémon 官方无隶属或背书关系。
