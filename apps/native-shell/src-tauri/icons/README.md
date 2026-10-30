# PTCG Lab · 大师球扇形卡组

`icon-source.png` 为已选定的三张大师球牌背扇形展开图稿。以紫色、洋红、白色和深紫为主；后两张牌的图案降低对比度与饱和度，中央牌完整突出。

从仓库根目录运行 `python scripts/native/icons.py`，使用锁定的 Tauri CLI 从原图生成桌面资源：

- `icon.icns`：macOS 应用与 Dock 图标。
- `icon.ico`：Windows EXE／窗口多尺寸图标。
- `icon.png`、`32x32.png`、`128x128.png`、`128x128@2x.png`：原生窗口及打包辅助尺寸。

应用图标在 `tauri.conf.json` 的 `bundle.icon` 显式声明。生成命令仅复制桌面所需文件；不会将其他平台输出加入源码。

图标二进制与配置共同纳入原生源码身份摘要，变更后必须重新构建、校验资源和签名封套。原图及所有尺寸保留透明外边缘。
