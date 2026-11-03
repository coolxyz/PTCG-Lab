# PTCG Lab 移动 App

独立 Tauri 2 iOS 宿主。React 复用产品的卡牌说明、网络大厅和 BattleArena；Rust 持有 SQLite、图片缓存和 API v2 HTTPS/WSS。移动包不包含 Python、规则引擎或人机练习。

支持收藏品相/备注、CSV 批次导入和撤销、卡组查找/新建/改名/复制/删除、冻结版本、系统文件选择及分享。卡组 CSV/TXT/JSON 与桌面通用，默认分享 CSV；删除需要确认及版本校验，冻结版本和历史对局保留。展示资料可按可信 SHA-256 单独更新，完整落盘后事务切换，不改写冻结身份。

网络客户端提交本机已冻结卡组。令牌和真实控制身份不进入 GUI；Keychain 密钥保护服务器/CA 绑定的加密日志，未知命令沿用原 ID 和内容恢复。HTTPS/WSS、卡图和展示资料共用系统 TLS 验证，局域网可额外设置公开 CA。图片只使用在线缓存，iOS 不读取桌面克隆目录，不提供 ZIP 导入。

iPhone 竖屏大厅使用单列/换行和受约束的原生选择控件；布局回归包含 WebKit、文档 scrollWidth、长卡组名及完整规则身份。Swift 系统文件/分享桥接只处理本次授权的文件，不向 GUI 暴露通用路径或凭据接口。App Documents 用于文件交换，数据库和加密日志保持私有。

用户已确认 iOS、与桌面完整对局/回放及升级安装测试通过；最低系统不要求实机测试。iOS 采用 TestFlight 有限分发，签名上传与许可/隐私材料单独准备，开发签名安装不等于已在 TestFlight 发布。历史自动化失败及人工结果分别保留，不伪造重测证据。

构建、资源准备、使用、连接和验证步骤见 [iOS 说明](../../docs/iOS开发与验收.md)。`npm run mobile:check` / `npm run mobile:build` 检查/构建前端；Rust 测试和 iOS 目标检查另行执行。原生界面驱动见 [XCUITest 步骤](../../tests/mobile/ios-ui/README.md)，只对明确授权的独立环境执行。`gen/`、`dist/`、展示资源、设备配置、构建和测试输出不提交。
