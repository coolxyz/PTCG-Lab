# PTCG Lab

PTCG Lab 是简体中文 PTCG 本地应用，提供卡牌资料库、收藏、卡组构筑、人机练习与房间码网络对战。桌面 App 使用 Tauri 2，本机 Web 使用同一套产品界面；网络对战由独立的 API v2 服务裁定。人机和网络模式共用本地卡组、对战桌面及回放界面。

当前版本为 **1.1.0-preview.1**。macOS Apple Silicon 和 Windows x64 均有打包入口；Windows 原生实机、最低系统、干净机和正式签名验收仍待完成，不能将源码发布分支或交叉编译成功等同于正式二进制发行。当前支持状态见 [RELEASE.json](RELEASE.json)。

## 开始使用

- 桌面用户：阅读 [安装与使用手册](docs/启动使用与跨系统迁移.md)，使用维护者提供的完整 App/DMG 或 Windows ZIP。
- 连接对战服务器：关闭 App 后，用同批 API kit 的公开 CA 和 origin 配置客户端，重开后在大厅建立身份、选择已保存卡组并创建或加入房间。
- 卡图：默认从 GitHub Raw 按需下载并本地缓存，可配置静态图源/CDN，也可导入带 SHA-256 的离线 ZIP。无需完整下载所有图片，详见 [卡图设置](docs/按需卡图与本地缓存.md)。

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
- [安装升级、身份迁移与整套回退](docs/安装升级与迁移.md)
- [Windows 验证步骤](docs/Windows验证步骤.md)
- [更新上游卡库与规则](docs/上游卡库同步.md)
- [制作离线卡图包](docs/Git卡图下载与资源包工具.md)
- [架构与维护](docs/架构与维护.md)
- [源码发布与隐私](docs/GitHub发布与隐私.md)

main 保留产品源码、锁定依赖、构建部署工具、用户说明和必要的回归测试源码。阶段计划、历史验收报告、容量实验、临时环境和测试生成数据保留在开发分支或本机私有目录，不进入产品发布树。项目不包含独立 AI 研究工作区。

规则只执行当前批准版本，身份、哈希和权限校验始终生效；更新规则后旧局可能需要重新开局，历史可见帧可以只读查看。源码采用 [MIT](LICENSE)，第三方引擎、卡牌文字、图片和标志的权利说明见 [第三方声明](THIRD_PARTY_NOTICES.md)。
