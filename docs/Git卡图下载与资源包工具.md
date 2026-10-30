# Git 卡图下载与 App 资源包工具

当前默认推荐 App 的[按需 Raw/CDN 下载和本机缓存](按需卡图与本地缓存.md)，无需运行此工具。此页工具用于明确的离线资源制作；全库超出 ZIP 预算时缩小 `--product`/`--printing-id` 范围，Git 缓存会保留。多个 ZIP 不自动叠加，不能用分包规避单份启用语义。

此工具通过 Git 下载公开上游卡图，生成当前 App 可识别的展示资源 ZIP 与 SHA-256。它只读取目标安装包目录，写入专用 Git 缓存和新的输出目录；不启动、停止或导入 App，不读写用户收藏、卡组、身份，不访问局域网 API，也不把 Git 加入 App 的运行依赖。

需要 Git 在 PATH 中。Python 和 Pillow 可直接使用已打包 App 随带的运行时，不需要额外安装 Python、pip 或 Node。本次在 macOS 上实际验证 Git 2.56.0、随包 Python 3.14.10；Windows 提供同一脚本的运行方式，仍需实机确认。

## macOS

在独立工具目录中，使用真实的 App 路径。DMG 中的 `.app` 或已安装的 `.app` 都可作为只读目标：

```sh
./download-macos.command "/Applications/PTCG Lab Preview.app" \
  --product CSVM2cC --cache ./cache --output ./resources-1
```

从源码运行则把启动器换为 `sh scripts/product/git_card_images.sh`。启动器只用 App 随带 Python 执行脚本，不会启动图形 App。

## Windows PowerShell

将工具放在独立目录，选择已完整解压的 Windows App 文件夹：

```powershell
$app = "C:\PTCG-Lab"
& "$app\backend\python\python.exe" -I -B -X utf8 .\git_card_images.py `
  --app $app --product CSVM2cC --cache .\cache --output .\resources-1
```

仍需要 Git 在 PATH 中。命令不使用 WSL2、不修改执行策略，也不运行 App EXE。

## 选择范围与固定版本

- `--list-products`：只列出目标 App 内置目录的卡包代码和数量，不联网。
- `--plan-only`：只下载 Git 元数据并生成计划，不下载卡图或制作 ZIP；可以不指定卡包。
- `--product CSVM2cC`：按卡包代码筛选；可多次提供，合成一份资源包。
- `--printing-id <完整 printingId>`：按具体印刷身份筛选，可重复。
- `--limit 10`：选定范围中的前十个条目，适合小批人工测试。
- `--repository <GitHub HTTPS 仓库>`：默认原上游 `https://github.com/duanxr/PTCG-CHS-Datasets`，仅公开仓库，不接受带凭据 URL。
- `--commit <完整40位SHA>`：复用精确版本；不指定时固定本次获取到的 `origin/main`，后续图片读取不跟随分支漂移。
- `--all-cards`：明确请求全部匹配卡图，可能超过 App 的容量限制；不能与范围过滤参数混用。默认不会意外下载全库。
- `--name <名称>`：在 App 中显示的资源包名称。

例如先看全库计划，再按需下载：

```sh
./download-macos.command "/Applications/PTCG Lab Preview.app" \
  --plan-only --cache ./cache --output ./plan-1
```

Git 缓存只保存对象，不检出或执行上游工作区、不运行钩子／子模块／LFS smudge，也不调用 GitHub REST blob API。已经下载的对象可复用；重试请使用计划中的固定 `--commit`、相同 `--cache` 和**新的** `--output`。不会覆盖已有输出或自动清理既有缓存。

## 手工导入

成功输出包含：

- `card-images.zip`：仅 `DISPLAY.json` 与按内容摘要命名的图片文件。
- `SHA256.txt`：仅一行 ZIP 的 64 位摘要，供界面粘贴。
- `SHA256SUMS.txt`：命令行校验用。
- `PLAN.json`：固定仓库／提交、目标目录身份、图片来源与未匹配列表。
- `REPORT.json`：实际打包数量和大小，`automaticallyImported=false`。

人工打开 App 的「数据与对战更新 → 卡图与展示资源」，选择 ZIP，粘贴 `SHA256.txt`，点击「校验并安装资源」，再点击「启用此展示」。安装不自动启用。整套资源只更换显示图片，不修改卡牌规则或对战资格。

历史示例包为 **CSVM2cC 的 32 个条目**，启用后按该卡包筛选检查；示例范围不代表用户当前画面或全部卡库。

## 当前 App 的限制

当前资源导入器以安装包内置目录的语义身份和 engineId 校验。因此工具默认按 `--app` 的内置目录制作包，不能直接覆盖同步后新增、内置目录尚未识别的 printingId。早期测试实例同步后的 12,957 张与其内置 12,807 张不是同一目录；不会改写目录身份来强行导入。新卡的批量展示支持需另行完善 App 的资源目录绑定。

只对精确卡包／卡号／名称匹配的条目选图，并检查已有完整卡号、HP 和冲突牌面；模糊匹配不会自动放行。所有图片保留上游原始字节，核对 Git blob 身份、图片类型、大小和像素预算，并在包中记录 SHA-256。

App 单图最多 8 MiB／2000 万像素，单 ZIP 最多 256 MiB、解压总量最多 512 MiB。**一次只能启用一份展示包**，多个包不会自动合并；需要一起显示的卡包应在一次命令中重复 `--product` 合成。全库通常过大，超限会明确失败，不生成可导入的最终 ZIP；Git 缓存保留，可按卡包缩小范围重试。失败的 `.partial` 不是交付包。

定向测试使用本地合成 Git 仓库验证匹配、冲突拒绝、完整卡号、ZIP 格式、去重／可重复输出、容量拒绝、离线缓存和路径检查；不向运行中的 App 自动导入，也不运行完整对局回归。

## 本次实际验证

- 固定上游提交：`3f9648f364caa750a18b56bb8e9d562c4c17689f`，通过 Git 获取，不走 GitHub REST 图片接口。
- 当前目标 App 内置 12,807 个卡牌条目；全库计划精确匹配 12,781 个，26 个牌面冲突保留在 `all-card-plan/PLAN.json`，没有强行选图。该计划未下载全库图片。
- `sample-csvm2cc/card-images.zip` 实际下载并封装 32 个 CSVM2cC 条目、32 张图，大小 7,165,857 字节。ZIP SHA-256 为 `67b097ac1c59a477cedb92c8a5ed94fde4d51ff854e1c3af069094d0268d412d`。
- 使用 App 随包 Python 验证独立脚本和启动器；从 Git 缓存重建结果完全相同。目标 App 的资源清单校验与 ZIP 内容摘要检查通过；6 项定向测试通过。没有对当前 App 自动导入或激活资源。

独立交付目录为主项目 `release/git-card-image-tool-20261029`。其中 `download-macos.command` 是 Mac 启动器，`git_card_images.py` 可按上文在 Mac／Windows 随包 Python 中运行。Git 缓存不混入交付文件；实际开发缓存保留在独立工作树 `var/card-image-tool/git-cache`。
