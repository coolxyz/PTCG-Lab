# P5-D：Mac 与 Windows 双设备手动测试

本清单用于两台物理设备，不能用一台机器的两个浏览器代填。当前 Windows 测试路径为 **WSL2 Linux 后台 + Windows Edge/Chrome**，原生 Windows Python 的凭据后端仍未实现；不要跳过 POSIX 权限检查。若必须原生 Windows，需要单独实现并实机验证 Windows ACL、凭据保护和跨进程锁后才能验收。

## 1. 准备条件

- 两端各有 Python 3.14；Windows 的 Python 安装在 WSL2 中。运行已构建测试包无需 Node.js、规则引擎、数据库或 AI 环境。
- 一个独立部署的 **API v2 HTTPS/WSS** 服务，已加载批准的规则且 capabilities 的 acceptingMatches 为 true。现有 8770/8443 的 v1 服务不能用于本测试。服务端准备见仓库 `ops/online/README.md`；对外的证书须覆盖服务地址，客户端显式配置 CA，不关闭校验。
- 两端各有自己的可用卡组 JSON 清单（printingId/quantity），不复制对方凭据，不自动读取研究仓库收藏数据。
- 测试包包含三个本地 wheel、外部依赖锁、网络窗口静态文件和验收模板，不含服务器、规则、真实卡图、身份或私有 CA。本轮只准备测试包，不自动启动长期 LAN 服务。

将整个测试包复制到 Mac；Windows 端复制到 WSL 的 Linux 文件系统，例如 `/home/<用户>/ptcg-online-kit`。状态、启动文件、凭据和图片缓存也应放在 WSL Linux 文件系统，不能放 `/mnt/c`、OneDrive 或共享目录。Microsoft 的 [WSL 本机网络说明](https://learn.microsoft.com/windows/wsl/networking) 说明 Windows 浏览器可以经 localhost 访问 WSL 服务；[文件权限说明](https://learn.microsoft.com/windows/wsl/file-permissions) 区分 Linux 文件系统与 Windows 挂载盘权限。这是选用 WSL 的依据，不代表本项目已经完成 Windows 实机验收。

## 2. 两端安装

以下命令在 Mac 终端或 WSL shell 中执行，当前目录为测试包根目录。使用独立的新虚拟环境，避免更改现有 APP/AI 环境：

```sh
python3.14 verify.py
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps wheels/*.whl
```

依赖安装会从配置的 Python 包源获取当前平台的锁定版本；不要把 Mac 的外部 wheel 复制给 WSL。离线安装时须为目标系统单独准备同版本 wheelhouse。`verify.py` 校验包内文件哈希，不是发行者签名验证，应从可信渠道获得整个测试包及其校验清单。

## 3. 显式配置与启动

在包外创建私有目录，例如 `mkdir -m 700 "$HOME/.ptcg-lab-online-test"`。两台设备分别创建 `online.json`，使用同一批准的 API 地址和 CA，路径是各自本机路径：

```json
{
  "servers": {
    "lan": {
      "origin": "https://替换为API-v2服务地址:8874",
      "ca_file": "/absolute/path/to/approved-ca.pem"
    }
  },
  "manifests": []
}
```

公有可信证书可省略 ca_file。私有 CA 从部署方取得，必须校验来源。不要使用原 v1 的地址冒充 v2，也不要把对方 state-dir 一起复制过来。测试图片可选清单格式见 P5-C 验收文档；没有图包时显示卡名仍能对战。

在两台设备分别启动，只绑定本机 `127.0.0.1`。固定空闲端口便于重启后继续使用同一浏览器 origin；18786 冲突时换另一个专用端口，不终止占用者：

```sh
.venv/bin/python -m ptcg_desktop_api.launch \
  --config "$HOME/.ptcg-lab-online-test/online.json" \
  --state-dir "$HOME/.ptcg-lab-online-test/state" \
  --build "$PWD/web" --port 18786 --no-browser
```

Mac 在两分钟内打开输出的 HTML 文件。Windows 在资源管理器通过 `\\wsl.localhost\<发行版>\home\<用户>\...` 找到输出的 HTML，用 Windows Edge/Chrome 打开。保持后台终端运行。HTML 以单次表单 POST 完成授权并自动删除，不能仅输入根网址获取初次授权，也不能复制 ticket 到 URL。若浏览器策略阻止 UNC 文件自动表单、WSL 的 localhost 转发不可达，记录实际错误，停止该项验收；不要改成监听 `0.0.0.0`、开放 CORS 或取消 Origin/权限检查。

## 4. 两台设备逐项执行

| 项目 | 操作与通过条件 |
| --- | --- |
| 独立启动 | 两端游客身份独立，浏览器网络面板只连接各自本机。远端 access/refresh token 不出现在浏览器存储或请求体。 |
| 邀请与准备 | 各自提交卡组；A 建房、B 用房间码加入，双方准备只生成一局，自动进入 Arena。 |
| 自然终局 | 完成一局合法对战，不能以认输代替自然终局。可使用合法结束回合直到牌库耗尽；记录终局原因与总步数。 |
| PLAYER2 | B 的手牌、决策、奖赏、胜负均为自己的视角；对手隐藏手牌不可见。第二局交换建房者，再确认座位。 |
| 回放 | 两端分别打开本人回放、加载后续页、检查最终帧；回放无认输/提交入口，双方不能读取对手私有回放。 |
| 刷新与 APP 重启 | 对局中展开并自行保存“对局编号”；刷新需重新取得操作权。只关闭并重启自己的新后台，使用原 state-dir，身份不重建；新端口/新标签页可在大厅手工输入本人对局编号。 |
| 网络故障 | 对局中暂时断开一端网络，再恢复。推送或命令有明确错误提示；未知命令先“恢复待确认操作”，不得靠创建新身份或重发新动作绕过。检查没有双重执行。 |
| 多窗口 | 使用大厅“打开另一个窗口”，输入当前对局编号并接管。旧窗口变为只读，重新接管后才能行动；关闭窗口不认输。 |
| 换服务器 | 在另一个独立配置/状态目录中指定不同批准地址；初次应无原身份、卡组 revision 或对局。恢复原配置仍可找到原身份，不手动复制凭据文件。 |
| 缓存与缺图 | 已显示图后停用图源，刷新及重启仍显示已缓存图；未缓存图降级为文字，对战可继续。图片只由本机后台加载，API 对战服务不传图片。 |
| 既有任务 | 网络断开期间，原 APP 收藏/人机仍可使用，现有 AI 任务按原方式运行；不切换默认路由、不停止或重启旧服务。 |

“网络故障”实测是补充验收；精确的“远端已提交但响应丢失后强杀原生后台”由自动化测试私有故障工厂覆盖，生产包不包含故障接口。

## 5. 登记与退出

复制 `acceptance-template.json` 到私有验收目录，记录系统版本、WSL 发行版、浏览器版本、两端运行方式和各项 pass/fail/pending。不要填写 token、种子、手牌、房间码、真实卡组或凭据目录内容；截图只保存在私有目录。

任一物理设备或自然终局项目未通过时，P5-D 和 P5 都不能标记 completed。Windows WSL 通过也不等于原生 Windows Python/安装器通过。

退出仅关闭各自新后台终端。保留 state-dir 以保留游客身份与待确认记录。服务端仅停止本次建立的专用 v2 项目，不删除卷，不使用全局 Docker prune，不操作既有 v1、人机和 AI 服务。
