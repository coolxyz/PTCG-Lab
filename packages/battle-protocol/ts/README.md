# @ptcg/battle-protocol

P2-A v2 的生成类型，导出 `Command`、`MatchSnapshot`、`Capabilities`、`ProtocolError`、`ReplayPage`、`DeckSubmission` 等合同。`protocolVersion` 为 `2.0`，包仍为 private，不自动发布。

类型由 Python 包内的 `schema/v2.schema.json` 生成。不要手改 src/v2.d.ts。TypeScript 类型无法替代运行时 schema 校验，例如数组数量与两种 choice 的互斥仍需校验器检查。本包不含网络、认证或对战渲染逻辑，未接入旧 apps/web。
