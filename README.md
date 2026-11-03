# PTCG Lab

PTCG Lab 是简体中文 PTCG 本地应用，提供卡牌资料库、收藏、卡组构筑、人机练习与房间码网络对战。桌面 App 使用 Tauri 2，本机 Web 使用同一套产品界面；网络对战由独立的 API v2 服务裁定。人机和网络模式共用本地卡组、对战桌面及回放界面。

当前版本为 **1.1.0-preview.1**。网络对战和原生 App 的当前功能开发已完成，后续进入维护与具体部署/分发。桌面支持 macOS Apple Silicon 和 Windows x64，用户已确认 Windows、iOS 和升级安装人工验收通过。Intel Mac 暂不支持，声明的最低系统不作为实机验收门禁。项目按开源源码交付，由用户自行编译桌面程序，不要求维护者提供 Developer ID、公证或 Windows 签名；iOS 计划通过 TestFlight 有限分发。当前支持状态见 [RELEASE.json](RELEASE.json)。

## 开始使用

- 桌面用户：阅读 [安装与使用手册](docs/启动使用与跨系统迁移.md)，按[构建说明](docs/构建与发布.md)生成完整 App/DMG 或 Windows ZIP。
- 连接对战服务器：配置 API origin，公网可信证书使用系统信任；局域网私有 CA 需额外配置同批公开 CA。重开后在大厅建立身份、选择已保存卡组并创建或加入房间。
- 卡图：默认从 GitHub Raw 按需下载并本地缓存，可配置静态图源/CDN，或指定已 clone 的本地 Git 卡图目录直接读取。无需完整下载所有图片，详见 [卡图设置](docs/按需卡图与本地缓存.md)。

## 从源码运行

需要 Python 3.14+、Node.js/npm、Git，以及与当前源码匹配且允许使用的运行资产。公开仓库不含完整卡表、规则资产、第三方引擎运行目录或用户数据；仅 clone 仓库无法直接启动完整产品。

在新源码目录中，从可信的当前配套资产目录导入后运行：

```sh
python3 scripts/public/import_local_assets.py /path/to/approved-product-assets
bash start.sh --setup --check
bash start.sh
```

Windows 使用 `./start.ps1 -Setup -Check`，再运行 `./start.ps1`。资产必须匹配 [RELEASE.json](RELEASE.json) 的卡表和索引，首次导入拒绝覆盖已有资产。配置网络功能、端口和独立身份的方法见 [使用手册](docs/启动使用与跨系统迁移.md)。

## 部署与维护

- [构建 macOS、Windows 和 API Compose 包](docs/构建与发布.md)
- [API 公网部署、自动证书及备份恢复](docs/API公网部署与自动证书.md)
- [安装升级、身份迁移与整套回退](docs/安装升级与迁移.md)
- [Windows 验证步骤](docs/Windows验证步骤.md)
- [更新上游卡库与规则](docs/上游卡库同步.md)
- [本地克隆卡图与在线缓存](docs/按需卡图与本地缓存.md)
- [iOS 移动开发与验收](docs/iOS开发与验收.md)：包含收藏/CSV 交换、卡组管理及文件导入、卡图缓存、目录更新、原生分享及 API v2 对战；iPhone XR 与桌面完整对局及回放已人工验收。TestFlight 签名上传与分发材料另行准备。
- [TestFlight Release 归档、签名与有限分发](docs/TestFlight发布.md)
- [部署 Cloudflare 免费卡图 CDN](docs/Cloudflare卡图CDN部署.md)
- [架构与维护](docs/架构与维护.md)
- [源码发布与隐私](docs/GitHub发布与隐私.md)

main 保留产品源码、锁定依赖、构建部署工具、用户说明和必要的回归测试源码。阶段计划、历史验收报告、容量实验、临时环境和测试生成数据保留在开发分支或本机私有目录，不进入产品发布树。项目不包含独立 AI 研究工作区。

规则只执行当前批准版本，身份、哈希和权限校验始终生效；更新规则后旧局可能需要重新开局，历史可见帧可以只读查看。源码采用 [MIT](LICENSE)，第三方引擎、卡牌文字、图片和标志的权利说明见 [第三方声明](THIRD_PARTY_NOTICES.md)。
