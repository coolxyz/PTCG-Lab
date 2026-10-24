# ptcg-online-client

P5-A 原生 API v2 客户端，当前版本 `0.5.0.dev2`。由本地后台持有，提供能力协商、游客身份、卡组提交、房间邀请、控制权、对战命令、WSS 私有快照和本人回放。当前只完成客户端基础层，尚未接入本地 GUI。

运行需要 Python 3.14、`ptcg-battle-protocol==0.2.0.dev1`、`jsonschema==4.25.1`、`websockets==15.0.1`；完整外部依赖锁见 `requirements.lock`。不依赖服务端、规则引擎、AI、本地数据库或卡图。

```python
from pathlib import Path
from ptcg_online_client.client import Client
from ptcg_online_client.transport import Server

# 地址只接受 origin；不得附带 /api 路径、用户名、查询或片段。
server = Server("https://battle.example.org")
with Client(server, Path.home() / ".ptcg-lab-online") as client:
    capabilities = client.capabilities()
    if client.status()["identity"] is None:
        identity = client.create_guest("训练家", "我的电脑")
```

测试私有 CA 时可向 `Server` 传 `ca_file`；不会关闭 TLS 校验。仅显式启用 `allow_loopback_http=True` 时允许回环 HTTP，用于隔离测试。地址和 CA 内容共同决定本地 profile，切换任一项不会复用凭据。

当前存储后端仅支持 POSIX：目录 `0700`、文件 `0600`，是权限受限的**明文文件**，并非系统钥匙串或加密存储。一个 profile 同时只允许一个客户端进程持有；同进程请求合并刷新。Windows 凭据后端待实现，当前直接拒绝初始化。不要将凭据目录加入备份发布物、版本库或交给浏览器。

`submit()` 在发送前持久保存原始请求；结果未知时使用 `recover(match_id)` 先查回执，再按原 commandId 重试。未解决前不能发送另一个命令。刷新响应丢失后停止自动刷新并返回 `REAUTHENTICATION_REQUIRED`，重启也不会重用已消费的 refresh token。当前游客没有账号找回机制；显式 `forget_identity()` 会失去此身份，存在待确认操作时默认拒绝清理。

房间写入在单次调用内沿用同一 requestId；跨调用/重启重试需要调用方保存并传入 `request_id`。P5-B 本地桥接已提供安全的恢复摘要与操作接口，浏览器不应直接调用本包或获得其内部 `vault`。

服务 envelope 的三个 schema 是服务端合同的逐字节副本，发布门禁检查同步；对战 DTO 直接复用共享协议包。详见 [P5 实施记录](../../docs/P5本地网络客户端实施记录.md)。

P5-B 增加 `recovery_status()` 和 `recover_control(match_id)`：前者只返回待确认命令标识/动作及控制请求的期望 epoch，后者重试持久化的同一控制请求。二者不返回 token、controllerId 或原始 choice。
