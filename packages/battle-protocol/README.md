# ptcg-battle-protocol

P2-A 的 v2 草案合同与运行时校验。未接入现有人机、网络或 AI 运行路径，不代表 API v2 已上线。

`schema/v2.schema.json` 是唯一手写 wire schema；`v2.py` 与 TS 类型由固定工具生成。`validation.decode(kind, bytes)` 提供有大小限制的 UTF-8 JSON 解码、严格字段和语义校验，失败只抛出 `ProtocolViolation.code`。`validation.validate(kind, value)` 供已构造的 JSON 值使用。

`validate_command(command, trusted_view=..., authenticated_seat=...)` 检查当前版本、操作权和选择是否属于该决策。调用方必须先完成成员授权及已提交命令的幂等查询；本函数不访问数据库、不执行规则、不证明客户端身份。输入的 trusted_view 必须由服务端规则投影产生。

`IMPLEMENTATION_READY=False` 表示整套产品迁移尚未完成；本阶段可用的是协议库。私有恢复记录、随机种子和引擎类型不属于此包。卡牌文字/图片由本地展示目录提供。完整字段和暂未覆盖内容见仓库 `docs/P2共享协议与规则核心实施记录.md`。

运行要求 Python 3.14，直接依赖固定为 jsonschema 4.25.1 与 referencing 0.37.0，完整闭包在 requirements.lock。包导入不读取工作区或卡池；校验首次调用时仅读取 wheel 内附带的 schema，远程引用不允许。
