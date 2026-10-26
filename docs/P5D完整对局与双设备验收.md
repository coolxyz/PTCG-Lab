# P5-D 完整对局、恢复与双设备验收

更新：2026-10-26。当前状态：用户已确认 P5 对战验收完成，房间新建及对战过程未发现问题；P5/P5-D 已验收，P5.5 产品整合待开始。对战画面显示问题按用户决定延期到 P5.5 完成后集中修正。逐项未报告的物理故障、回放或缓存检查不补写为通过；以下早期 pending/隔离文字保留为历史阶段记录。验收范围见 [用户验收记录](../releases/p5d-user-acceptance.json)。

## 1. 本轮范围与修正

- 新增真实规则、独立 TLS API/PostgreSQL、两个原生后台进程和两个浏览器上下文的完整 GUI 测试。所有合法决策通过可见 Arena 控件提交，不从服务器恢复数据推导选择，不调用旧人机或 AI 接口。
- 网络窗口显示可复制的对局编号，便于使用原身份手工找回；所选规则与卡组 revision 一起保存在原有非秘密会话存储中，规则消失时清除旧 revision。
- 长局验收发现分页入口在回放模式被共享 Arena 隐藏。将网络模式自己的“加载更多回放”放到始终显示的状态区域，保持共享 BattleArena、旧人机和 AI 代码不变。
- 新增客户端测试包组装和完整性验证工具，复制固定三个 wheel、独立网页构建、锁定依赖、操作文档和待填验收模板。不包含规则、AI、服务器、真实图片、配置、身份或 CA。

纯 API 服务、核心/协议/Provider、共享 BattleArena 与原人机/AI 代码没有因本轮改变，默认产品入口仍走 legacy。`@ptcg/desktop-web` 推进至 `0.5.0-p5d`；online-client 修正并发快照读取，版本为 `0.5.0.dev3`；desktop-api 仅随该依赖推进至 `0.5.0.dev4`。

## 2. 自动化矩阵

| 条件 | 验证方式 |
| --- | --- |
| 完整自然终局 | 双方在 GUI 选择合法选项，优先结束回合；有界 180 步内达到非认输自然终局，两席均实际提交，胜者与原因一致。属于合法牌库耗尽策略，不能据此声称覆盖所有卡牌效果。 |
| 命令响应丢失 + 原生进程强杀 | 测试 transport 收到真正提交成功的远端响应后丢弃一次，GUI 显示待确认；杀死该新宿主进程，再用原 state-dir、端口和身份启动。新窗口先恢复原命令再取得操作权，最终数据库回执数等于合法动作总数。 |
| WSS 断开 | 关闭测试 transport 的真实 WSS 连接，原生客户端沿既有游标恢复；后续双方继续看到相同 matchSeq。 |
| 多窗口接管 | 第二个浏览器页面实际取得同席控制权，旧页退为只读；关闭第二页后旧页重新接管并继续。 |
| 资源离线 + 刷新 | 使用明确标注的合成 PNG 显示资源，不复制真实卡图。两端首次命中后删除原图，浏览器禁用 HTTP 缓存并刷新，验证本机哈希缓存仍能显示图，对局继续。HTTPS 下载校验由资源单元测试覆盖，这个 GUI 场景不声称使用了外部 HTTPS 图站。 |
| 完整私有回放 | 双方从第一页一直加载到最终帧，序号连续，帧数等于命令数 + 1；终局原因一致，对手手牌为空且没有可操作 decision。 |
| 模式与权限隔离 | 浏览器请求仅发往各自本机，无远程连接、AI advance、旧 /api/battle；存储中无远程 token、capability 或 lease。既有安全边界与人机回归另行运行。 |

测试故障仅存在于 `tests/desktop_host/fault_host.py`，由测试父进程通过私有文件标记控制。生产本地 HTTP API 没有故障、任意文件写入或重启入口。测试仅终止自己创建的子进程，并由已有 helper 回收自己的 PostgreSQL 容器。

首次自动化执行暴露了测试工厂未初始化延迟创建的客户端，已修正测试构造。第二次达到自然终局后发现真实分页按钮不可见，已修复网络窗口；失败证据保留于忽略目录。随后捕获 HTTP 快照晚于 WSS 更新返回导致的 STALE_STREAM：原生 snapshot 在严格校验身份后保留已经持久化的较新视图，既不回退游标，也不吞掉身份错误。已增加确定性竞态回归，最终修正版完整验收通过；失败日志保留以便追溯。

## 3. 验收结果

本机自动化与发布验收通过，物理双设备待验。证据位于忽略目录 `var/p5d/`；公开仓库仅保留无私有数据的 `releases/p5d-acceptance.json` 汇总。

| 检查 | 最终结果 | 证据 |
| --- | --- | --- |
| 完整原生客户端/桥接/宿主/真实 GUI 验收 | **73 passed**，无跳过；7 条上游弃用警告 | `acceptance-final-02/results.xml` |
| GUI 自然终局 | **100** 条命令，两席各 **50**；`deck_out` | `acceptance-final-02/p5d-gui-report.json` |
| 丢响应后进程重启与幂等恢复 | PLAYER2 原生进程强杀后恢复；最终 **100** 条回执，没有重复提交 | 同上 |
| 双方完整只读回放 | 各 **101** 帧，覆盖第 0—100 步与终局，序号连续 | 同上 |
| WSS/多窗口/离线缓存 | 实际连接关闭、两窗口接管、移除图源后禁用浏览器缓存刷新均通过 | 同上 |
| 浏览器访问边界 | 远端请求 **0**、页面错误 **0**、无 AI advance / 旧人机 API 请求 | 同上 |
| HTTP/WSS 快照竞态 | 确定性回归通过，旧快照不会覆盖新推送，身份不匹配仍拒绝；包含在 73 项内 | `client-race.log` |
| 网络控制器 | **5** 个恢复/隔离场景通过 | `controller-final.log` |
| 原 P3 人机/共享界面回归 | **30 passed** | `p3-browser.log` |
| 核心/Provider/协议/迁移/导出 | **235 passed** | `contracts-final.log` |
| 发布/冻结/构建与仓库外消费 | 全部通过；三个 npm 包仓库外消费通过 | `build-release.log`、`npm-consumer.log` |
| 测试包完整性 | 固定 **10** 个文件哈希通过，压缩后解压复验通过 | `kit-verify.log`、`kit-extracted-verify.log` |
| 现有服务只读检查 | 人机 8765、局域网 HTTP 8770 / HTTPS 8443 均 **200** | `service-health.json` |

已复查双方终局回放截图，显示对应座位的胜负、牌库耗尽原因与只读状态。新测试进程均已退出，测试数据库与临时 DSN/密码文件已回收。未重启原人机/局域网服务，未修改 AI 任务。

实际 Mac + Windows 两台设备、WSL 文件启动/本机转发、物理断网、不同系统上的安装与图源离线后原生重启仍由用户手动验收。这里的同机两个原生进程与两个浏览器上下文不等于两台物理设备。

## 4. 复现与交付

```sh
npm run p5:build
node scripts/migration/p5c_controller_test.mjs
PTCG_V2_REAL_INPUTS_FILE=/absolute/approved-real-inputs.json \
PTCG_P5D_BROWSER=/absolute/chromium \
PTCG_P5D_BUILD="$PWD/var/p5c/web-dist" \
var/p4a/venv/bin/python scripts/migration/p4a_postgres.py \
  --output var/p5d/new-acceptance --python var/p4a/venv/bin/python --suite gui
```

`--suite desktop` 仅运行宿主/GUI 测试，`--suite gui` 包含原生客户端和桥接回归。旧 P5-C GUI 需同时设置 `PTCG_P5C_BROWSER` / `PTCG_P5C_BUILD`；缺少明确真实输入或浏览器时相关场景跳过。CI 不用合成数据冒充真实规则/双设备验收。

构建包后组装不含私有内容的客户端测试目录：

```sh
python scripts/migration/p5d_client_bundle.py \
  --packages var/p5d/build-release --web var/p5c/web-dist \
  --output var/p5d/client-kit
python var/p5d/client-kit/verify.py
```

实际安装、配置与逐项实测见 [Mac 与 Windows 手动清单](P5D双设备手动测试.md)。模板 `releases/p5d-manual-template.json` 全部为 pending，物理结果不能从自动化报告推导。两台设备均须连接真正的独立 v2 HTTPS 服务；当前长期运行的 v1 服务不能代替，该阶段不自动重配或停止旧服务。

## 5. 完成门槛与回退

自动化完整通过之后，仍需实际两设备完成邀请、自然终局、PLAYER2、私有回放、刷新/重启、断网恢复、多窗口、地址隔离、图源离线与现有任务隔离。用户确认的证据填入私有验收表后才可标记 P5-D/P5 completed；在此之前不进入默认入口切换，不声称 P6 容量或跨平台安装器完成。

回退仅使用已提交的 P5-C 客户端构建或关闭新窗口，保留原 state-dir。不要删除身份/待确认命令，不重启原研究仓库的人机、局域网 v1 或 AI 服务。

本轮已生成 `var/p5d/ptcg-lab-p5d-client-kit.zip` 及 `.zip.sha256`，可复制到两台设备；包内 README 是手动测试清单，验收模板仍为 pending。独立 API v2 服务地址、批准 CA 和卡组清单需另行提供，压缩包不启动或迁移服务器。

## 双设备试用修复：关闭窗口后重新打开

2026-10-26：确认启动 HTML 是两分钟有效的一次性授权文件，使用后删除属于既有安全约定，但启动器未说明如何重开。`ptcg-desktop-api` 推进到 `0.5.0.dev5`，终端同时提示首次授权路径和同浏览器重开地址；交互终端支持 `r` / `reopen` 生成新的授权文件，后台和远端身份持续保留。标准库 `sys` 仅用于检测交互 stdin 和读取终端命令，已加入 desktop-api 的导入白名单。服务端、规则、原生网络客户端及旧 APP 入口未修改。

本轮宿主、启动器与资源回归 17 项通过，覆盖同会话重开、过期后重新授权、拒绝重复 ticket、无授权根页不签发会话、容量限制与关闭处理。实际 Mac/Windows 物理验收状态仍为 pending，不能将修复测试视为双设备验收通过。
