# P3-C 完整产品接入与回退验收

日期：2026-10-24。前序 P3-B 已提交为 `3ce7ba2`。P3-C 已完成本地隔离验收。本阶段在 PTCG-Lab 接入完整产品导航，保留默认旧界面构建；不切换正在运行的人机练习、局域网对战或 AI 任务。P4 的纯 API 服务尚未开始。

## 实现与边界

完整产品壳层继续位于 `apps/web`，复用已有资料库、收藏、构筑、版本、导入导出、卡图及同步页面。新增 `Practice.tsx` 作为默认旧 Battle 包装入口。独立产品构建配置只将该入口解析为 `@ptcg/desktop-web/practice` 的公开导出，React/ReactDOM 单实例解析；不导入工作区包私有路径，不复制整个产品壳层。`@ptcg/desktop-web` 版本为 `0.3.0-p3c`，共享 UI 继续使用已验收的 `0.3.0-p3b`。

共享 PracticeApp 持续挂载，通过 active 控制调度。进入练习时刷新卡组版本与历史列表；请求进行中则排队刷新大厅，不重建对局。导航刷新保留未知结果的错误与待确认命令，避免导航绕过重试停机。构筑页面的“使用此版本对战”作为一次性版本选择意图消费，同一版本再次被选择时仍生效；不会覆盖已经开始的对局。

产品路由支持 `#cards`、`#collection`、`#decks`、`#battle`、`#sync`，兼容 `#practice` 和 `#build`，并响应浏览器前进/后退。显示对战时，通过宿主样式隐藏产品壳层，保留控制器实例；BattleScreen 的 portal 负责完整窗口与系统全屏。窄窗口产品导航可横向滚动，文字和图标不再互相挤压。

恢复存储兼容同源旧 `ptcg-match`：优先读取旧指针，成功加载后同步写入旧键与 `ptcg-p3-practice:match`，使回退后的旧界面也能找到当前对局。旧界面选中另一局后，新界面会采用更新的旧指针。所有读取仍经过后端会话权限验证；403/404 清除无效指针，临时网络错误保留。命令回执按 matchId 保持在 P3 命名空间，不改 Cookie、规则状态或数据库。

## 构建、验收与回退入口

在仓库根目录安装固定依赖：

```sh
npm ci --ignore-scripts
npm run p3:product:build
```

结果默认写入 `var/p3c/product-dist`，不会覆盖 `apps/web/dist`。原 `npm --prefix apps/web run build`、`start.sh`、`start.ps1` 和后端静态目录保持原兼容行为。根启动脚本消费部署目录中的静态制品；P3 不自动选择新 GUI，也不启动或重启服务。

持有已授权卡池与引擎资产的本地验收环境可以生成新副本：

```sh
python scripts/migration/p3c_prepare.py --output var/p3c/new-acceptance
```

输出保留 `frontend`（共享界面）、`frontend-legacy`（旧界面）和 `app`（允许公开源码与授权规则资产的隔离副本）；只在该副本的 `apps/web/dist` 安装新构建，不复制用户数据库、模型或研究配置。输出目录必须不存在。设置以下环境后运行 `npm run p3:product:test`：

- `PTCG_PRACTICE_TEST_STAGE`：上述副本父目录的绝对路径。
- `PTCG_PRACTICE_TEST_PYTHON`：含产品运行依赖的独立解释器路径；不要对虚拟环境解释器做 realpath。
- 可选 `PTCG_BROWSER_PATH`：测试 Chrome 的可执行文件。

测试监督器校验隔离清单，生成 UUID 命名的新 SQLite，只绑定 `127.0.0.1:8783`，禁止复用已启动服务，结束后关闭自己的子进程。旧产品测试从副本加载，避免两套 Playwright 运行时冲突。公开 CI 只做无真实资产的构建、合成测试和包消费；真实产品测试在持有资产的本地环境运行，不声称远程 CI 已通过。

回退仅需在**停止的目标部署或新部署副本**恢复 `frontend-legacy` 静态制品，不变更数据库和运行时。本次回退测试在同源浏览器中替换 HTML/静态资源，先后加载新→旧→新制品，保持同一个 Cookie、对局 ID 和服务，验证终局内容完全相等；测试不改实际部署目录。旧界面没有 P3 命令回执恢复能力，存在待确认命令时应先在新界面完成同 ID 重试/核对，再回退；不能通过回退规避未知结果。API、AI、规则与历史格式保持冻结版本。

## 验收证据

最终结果与限制记录在本节；原始 JSON、日志、截图均留在被忽略的 `var/p3c`，不随源码公开。

| 验收项 | 结果与证据 |
| --- | --- |
| 完整产品及旧界面生产构建 | TypeScript 与 Vite 通过，保留双制品 |
| 公开共享 UI / 控制器 / 存储测试 | 30 项通过；`public-browser-acceptance.log`，含双座位、全屏、只读回放、命令重试、导航、旧键兼容 |
| 完整产品浏览器回归 | 21 项通过；`browser-acceptance.log`、`browser-results.json`，含 17 项收藏/构筑/卡图/同步，2 项真实人机，2 项产品导航与回退 |
| 产品后端回归 | 75 项通过；`isolated-1/product-regression.log`，运行于独立副本 |
| 根兼容启动检查 | 隔离副本 `bash start.sh --check` 通过，未启动服务；测试依赖复用本产品验收虚拟环境 |
| 公开规则/协议/资源/迁移门禁 | 210 项通过；见 `contracts.log`；P2-D 源码绑定继续通过 |
| wheel/sdist/npm 与仓库外消费 | 见 `release-final/BUILD-REPORT.json`、`P3-CONSUMER-REPORT.json` |
| 既有服务只读检查 | 见 `service-health.json`；不重启服务或调整 AI 任务 |

真实人机测试包含五套 H2 策略、早期决策、响应丢失后的同 ID 重试、刷新恢复、AI 单步、认输及历史回放，不将这些短局冒充 AI 强度或完整自然终局评估。同步 UI 的预演/失败路径使用受控响应；仓库设置与拒绝任意源走真实本地 API，后端同步发布回归使用测试夹具，不运行实际上游发布。

P3 完成代表共享显示与本地产品接入达到本阶段门槛，不代表已切换默认产品、已部署网络 API，或已经完成 P4—P8 的认证、多局调度、容量及运维验收。
