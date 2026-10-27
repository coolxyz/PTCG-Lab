# P8-A macOS 原生技术验证

Tauri 2 壳复用完整产品 GUI 和 Python 产品后台。当前只实现 macOS arm64 技术验证，
不是已签名的发行包；Windows、Keychain、升级迁移、移动端仍有独立验收门槛。

启动时校验随包清单，通过继承管道启动随包 Python，验证 PID、随机 nonce 和回环地址。
原生壳写入 HttpOnly 会话 Cookie 后打开同源产品 GUI；没有页面原生命令权限。
关闭窗口退出后台，只等待／终止本壳创建的子进程，不发现或接管其他服务。
用户数据位于操作系统 Application Support 中的独立 preview 身份，安装资源只读。

开发构建步骤见 `docs/P8原生发布实施记录.md`。`Cargo.lock` 和 npm lock 必须提交。
固定 Rust 1.90.0，Tauri 2.12.5／tauri-build 2.7.4／CLI 2.12.4；macOS 最低 12.0。
