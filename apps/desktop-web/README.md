# @ptcg/desktop-web

P5-C 显示适配、本地练习宿主与独立网络窗口，版本 `0.5.0-p5c`。主入口导出 `snapshotView` / `replayView`；`@ptcg/desktop-web/practice` 导出 PracticeApp 与模式控制器，完整产品导航已保持其挂载并通过 active 暂停调度。

本地模式沿用同源 `/api/battle` 与 HttpOnly 会话 Cookie，不重构 AI。会话、选择重试、AI 计时、投币等待和回放由模式宿主持有；共享显示包不访问 API。默认 P3 构建仍只启用合成网络演示；真实网络窗口使用独立 P5 构建。隔离人机验证见 [P3-B 验收](../../docs/P3B本地人机适配验收.md)。

在根目录运行 `npm run p3:dev` / `p3:build` / `p3:test`。本地模式只允许显式启用和专用环回后端端口；不会默认代理正在运行的旧服务。旧 apps/web 与根启动脚本继续使用原入口；`npm run p3:product:build` 构建接入共享 PracticeApp 的完整产品到 `var/p3c/product-dist`，不覆盖旧 dist。完整接入、恢复键兼容与回退步骤见 [P3-C 验收](../../docs/P3C完整产品接入验收.md)。

## P5-C 网络窗口

当前包版本 `0.5.0-p5c`，新增 `@ptcg/desktop-web/online` 导出 OnlineApp 与 OnlineController。`npm run p5:build` 生成独立 `var/p5c/web-dist/online.html`，通过 desktop-api 原生启动器运行。默认 P3 演示、原人机模式和 apps/web 构建未切换。

网络窗口提供配置服务器选择、游客身份、JSON 卡组清单、房间邀请/准备、共享 Arena、控制权、原命令恢复、本人回放和缺图降级。浏览器请求固定本机 API，不直接连接远端。远端令牌、本机 Cookie、窗口 capability 和控制 lease 均不写入浏览器存储；sessionStorage 仅保存窗口 ID、选择的服务器、身份 ID、对局/房间/卡组 revision，以及未确认大厅请求的原始 requestId/内容。

旧标签页重复复制导致 windowId 相同时，新页面签发会撤销旧 capability，旧页安全停用；使用“打开另一个窗口”（noopener）创建独立窗口。详见 [P5-C 验收](../../docs/P5C本地网络窗口与资源缓存验收.md)。
