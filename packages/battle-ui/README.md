# @ptcg/battle-ui

P3-B 共享对战显示包，版本 `0.3.0-p3b`。导出 `BattleArena`、`BattleScreen`、展示类型与 `playerLabel`；主入口自动引入必要样式，也提供 `@ptcg/battle-ui/styles.css` 主题入口。

组件接收已授权的展示 DTO、卡牌显示目录和回调，仅负责牌桌、决策、回放和全屏显示。选择回调只返回选择引用；会话、API、认证、AI 调度和回放计时由模式适配器持有。图片 URL 由宿主提供，组件不查询本地资料库。

React/ReactDOM 为 peer 依赖。当前导出 TypeScript/TSX 源码，须由 Vite 等构建器处理，尚非稳定发布 SDK。不依赖旧 apps/web 或研究工作区。已由独立本地练习适配器消费；原生产入口尚未切换。投币和弹窗使用独立 React key，避免动画状态回调导致重复挂载。

运行合成演示：仓库根目录 `npm run p3:dev`。边界、验收和本地接入计划见 [P3 实施记录](../../docs/P3共享界面与本地接入实施记录.md)。

P5.5 外观设置：对战与回放页头提供“桌面外观”，包含纯色、原创 SVG 赛事风格桌垫及本地图片导入。`BattleArena` / `BattleScreen` 的可选 `appearanceStorage` 端口由宿主实现；未提供时只在本次挂载中保留。导出 `Appearance`、`AppearanceStorage`、`parseAppearance`、`DEFAULT_APPEARANCE`。共享显示包不直接读写浏览器存储；完整产品的人机、网络和演示适配器注入同一个本地偏好存储。详见 [外观设置说明](../../docs/P5.5对战桌面外观.md)。

P5.5 对局信息：`BattleArena` 内的 `BattleLog` 显示结构化动作、公开卡牌、投币和结果，提供筛选、阅读保持与卡牌详情。事件支持 `turn/kind/cardGroups`；宿主必须先完成可见性投影。`@ptcg/battle-ui/presentation` 提供不加载 CSS 的纯显示辅助函数。详见 [对局信息展示设计](../../docs/P5.5对局信息展示设计.md)。
