# GitHub 发布与隐私

## 三种交付物不能混用

| 交付物 | 内容 | 是否适合公开上传 |
| --- | --- | --- |
| 公开源码包 `scripts/public/export_source.py` | 自有源码、文档、许可证、依赖清单；不含 .git | 推荐以此建立新 GitHub 仓库，仍应人工复查 |
| 完整数据 release 包 | 卡池、生成引擎和冻结版本 | 包含第三方受限资料；未取得所需授权，不应公开 |
| 个人迁移包 / 用户备份 | Git 历史、数据库、收藏、对局私有状态、缓存 | 仅供本人迁移，不要上传 GitHub |

原资料仓库为 [duanxr/PTCG-CHS-Datasets](https://github.com/duanxr/PTCG-CHS-Datasets)。其 README 的用途和再分发限制见第三方声明。项目 MIT 只授权自有代码与文档；不会授予卡图、卡牌数据或商标权利。

## 源码检查与导出

公开发布前检查源码及 Git 历史中的凭据、机器路径和本地数据。审计结果保存在 `var/publication-audit.json`，该报告不随源码包分发。扫描不能保证识别所有敏感内容，发布前仍需复查。

```bash
python3 scripts/public/audit.py --history
python3 scripts/public/export_source.py
```

生成 `exports/PTCG-Lab-public-source.zip` 和同名 SHA-256 校验文件。压缩包不覆盖已有同名输出，可用 `--output` 指定新名称。包内 `PUBLIC-SOURCE.json` 记录逐文件校验值。

解压到新目录后先人工复查，再自行执行：

```bash
git init -b main
# 先配置希望公开的 name/email；可使用 GitHub 的 noreply 邮箱。
git add .
git diff --cached --stat
git commit -m "Initial public source release"
# 随后在 GitHub 建空仓库，并按 GitHub 给出的命令设置 remote/push。
```

源码导出不包含 Git 历史。直接推送现有仓库时，历史提交及作者、提交者身份也会公开；`.gitignore` 不会移除已提交的内容。

## 公开源码如何启动

**公开源码包不是带全卡池的即用安装包。** 卡牌资料和运行数据刻意不附带。仅克隆后执行启动脚本会提示缺少本地资产，这不应通过从他人个人迁移包中抽取用户数据库解决。

有权使用且与当前代码兼容的可信本地完整环境时，在新的公开源码工作目录运行：

```bash
python3 scripts/public/import_local_assets.py /path/to/your/trusted-private-project
bash start.sh --setup --check
bash start.sh
```

Windows 使用相应 Python 命令与 `start.ps1 -Setup -Check` / `start.ps1`。导入工具仅复制 `data/`、`runtime/engine/` 和引擎完整性清单，不复制用户数据库、Git 历史、浏览器身份或旧发布工作进程。目标路径已存在会拒绝覆盖。确保该本地目录可信并且使用权允许；导入源码文件之后启动会执行其引擎代码。

公开源码需要匹配的卡牌数据、效果实现和引擎运行时。目前没有从源码一键初始化完整对战卡池的入口；仅下载上游卡牌 JSON 无法提供完整运行环境。

## 上游仓库设置

进入“数据与对战更新”，输入完整 GitHub HTTPS 地址，保存后再检查更新或预演。默认原仓库；支持同结构的公开 fork。设置保存在本机 `.catalog/sync/repository.json`，更换仓库不会修改收藏、已有对局或已发布规则。缓存按仓库隔离，旧任务及旧图片继续对应原来源。当前只跟踪 main，不支持私有仓库令牌、SSH 或任意 Git 服务。

## 发布与维护注意

完整 release 构建涉及受限资料，命令需显式 `python scripts/release.py build --include-restricted-data`，该开关只是确认范围，不是取得第三方授权。个人数据备份与恢复见 [安装与使用手册](启动使用与跨系统迁移.md#备份恢复与升级)。
