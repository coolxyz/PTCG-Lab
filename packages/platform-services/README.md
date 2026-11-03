# Platform services

产品能力合同。浏览器和桌面支持完整本地功能；iOS 排除人机和本地规则执行。macOS Apple Silicon、原生 Windows x64 与 iOS 已有实际宿主实现，功能测试与升级安装由用户确认；Intel Mac 暂不支持。

桌面后台继续使用同源 HTTP 接口，调用不能自动获得原生权限。移动端 `apps/mobile` 通过仅授予随包主窗口的 Tauri 命令访问原生数据，不启动 Python 或回环 HTTP 服务；复用共用大厅和 Arena。令牌、凭据存储、进程控制与任意文件路径不属于 GUI 合同。

移动端包含 SQLite 卡组/收藏、CSV/TXT/JSON 文件交换、目录事务更新、在线卡图缓存、API v2、Keychain 密钥和加密恢复日志。目标能力合同与原生 `implemented` 报告分开，文件路径和身份不进入交换文件，移动导入不直接复制桌面数据库。桌面按开源源码自行编译，iOS 计划通过 TestFlight 有限分发；步骤见[移动说明](../../docs/iOS开发与验收.md)。
