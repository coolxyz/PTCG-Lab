# @ptcg/desktop-web

P3-C 显示适配、本地练习宿主与独立网络界面演示，版本 `0.3.0-p3c`。主入口导出 `snapshotView` / `replayView`；`@ptcg/desktop-web/practice` 导出 PracticeApp 与模式控制器，完整产品导航已保持其挂载并通过 active 暂停调度。

本地模式沿用同源 `/api/battle` 与 HttpOnly 会话 Cookie，不重构 AI。会话、选择重试、AI 计时、投币等待和回放由模式宿主持有；共享显示包不访问 API。默认构建仍只启用合成网络演示。隔离人机验证见 [P3-B 验收](../../docs/P3B本地人机适配验收.md)。

在根目录运行 `npm run p3:dev` / `p3:build` / `p3:test`。本地模式只允许显式启用和专用环回后端端口；不会默认代理正在运行的旧服务。旧 apps/web 与根启动脚本继续使用原入口；`npm run p3:product:build` 构建接入共享 PracticeApp 的完整产品到 `var/p3c/product-dist`，不覆盖旧 dist。完整接入、恢复键兼容与回退步骤见 [P3-C 验收](../../docs/P3C完整产品接入验收.md)。
