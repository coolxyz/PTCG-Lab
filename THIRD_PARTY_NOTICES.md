# 第三方来源与版权

核对日期：2026-10-09。PTCG Lab 为非官方开源工具与 AI 对战实验平台，与 Pokémon 官方无隶属或背书关系。

## 自有代码

项目自有代码和文档使用根目录 `LICENSE` 的 MIT 许可证。该授权不适用于第三方素材、商标、数据集或用户数据。贡献者应仅提交有权许可的代码，不提交私人数据、下载卡图或完整第三方数据快照。

## 规则引擎

来源：[gemelom/ptcg-engine](https://github.com/gemelom/ptcg-engine)，固定基础提交 `92c3cc4fe85a26f102d7bb6b3e8be7678512d2e5`。本地 vendor 源码该版本声明 MIT，完整原文保存在 `licenses/ptcg-engine-MIT.txt`。生成引擎由 `scripts/engine/build_engine.py` 应用本项目修复，改动在脚本及 `packages/rules` 中可查。分发含上游代码的衍生版本时保留其版权与许可；不要把衍生部分全部声明为本项目原创。`runtime/engine/UPSTREAM-LICENSE` 是生成副本的原声明。

## 简中卡牌资料和图片

原数据仓库：[duanxr/PTCG-CHS-Datasets](https://github.com/duanxr/PTCG-CHS-Datasets)。用户可在应用中配置兼容的 fork；fork 是获取渠道，不改变原数据权利和使用条件。该仓库 README 声明用于非商业、学术或研究用途，并要求未经官方权利方书面许可不得再分发。不能将“GitHub 上能下载”理解为 MIT 或可任意再分发。

因此公开源码导出默认排除整个 `data/`、下载快照、卡图、目录数据库和完整第三方测试资料。项目 MIT 不对这些内容授予许可。需自行确认来源条件与使用授权；持有本地副本不等于取得公开发布或商业使用许可。

卡图、卡牌文字、人物及相关商标归各自权利人所有，包括 Pokémon、Nintendo、Creatures、GAME FREAK 等。来源站点包括 [Pokémon 卡牌官方网站](https://asia.pokemon-card.com/) 和数据仓库记录的来源。项目仅记录来源与规则映射，不主张素材权利，也不承诺项目声明可代替权利方授权。

历史研究使用的 [神奇宝贝百科](https://wiki.52poke.com/) 页面、PDF、网页截图和原始卡表适用其各自声明；本项目代码许可证不覆盖它们。公开源码包排除原始页面、PDF、截图和完整卡表，但仍可能含有卡名、修正映射和测试中的短文本。不能把“不含完整数据集”解释为“不含任何第三方内容”；这些内容须按实际来源、表达和许可逐项核验，不因位于源码目录而成为原创。52poke 页面及文件的具体复用条件尚未完成全面核验。

## 参考项目

- [keeshii/ryuu-play](https://github.com/keeshii/ryuu-play)：对战交互与模拟器架构参考；当前 [LICENSE](https://github.com/keeshii/ryuu-play/blob/master/LICENSE) 为 MIT。
- [zjunet/PTCG-Bench](https://github.com/zjunet/PTCG-Bench)：规则验证与 AI 评测参考；当前 [LICENSE](https://github.com/zjunet/PTCG-Bench/blob/main/LICENSE) 为 MIT。

引用链接不表示将参考项目重新授权为 MIT；本公开源码包不附带这两个仓库的完整源码。后续复制或改写其代码时，须核对实际所用提交的许可并保留相应声明。

## 安装依赖

Python 依赖固定在 `apps/api/requirements.lock`，JavaScript 依赖固定在 `apps/web/package-lock.json`。这些包遵循各自许可证；安装结果中的许可证保留在各包目录。公开源码包不包含 `.venv`、`node_modules`、浏览器二进制或打包后的第三方前端代码。若单独分发构建产物，需要一并检查并提供所含依赖的许可声明。

## 发布边界

非商业、研究用途、署名、非官方声明或由用户自行下载，都不等同于取得官方授权。源码开放与完整卡池/卡图再分发、公开托管对战服务、商业运营应分别评估；当前没有据此确认官方素材的再分发授权。公开展示宜使用自制示例素材。

当前前端锁文件涉及 MIT、ISC、Apache-2.0、BSD-3-Clause 和 CC-BY-4.0 等许可；其中 React 为 MIT、lucide-react 为 ISC、构建依赖 caniuse-lite 为 CC-BY-4.0。发布构建产物时应以实际包含的组件为准保留所需版权、许可和署名，不能用根目录 MIT 代替全部依赖声明。

PTCG Lab 是项目名称，不代表官方背书，也不表示已完成商标、域名或软件商店名称核验。本说明记录技术核查与授权边界，不构成完整法律审查。
