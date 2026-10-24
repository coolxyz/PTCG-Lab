# P3-B 本地人机适配验收

日期：2026-10-24。P3-A 已提交为 `b2d79e0`。P3-B 将本地练习模式接入共享 BattleArena，在隔离后端副本完成验收；原 `apps/web`、根启动脚本、冻结规则、专属 H2 策略和研究工作区均未修改。P3-C 的完整产品导航与端到端回归仍待实施，P3 整体保持 in_progress。

## 1. 实现与接口

`apps/desktop-web/src/practice` 包含：

- `types.ts`：冻结本地 API 的会话、卡组版本、开局及回放类型；本地 PracticeView 另外保留 aiVersion/aiStrategy，不把 AI 元数据加入共享显示合同。
- `transport.ts`：固定同源 `/api/battle` 路由。只有此模块可发请求，沿用 HttpOnly Cookie，不读取认证令牌、不引入远程网络客户端。
- `storage.ts`：只保存本地续局指针、待确认命令及开局请求。新命名空间 `ptcg-p3-practice:` 不自动接管旧界面的续局指针；同一浏览器 origin 内按 matchId 隔离命令，不存种子、私有规则状态或 AI 信息集。
- `controller.ts`：会话初始化、单一请求锁、AI/回放计时、回包确认与恢复。React StrictMode 的重复挂载效应不会重复创建会话或调度器。
- `PracticeApp.tsx`：本地大厅、预设/已有版本选择、策略及速度/暂停/单步控件，把授权视角和回调传给共享界面。`active=false` 隐藏界面并暂停调度，但保留控制器和已收到的局面。

| 用途 | 冻结本地 API | 迁移约束 |
| --- | --- | --- |
| 会话与目录 | POST session；GET revisions/matches | 初始化顺序先会话，再读取；显示目录仍由本地服务提供 |
| 推荐预设 | POST presets/{id}/revision | 五套内容与匹配逻辑由后端决定，前端不复制卡表 |
| 开局 | POST matches | H2 默认；预设 opponent 与自定义 opponentRevisionId 互斥；不确认结果前复用 requestId |
| 选择 | POST matches/{id}/commands | 原 commandId/stateVersion/decisionId/choice 原样重试；共享 UI 只发选择意图 |
| AI 推进 | POST advance?steps=1 | 单个请求锁，速度仍为 3/1.8/1.2 秒；暂停时仅显式单步推进 |
| 认输 | POST resign | 沿用 expectedStateVersion；不在前端计算胜负 |
| 回放 | GET replay/timeline、replay?after=…&limit=1 | 使用历史记录，不从新规则重算；回放时禁止选择与 AI 推进 |

目录显示延续旧界面的规则：只映射 verified 卡牌，优先上传图片、已有图片与说明。新界面没有读研究目录、下载资源包或向服务器上传卡图。

## 2. 生命周期与失败行为

- 进入/离开大厅、模式导航、系统全屏不创建新局。离开模式即取消后续计时；已经提交的请求仍接收结果，保留权威局面，不把一次已接受动作当成可以取消的服务端事务。
- 控制器在 busy、错误、待确认命令、回放、暂停或投币动画期间不自动推进 AI。手动单步也受请求锁、当前行动方和动画等待约束。
- 开局请求在发送前持久保存，未知结果时锁定卡组/策略选择；刷新页面后“重试开局”仍使用原参数和 requestId。成功后才清除。
- 命令发送前保存待确认数据。回包丢失、普通网络异常或 5xx 不清除；刷新只读取局面，不能把未核对的命令当作已确认。离开再返回、重载浏览器后可“重试同一命令”。
- 后端明确返回 400/403/404/409/422 才视为拒绝并清除相应请求；保留错误提示，用户刷新后重新选择。读取续局时的临时错误不删除续局指针；明确 403/404 可移除不可访问的指针。
- AI advance/认输仍沿用旧 API，没有新增命令幂等协议；网络错误后应刷新读取权威局面，不宣称这两个旧端点具有命令回执语义。跨窗口控制权属于后续网络客户端阶段。
- Cookie 仍由原本地服务管理；清除 Cookie 不能通过新会话取得原用户的私有对局。P3-C 再处理旧导航入口与续局指针兼容，不在本轮隐式迁移用户记录。

投币验收发现共享牌桌的投币组件和弹窗在同一步具有相同 React key，状态回调可能引发重复挂载。共享副本已分别使用 coins/dialog 前缀并增加浏览器回归；没有修改旧研究界面或规则 RNG。

## 3. 验收范围与证据

| 检查 | 本地结果 | 证据 |
| --- | --- | --- |
| 公共前端、DTO 和模式控制器 | 26 项通过，包含原 P3-A 16 项、7 项控制器、3 项本地模式浏览器场景 | `var/p3b/browser-final.log` |
| 真实本地后端浏览器验收 | 2 项通过：五套预设的专属 H2 匹配；真实开局、选牌、回包丢失重试、重载、单步、认输和历史回放 | `var/p3b/real-browser-results.json` |
| 冻结产品回归 | 75 项通过：收藏、目录、H2 专属策略、预设、会话、发布/同步及源码导出 | `var/p3b/legacy-regression.xml` |
| 公共核心/协议/Provider/门禁 | 208 项通过，含 P2-D 源码绑定和本地适配能力白名单 | `var/p3b/contracts.log` |
| 既有服务只读检查 | 人机练习、本地对战和局域网 TLS 入口均返回 HTTP 200 | `var/p3b/service-health.json` |
| 生产构建与独立包 | TypeScript、Vite、7 wheel、7 sdist、3 npm 包及仓库外消费通过 | `var/p3b/build-final/BUILD-REPORT.json`、`P3-CONSUMER-REPORT.json` |

真实验收使用当前 PTCG-Lab 的冻结后端副本、已核验的本地资产、新数据库与独立浏览器会话。五套预设的 aiStrategy.id/name 来自真实后端逐项验证，未在前端伪造策略标签。丢失响应测试先让真实服务器接受命令，再中断回包；重载后重试，确认状态版本没有再次增加。测试只保存本人视角回放，对手手牌为空且无 seed。

真实浏览器流程只覆盖有意义的前期动作及认输终局，没有将其宣称为完整自然胜负对局或 AI 强度评估。75 项回归在隔离副本运行；收藏/构筑/同步的新 GUI 导航流程、根启动切换和产品回滚属于 P3-C，不能用这些后端测试代替。公开 CI 运行合成和门禁验收；真实资产验收只在本地执行。

## 4. 隔离复现

正常 `npm run p3:dev` 仍是未连接服务器的网络界面演示。公开合成验收运行 `npm run p3:test`，请求全部由测试拦截，不需要真实卡牌或后端。

本地真实验收需要已有可信资产与包含旧产品依赖的独立 Python 工具环境。从仓库根目录运行：

```sh
python scripts/migration/p3b_prepare.py --output var/p3b/new-acceptance
export PTCG_PRACTICE_TEST_STAGE="$PWD/var/p3b/new-acceptance"
export PTCG_PRACTICE_TEST_PYTHON="$PWD/var/p1/venv/bin/python"
npx playwright test --config apps/desktop-web/playwright.practice.config.ts
```

准备脚本拒绝覆盖既有目录，复制公开代码与明确允许的可信资产，不复制用户 var 数据库、会话、图片缓存或模型。只在副本 `apps/web/dist` 放置启用本地练习的构建。测试 supervisor 固定监听 `127.0.0.1:8783`，每次创建新的 SQLite 数据库；不复用已有端口，结束停止自己的子进程。设置 `PTCG_BROWSER_PATH` 可用已安装 Chrome 的独立测试进程。

如需手工查看这个隔离副本，可使用同一环境变量执行 `node scripts/migration/p3b_server.mjs`，访问 `http://127.0.0.1:8783/#practice`；停止会关闭测试服务器。每次启动是新测试数据库，若已有浏览器续局指针不可访问，刷新大厅后重新选择测试局。

前端开发可显式设置 `PTCG_PRACTICE_API=http://127.0.0.1:8783` 后运行 `npm run p3:dev`。只接受专用环回端口，拒绝现有 8765/8770/8443；没有任意远程代理。不得将本轮构建覆盖到正在服务的研究目录。

## 5. 后续与回滚

P3-C 将把 PracticeApp 的持续挂载模式接入完整产品导航，并验证收藏、构筑、资料同步、本地练习、刷新恢复和回放的完整流程。迁移时保留旧构建与启动兼容入口，处理旧续局指针的显式兼容；不同时替换历史规则身份或 AI 实现。

本阶段回退只涉及新 desktop-web、battle-ui、测试与文档。停止独立测试进程并继续原根启动即可，不需对研究数据库、局域网在线局或模型做迁移。
