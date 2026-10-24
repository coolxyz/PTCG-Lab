# @ptcg/battle-ui

P3-B 共享对战显示包，版本 `0.3.0-p3b`。导出 `BattleArena`、`BattleScreen`、展示类型与 `playerLabel`；主入口自动引入必要样式，也提供 `@ptcg/battle-ui/styles.css` 主题入口。

组件接收已授权的展示 DTO、卡牌显示目录和回调，仅负责牌桌、决策、回放和全屏显示。选择回调只返回选择引用；会话、API、认证、AI 调度和回放计时由模式适配器持有。图片 URL 由宿主提供，组件不查询本地资料库。

React/ReactDOM 为 peer 依赖。当前导出 TypeScript/TSX 源码，须由 Vite 等构建器处理，尚非稳定发布 SDK。不依赖旧 apps/web 或研究工作区。已由独立本地练习适配器消费；原生产入口尚未切换。投币和弹窗使用独立 React key，避免动画状态回调导致重复挂载。

运行合成演示：仓库根目录 `npm run p3:dev`。边界、验收和本地接入计划见 [P3 实施记录](../../docs/P3共享界面与本地接入实施记录.md)。
