# Cloudflare 免费卡图 CDN 工具

维护端通过 Git 获取固定版本的原图，导出为 Cloudflare Workers Static Assets。用户的 App 仍然只在需要显示图片时下载并缓存；图片不进入对战 API，不需要重新编译 App，与本地克隆目录模式并存。

工具入口为 `scripts/product/cloudflare_card_images.py`，包含 `plan`、`build`、`verify`、`deploy`。前三个命令不访问 Cloudflare；只有显式运行 `deploy` 才可能发布。本文不表示已经部署到某个账号。

## 1. 准备运行环境

- Git 在 PATH 中；构建仅下载公开 GitHub 仓库，不使用 REST 图片接口，不检出或执行上游代码。
- Python 3.14+ 与 Pillow。可使用已打包 App 自带的 Python，或已按项目安装说明准备的 Python 环境；计划和校验清单本身不需要启动 App。
- 部署另需 Node.js 与锁定的 Wrangler。部署工具位于独立的 `scripts/product/cloudflare`，不加入 App 运行依赖。

以下示例在仓库根目录执行，以 `python3.14` 代表已准备好的 Python。macOS 也可替换为：

```sh
"/Applications/PTCG Lab Preview.app/Contents/Resources/backend/python/bin/python3" -I -B -X utf8 scripts/product/cloudflare_card_images.py plan --app "/Applications/PTCG Lab Preview.app"
```

Windows PowerShell 可使用：

```powershell
& 'C:\PTCG-Lab\backend\python\python.exe' -I -B -X utf8 scripts/product/cloudflare_card_images.py plan --app 'C:\PTCG-Lab'
```

`--app` 只读取安装包中的索引，不读取用户 profile。使用 `--index PATH` 可显式选择精确索引，并可重复提供。两者不能混用；都不指定时使用当前源码的 `resources/artwork/builtin-index.json`。**部署给已安装 App 使用时，应选择该 App 自带的索引，而不是假定它与当前源码一致。**

## 2. 离线预演与免费额度

```sh
python3.14 scripts/product/cloudflare_card_images.py plan
```

计划只读取索引，不联网、不下载图片、不写输出。结果包含固定仓库/提交、索引与卡表摘要、索引条数、按完整 URL 去重的图片数和控制文件预留。当前源码索引为 12,931 张图片，保守计入 `_headers` 后为 12,932 个文件。

Cloudflare Free 每个版本最多 20,000 个静态文件、单文件 25 MiB；工具保守为 `_headers` 预留一个名额，所以最多导出 19,999 个图片 URL。实际图片还必须满足 App 的 8 MiB／2,000 万像素限制。文件数超限会在初始化 Git 和下载图片前拒绝，不自动升级套餐或拆分域名。

静态文件直接由 Assets 提供时，请求免费且不计入动态 Worker 的每日请求额度；工具不生成 Worker 脚本，也不配置 `run_worker_first` 或 Worker Cache。额度和计费可能调整，部署前复核 [静态资源计费](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/) 与 [平台限制](https://developers.cloudflare.com/workers/platform/limits/)。

## 3. 下载与构建候选

```sh
python3.14 scripts/product/cloudflare_card_images.py build \
  --index resources/artwork/builtin-index.json \
  --name ptcg-card-images \
  --cache var/cloudflare-artwork/git-cache \
  --output release/cloudflare-artwork/candidate-1
```

如果服务对象是已安装 App，改用 `--app '/Applications/PTCG Lab Preview.app'`。输出目录必须是新的；仓库内生成目录只能位于忽略提交的 `var/`、`release/` 或 `exports/`，不能与缓存、索引或上一份输出重叠。Git 缓存按仓库隔离，只获取索引固定的 40 位提交，逐一核对 Git tree 与 blob 身份。

构建按批获取缺失 Git 对象，保留原始文件字节，检查 Git blob、SHA-256、格式、大小与完整图像解码。不修改卡表、规则或用户资料，也不生成或导入展示 ZIP。默认本地图片总量预算为 8 GiB；可通过 `--max-total-mib` 显式调整，它是维护端磁盘预算，与 Cloudflare 免费文件数预算分别检查。

失败时不留下可部署的最终目录，Git 对象缓存保留，重试使用相同缓存与新输出路径。成功目录包含：

```text
candidate-1/
  public/
    _headers
    <owner>/<repository>/<40位提交>/img/...png
  wrangler.json
  MANIFEST.json
  MANIFEST.sha256
  PLAN.json
  REPORT.json
```

只有 `public/` 是上传的静态目录。根目录的计划、清单和报告不公开上传；凭据不写入这些文件。`_headers` 设置长期 immutable 缓存，配置关闭 HTML 路径重写和 SPA 回退；缺图返回 404，不用首页或占位 HTML 冒充图片。

### 同时覆盖已同步资料的实际首选卡图

已安装 App 的随包索引不包含用户资料中全部同步图片路径。若这台 App 曾运行数据同步，必须显式增加 `--sync-home`，将当前激活卡表的首选卡图与随包索引取并集；只更新 App 或重复导出随包索引不能保证覆盖同步页面。

```sh
python3.14 scripts/product/cloudflare_card_images.py plan \
  --app '/Applications/PTCG Lab Preview.app' \
  --sync-home "$HOME/Library/Application Support/org.ptcglab.desktop.preview/app.sync"

python3.14 scripts/product/cloudflare_card_images.py build \
  --app '/Applications/PTCG Lab Preview.app' \
  --sync-home "$HOME/Library/Application Support/org.ptcglab.desktop.preview/app.sync" \
  --previous release/cloudflare-artwork/candidate-1 \
  --name ptcg-card-images \
  --cache var/cloudflare-artwork/git-cache \
  --output release/cloudflare-artwork/candidate-2
```

工具只读指定同步目录的 current.json、当前发布清单／卡表和对应 snapshot 的 source.json／image-index.json，检查卡表发布摘要，再用固定 Git tree/blob 验证新增图片。不会执行冻结代码、加载旧规则、读取用户数据库或账号凭据，也不默认扫描其他用户或历史发布。导出的公开内容只有图片和固定缓存头；计划不记录本机 profile 路径。当前已核对的首选卡图并集为 13,469 个图片 URL，比随包索引多 538 个。

默认范围是资料库／对战的首选卡图。若还需其他稀有度等卡面，增加 `--include-variants` 预演；全卡面会超过当前 Free 的 20,000 文件预算，工具会在下载前拒绝，不会静默裁剪或升级套餐。未导出的额外卡面可暂用 GitHub Raw 图源，或明确规划其他存储方案。

Finder 浏览 public/ 可能生成 `.DS_Store`。校验会报告额外／缺失文件数量和少量相对路径示例；将这些元数据移出 public/ 再校验。不要忽略所有未知文件或绕过清单检查，也不要将凭据放进部署目录。

## 4. 校验候选

从构建结果或 `MANIFEST.sha256` 取得 64 位摘要，替换以下 `MANIFEST_SHA256`：

```sh
python3.14 scripts/product/cloudflare_card_images.py verify \
  --directory release/cloudflare-artwork/candidate-1 \
  --manifest-sha256 MANIFEST_SHA256
```

校验要求精确公共文件集合、每个文件的 SHA-256/Git blob/大小/解码、静态部署配置和额度一致，拒绝新增私有文件、符号链接、配置被加入 Worker 脚本以及损坏图片。摘要应保存在维护记录中；同目录的 sidecar 仅方便传递，不能代替可信来源。

## 5. 安装部署 CLI、登录与发布

锁定 Wrangler 为 **4.156.0**，使用独立 lock 文件：

```sh
npm ci --ignore-scripts --no-audit --no-fund --prefix scripts/product/cloudflare
npm --prefix scripts/product/cloudflare run login
```

锁定 Wrangler 的最低要求是 Node.js 22+。登录由维护者在自己的浏览器完成；不要将账号 token 放在命令参数、配置文件或 Git 中。CI 可通过受保护环境变量 `CLOUDFLARE_API_TOKEN` 提供限定账号/Workers 权限的 token。`.wrangler/` 与 `.dev.vars*` 已被 Git 和公开源码策略排除，但仍不要在源码目录中手工保存 token。

从 Cloudflare 控制台复制 **Account ID**（32 位十六进制），替换 `ACCOUNT_ID`。先检查发布命令：

```sh
python3.14 scripts/product/cloudflare_card_images.py deploy \
  --directory release/cloudflare-artwork/candidate-1 \
  --manifest-sha256 MANIFEST_SHA256 \
  --account-id ACCOUNT_ID \
  --wrangler scripts/product/cloudflare/node_modules/wrangler/bin/wrangler.js \
  --dry-run
```

本工具的 `--dry-run` 只校验并打印命令，不启动 Wrangler、不认证、不上传。核对候选、账号与 Worker 名称后，去掉 `--dry-run` 执行同一命令，即可发布。部署前再次完整校验，固定账号通过子进程环境传入，不使用 shell，也不自动安装 CLI。Windows 同样选择 `bin/wrangler.js`，不使用 `.cmd`/PowerShell 包装器。

**对同一账号、同一 Worker 名称发布会更新其现有部署。** 每次需要完整提供要保留的静态文件集合，不能认为上一版部署中的文件会自动追加保留。工具不设置自定义域名；先用 Wrangler 给出的 `https://<worker>.<subdomain>.workers.dev` 地址验证，之后可由维护者在控制台绑定自己的域名。部署行为见 [Cloudflare 官方手顺](https://developers.cloudflare.com/workers/static-assets/get-started/)。

## 6. 配置 App 与验收

打开 App「数据与对战更新 → 在线卡图与本机缓存 → 图片来源与缓存设置」，选择“静态图源 / CDN”，将**基础地址**设为发布得到的 HTTPS origin，例如：

```text
https://ptcg-card-images.your-subdomain.workers.dev
```

不在基础地址重复添加 owner、仓库或提交，也不使用查询参数、凭据和重定向入口。App 会追加 `/<owner>/<repo>/<commit>/<path>`，继续校验原图身份并使用既有缓存。切换图源不会自动清空缓存；验收请选择尚未缓存的图片，并确认无 `IMAGE_MISSING` 或内容哈希错误。

验收步骤：

1. 从 `MANIFEST.json` 选择图片路径，确认远端返回 200、图片 MIME 与原始字节 SHA-256 一致，且没有重定向。不要启用自动转码、缩放或有损优化。
2. 请求不存在的图片应返回 404，不能返回 200 HTML。
3. 在 App 显示新图片，关闭重开后仍能使用本地缓存；断网只验证已缓存内容。
4. 网络对战与资料库均能显示同一资源；图库更新不改变规则支持状态。

## 7. 上游更新与保留旧 App

工具固定索引的提交，不自动将 `main` 最新卡图强塞给旧 App。先按 [卡图索引维护](按需卡图与本地缓存.md#固定图片索引) 生成并审核新索引，再导出 CDN。

上一份输出可作为本地复用来源：

```sh
python3.14 scripts/product/cloudflare_card_images.py build \
  --index /approved/index-old.json \
  --index /approved/index-current.json \
  --previous release/cloudflare-artwork/candidate-1 \
  --name ptcg-card-images \
  --cache var/cloudflare-artwork/git-cache \
  --output release/cloudflare-artwork/candidate-2
```

旧输出必须完整通过校验；相同 Git blob 即使出现在新的提交 URL 下，也复用经过校验的本地字节。上一版没有的内容才读取 Git 对象，已缓存对象不重复下载。Wrangler 负责上传端的资源去重；本工具不承诺远端实际上传字节数。

`--previous` **只提供缓存，不自动保留旧图片 URL**。要继续服务旧 App，必须显式同时提供旧索引。不同提交下相同图片仍是两个 URL，分别占用静态文件数；多个索引可能超出免费额度。达到上限时应明确调整支持的版本，或改用适合大图库的存储方案，不能静默删除旧文件。源文件、旧候选和 Git 缓存均不自动清理。

## 维护与发布边界

只提交工具、锁定依赖、必要测试源码与本文。`release/`、`var/`、Git 对象、下载图片、计划和构建报告不进入产品 main；Cloudflare 登录资料和 token 永不写入源码或输出目录。App 已移除卡图 ZIP 导入。离线使用可选择 [本地克隆目录](按需卡图与本地缓存.md#直接读取本地克隆目录)，无需制作资源包。

本工具的回归使用本地合成 Git 仓库，覆盖固定提交、路径拒绝、去重、额度预检、增量复用、多索引保留、完整性、失败清理及部署命令。Cloudflare 账号登录、实际发布和公网验收由维护者按本文执行，不把本地测试写成远端发布成功。
