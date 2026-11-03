# iOS 移动端开发与验收

`apps/mobile` 是独立的 Tauri 2 和 React 移动 App，原生 Rust 层持有 SQLite、图片缓存和 API v2 的 HTTPS/WSS 连接，复用桌面产品的大厅、对战桌面与本人回放。应用内不运行 Python、HTTP 后台、本地对战规则或人机策略；不使用桌面 profile。构建、使用和验证步骤如下。

iPhone XR / iOS 18.7.11 已完成开发签名安装、卡库/卡图/卡组及真实 Keychain 身份保持验证。2026-11-03 用户确认 iOS 相关测试、XR＋桌面完整对局/本人回放和升级安装通过，功能验收按其确认范围收口。最低系统无需实机测试；其他历史候选的失败/pending 保留，不自动改写为重测通过。iOS 将通过 TestFlight 有限发布，上传与分发签名尚未执行；许可及分发材料另行审阅，不阻塞 API 交付。最新范围见[构建与分发说明](构建与发布.md)。

## 环境

构建机需要完整 Xcode、iOS SDK、CocoaPods、Rust 1.90.0、Node/npm 和锁定的 Tauri CLI 2.12.4。模拟器运行还需 Xcode 中安装对应系统运行时；较新的 Xcode 使用 Device Hub，旧版使用 Simulator.app。Rust crate 使用 Tauri 2.12.5 / tauri-build 2.7.4，依赖闭包见移动项目的 Cargo.lock。声明最低 iOS 16.0；需相应设备验证，不能从 SDK 编译成功推导最低系统通过。

```sh
npm ci --ignore-scripts
python -B scripts/native/ios.py doctor
rustup target add --toolchain 1.90.0 aarch64-apple-ios aarch64-apple-ios-sim
npm run mobile:check
npm run mobile:build
cargo +1.90.0 test --locked --manifest-path apps/mobile/src-tauri/Cargo.toml --no-default-features
```

构建维护工具使用 Python 3.14+；它不会进入移动包。doctor 只读检查 Xcode 首次设置/SDK/CocoaPods/XcodeGen/模拟器界面（Simulator 或 Device Hub），不自动安装组件或操作账号。使用专用工具链时显式设置 PATH、RUSTC、CARGO_HOME 和 CARGO_TARGET_DIR，避免与其他构建共用输出目录。iOS 构建说明参照 [Tauri 移动开发](https://v2.tauri.app/develop/) 与 [iOS 前置环境](https://v2.tauri.app/start/prerequisites/#ios)。

文件选择、分享和数据保护的 Swift 包位于 `src-tauri/ios`，通过标准 C FFI 与 Rust 连接；Cargo 的 iOS 原生构建会调用 SwiftPM 编译此静态库，无额外的远程 Swift 依赖。接口只接受卡组、收藏、展示资料几类产品操作，不开放路径或通用文件能力给 GUI。方式参照 [Tauri 的 iOS C FFI 说明](https://v2.tauri.app/develop/plugins/develop-mobile/#ios-2)。

## 展示资源与构建

源库不附带卡表/原图/用户数据。指定合法取得、与当前随包索引匹配的批准资产目录；工具只读卡表及中文详情，不复制运行时或数据库。输出目录必须是新的独立目录。

```sh
python -B scripts/native/ios.py prepare \
  --assets <批准资产目录> \
  --output var/ios-candidate
```

生成 `resources/{catalog,artwork,manifest}.json` 和私有 `tauri.ios.conf.json`；目录和内容绑定 SHA-256。印刷目录版本和服务器使用的语义展示目录版本分别保留，不互相替代。固定印刷/图片索引不匹配则拒绝生成；不能拿一份索引配另一份卡库。资源包含中文说明、招式和别名，不包含规则执行能力。配置文件中的绝对路径仅用于本机构建，不纳入 Git。

完整无分发签名候选包使用独立构建目录，先完成下节的工具准备，再从仓库根执行：

```sh
python -B scripts/native/ios.py candidate \
  --assets <批准资产目录> \
  --work var/ios-build-01 --output release/ios-candidate-01 \
  --rust-bin <Rust-1.90.0-bin绝对路径> --cargo-home <专用Cargo目录> \
  --tools-bin <私有工具目录>/bin --gems <私有工具目录>/gems \
  --target simulator --target device
```

工具先只读检查 `xcodebuild -checkFirstLaunchStatus`，未完成则退出，不代替操作者初始化 Xcode。通过后导出隐私检查过的源码快照，以锁定依赖构建前端、准备展示资料及许可证据，再由 Tauri/Xcode 完整链接。Node/npm 离线缓存和 Cargo 锁定的 crate/目标标准库须预先备齐；缺项会失败，不自动升级依赖。完整流水线使用移动项目的 `npm run tauri --` 入口生成 Xcode 脚本；不要直接用 Node 调用 tauri.js 初始化，否则生成的 Xcode 阶段可能找不到 CLI。SwiftPM 构建前会局部移除 Xcode 传入的 iOS SDKROOT，让 Package.swift 用 macOS SDK 编译，再由锁定的 swift-rs 选择目标 SDK；不会修改系统环境。输出/工作目录必须全新，失败查看 `BUILD-REPORT.json`、工作目录的 `PROGRESS.json` 和分步日志，重试改用新目录。生成 Xcode 工程仅在私有源码副本中。可显式传 `--target-cache <专用缓存目录>` 复用 Cargo 编译缓存；缓存与发布目录分开、拒绝符号链接，源码快照和最终包仍重新生成及审计。

成功输出按 `simulator/` 与 `device/` 分开：`.app.zip`、SHA-256、`BUNDLE-AUDIT.json`、`ACCEPTANCE.json` 和验收步骤。审计检查真实 Mach-O 的 arm64/平台/最低系统、App 身份、动态依赖、展示资料哈希及许可文件集合，并拒绝 Python、规则执行器、用户数据库和凭据。仅完成 SDK 检查、合成测试或生成工程时不会产生“已通过”记录。构建成功仍为 `runtimeAccepted=false`、`releaseReady=false`。

该命令不进行设备或分发签名：Tauri 临时配置使用 `0000000000` 哨兵值满足配置校验，它不是 Apple 开发团队；真机 SDK 构建禁用签名，模拟器则按下文使用标准本地 ad-hoc 流程。工具禁用全局 Homebrew 安装及真机日志访问。`idevicesyslog` 是显式报错的禁用入口，不代表已安装 libimobiledevice，也不支持通过该入口部署/查看真机日志。构建脚本不读取 Apple 账号凭据，不操作已运行模拟器或设备。Xcode/模拟器应由操作者安装完成后再构建。

模拟器包只能用于对应架构的模拟器，不能安装到 iPhone/iPad。真机分发必须在单独受控签名流程中使用真实开发团队、设备条件与签名，再验证最终签名产物。未签名包、源码 ZIP、展示资料包都不是可直接安装的 IPA；不要仅修改验收 JSON 就宣称签名分发完成。参照 [Tauri iOS 签名说明](https://v2.tauri.app/distribute/sign/ios/)。签名私钥、provisioning profile、账号和导出凭据不得进入源码。

模拟器包在最终资源组装完成后重新生成本地 ad-hoc 代码封印，并执行 `codesign --verify --deep --strict`；封印文件一并归档。`--no-sign` 与缓存重用可能留下不匹配的旧资源封印，造成安装成功却被系统拒绝启动，不能只检查 Mach-O。ad-hoc 不使用 Apple 账号、证书或 provisioning profile，不代表设备或分发签名；报告分别记录 `codeSeals` 和 `distributionSigned=false`。真机候选仍需实际团队签名，不能套用模拟器封印安装。

模拟器构建启用 Xcode 标准本地签名流程，使链接器生成 `__TEXT,__entitlements` 模拟器身份；默认 Keychain 访问组仅为本应用。项目按 SDK 条件使用 `CODE_SIGN_IDENTITY=-`、手动本地签名和空开发团队，并显式保留 iOS 16 最低系统；不会选择用户的分发证书。全程使用 `--no-sign` 的模拟器包虽然可能启动，却缺少这个身份，网络客户端会收到 Keychain `-34018`。不要将这些模拟器信息直接写入真实代码签名来替代标准构建流程。

打包审计会检查模拟器链接身份只属于本 App，并拒绝真机包包含模拟器身份；完成资源封印后再次核对归档。真机 SDK 候选仍使用 `--no-sign`，实际设备签名必须使用操作者的真实团队与授权配置。默认访问组按应用标识隔离，参照 [Apple Keychain 访问组说明](https://developer.apple.com/documentation/security/sharing-access-to-keychain-items-among-a-collection-of-apps)。模拟器成功不能据此通过真实设备 Keychain、锁屏或备份验收。

## 在模拟器中安装候选包

构建成功的 `simulator/PTCG-Lab-ios-simulator.app.zip` 供 Apple Silicon 模拟器使用。先核对 `.sha256`，用 `ditto -x -k <候选ZIP> <全新解压目录>` 保留权限。通过 Device Hub 新建本次专用 iPhone/iPad，确认系统运行时可用；只操作该设备，不使用不明确的 `booted` 别名或重置其他设备。

```sh
xcrun simctl create "PTCG iOS 验收" <device-type-id> <runtime-id>
# 将上一步返回的本次设备 ID 用在以下命令中
xcrun simctl boot <本次设备ID>
xcrun simctl bootstatus <本次设备ID> -b
xcrun simctl install <本次设备ID> "<解压目录>/PTCG Lab Mobile Preview.app"
xcrun simctl launch <本次设备ID> org.ptcglab.mobile.preview
# 结束测试后仅关闭本次设备，保留资料供重开验证
xcrun simctl shutdown <本次设备ID>
```

在 Device Hub 选择这台专用设备查看实际界面，工具入口参照 [Apple Device Hub 说明](https://developer.apple.com/documentation/xcode/managing-your-simulated-and-physical-devices-in-device-hub)。运行时可用、App 可启动、页面正确渲染和业务验收分别核实。启动白屏可能属于真实原生导航问题，不能用浏览器通过记录代替；内嵌根地址同时接受 `tauri://localhost` 与其带斜杠形式，其他来源仍拒绝。iOS 窗口大小由系统管理，避免固定桌面尺寸裁切手机页面。

## 明确授权后的开发签名真机安装

SDK 构建的 device ZIP 仍是未签名输入。单独的受控签名步骤须校验真实开发证书、描述文件有效期、应用标识、目标设备和证书匹配关系；使用本应用独有的 application-identifier/Keychain 组，不能把模拟器虚拟身份带入真机。签名后校验最终代码封印、entitlement、原展示资源，并在归档解压后重新验证。最终签名 ZIP/IPA 使用新的 SHA 和独立 ACCEPTANCE.json，不能覆盖原 SDK 候选账本。

在明确指定且已配对、开启开发者模式的设备安装，只选择操作者授权的设备 ID：

```sh
xcrun devicectl device install app --device <授权设备ID> "<开发签名App路径>"
xcrun devicectl device process launch --device <授权设备ID> org.ptcglab.mobile.preview
```

不自动替操作者完成信任、密码输入、账号或开发协议。已有描述文件可以满足条件时，不为验收创建额外证书；Xcode 管理的描述文件用于 XCTest Runner 时应按 Xcode 的自动签名规则复用，不能强指定为手动管理。临时工程、设备标识、描述文件、签名选择及凭据不进入源码。开发签名安装成功不是 App Store/正式发行通过。

系统文件交换位于“文件 → 我的 iPhone → App 的 Documents”。局域网服务器的公开 CA 是粘贴到 App 的服务器配置中；不需要全局安装系统根证书。人工和 XCTest 的结果分别记录，尤其是数字输入、软键盘遮挡和长证书输入：确认输入全文准确再保存，重开核对实际数据，不能把按钮点击日志当作事务已提交。

## 系统 TLS 与卡图验证

原生 HTTPS 与 WSS、Raw/CDN 卡图和展示 JSON 下载共用锁定的 `rustls-platform-verifier` 0.7.1。Apple 平台通过 Security.framework 检查证书链、服务器名称、签名与用途；不再假设 iOS 存在 Unix CA 文件。局域网公开 PEM CA 仅附加到所选服务器的 TLS 配置，HTTP 与 WebSocket 使用相同信任，仍绑定原服务器/CA 身份。不会关闭证书校验或自动信任未知证书。参照 [验证器官方说明](https://docs.rs/rustls-platform-verifier/0.7.1/rustls_platform_verifier/)。

本机系统代理不会隐式成为原生网络配置。若公网来源不可访问，可按上文更换可访问的 HTTPS CDN；不要通过跳过证书验证解决。卡图须通过固定 Git blob 和完整图像解码才缓存，失败遵循有限退避。模拟器显示成功不代替真实设备网络、锁屏 Keychain、缓存回收或完整对局验收。

## 私有构建工具与依赖证据

`ios-tools.lock.json` 固定 CocoaPods 1.16.2、XcodeGen 2.44.1 及其 Ruby gems 的版本、依赖与下载 SHA-256。当前私有安装配方适用于 macOS 系统 Ruby 2.6.10；其他 Ruby 版本明确拒绝，需要维护者另行审核配方，不隐式替换系统 Ruby。该配方是构建维护工具，不进入 App。

```sh
python -B scripts/native/ios.py tools --output var/ios-tools
# 缓存齐备后可离线复现；输出目录必须不同
python -B scripts/native/ios.py tools \
  --output var/ios-tools-reproduced --cache var/ios-tools/downloads --offline
PATH="$PWD/var/ios-tools/bin:$PATH" \
GEM_HOME="$PWD/var/ios-tools/gems" GEM_PATH="$PWD/var/ios-tools/gems" \
python -B scripts/native/ios.py doctor --runtime
```

安装仅写指定私有目录，不调用 sudo/Homebrew。逐一验证 `.gem` 摘要、真实 gemspec 的 `ruby` 平台、Ruby 版本及锁定依赖，拒绝 Java/其他平台和校验不符的包；保留 TLS 校验。XcodeGen 同时安装平台预设文件，避免只有可执行文件却缺失构建设置。工具检查与 App 运行检查分别记录；`doctor --runtime` 在 Xcode 首次设置未完成时不调用 simctl，避免它隐式启动全局初始化。

许可清单覆盖 Cargo iOS 目标与宿主构建依赖的保守闭包，以及前端声明的运行依赖，不声称每项都最终链接。Rust 原文仅从与 Cargo.lock SHA 一致的 crate 提取，npm 原文取锁定安装目录；workspace 使用其实际版本与项目许可。`licenses/INVENTORY.json` 保留逐项来源、文件摘要及 `missingEvidence`，`THIRD-PARTY.html` 方便人工审阅。上游只有 SPDX 声明但未附原文的依赖继续记为缺证据，不能据此通过分发许可审阅。

## 候选包验收记录

验收清单包含离线重开、收藏 CSV 去重/撤销、冻结版本、系统文件与分享、目录事务更新、图片缓存、Keychain/锁屏、后台/终止恢复、手机/iPad、跨桌面对局与回放、升级和签名等 19 项。每个包分别记录，所有项目初始为 `pending`。

```sh
python -B scripts/native/ios.py record \
  --ledger release/ios-candidate-01/simulator/ACCEPTANCE.json \
  --artifact release/ios-candidate-01/simulator/PTCG-Lab-ios-simulator.app.zip \
  --case launch-offline --result pass --evidence <实际截图或日志> \
  --system <实际系统版本> --device-class <设备类别> --operator <测试者>
```

记录前核对候选包实际 SHA，`pass` 必须附真实证据；不记录 UDID、令牌或完整私有对局。合成测试不是 Keychain/WKWebView/设备运行证据。验收结果不会自动开启正式发布门禁，最终签名、分发资料与许可审阅仍需单独完成。

## 当前使用范围

- 卡库：按名字/卡号/卡包搜索，分页显示，轻触打开中文说明。
- 收藏：按“未标注、全新、良好、使用痕迹”分别保存数量、备注和愿望单；卡面快捷“＋”记录到未标注品相，详情可编辑其他品相。
- 卡组：新建、选牌、显式保存、结构检查和冻结版本。冻结只确认结构与目录身份，对战准入始终以服务器为准。
- 卡组交换：粘贴或通过系统文件选择 CSV、TXT、JSON，兼容桌面导出；检查后显式新建卡组，可按所选格式分享或复制。不猜测跨版本身份、不覆盖已有卡组；支持查找、改名、复制及带确认/版本检查的删除，保留冻结快照和历史。
- 图片：Raw 或 HTTPS CDN，逐图校验并缓存；断网可看已缓存图片。移动端不能访问桌面机器的 Git 克隆绝对路径，不提供卡图 ZIP 导入。缓存可被系统回收；收藏与卡组放在独立的应用数据目录。
- 网络：原生 API v2 连接服务器，使用本机卡组的冻结版本创建/加入邀请房间、准备、取得操作权、对战和本人回放。服务器执行当前规则并决定准入；本机结构检查不等同于完整规则校验。XR＋macOS 完整对局及回放已有用户人工验收通过；两移动端/iPad 及真机故障恢复矩阵另验。

系统文件选择使用 iOS 的“文件”提供器。App 开放自己的 Documents 作为用户交换文件的位置，便于在“我的 iPhone/iPad”中放入卡组或 CSV，再从 App 显式选择导入；选择文件不会自动覆盖资料。SQLite、加密对战日志及凭据仍位于私有应用数据区域，不存入 Documents。Files 可见性、本机保存和分享接收方内容需要分别实测；仅启用文件共享设置不代表整项文件验收通过。

开发分支另有宿主原生真实规则探针 `apps/mobile/src-tauri/examples/real_network_probe.rs`。它只接受显式启用验收的 HTTPS 回环地址、20000 以上端口、公开 CA 和 60 张卡组，使用全新的输出目录及随机测试密钥，不访问系统 Keychain。由独立 API kit 提供当前规则身份；两位新身份通过正常准入、邀请、控制权和合法决策完成自然对局，检查 WSS 游标补发、已提交命令丢失响应后沿用原 commandId 恢复、加密日志重开及双方本人回放。以锁定工具链执行 `cargo run --locked --offline --no-default-features --manifest-path apps/mobile/src-tauri/Cargo.toml --example real_network_probe -- <配置JSON绝对路径> <全新输出目录绝对路径>`；配置字段为 `acceptance: true`、`server: {origin, caPem}`、`ruleReleaseId` 和 `entries: [{printingId, quantity}]`。不得对正式服务运行；结果明确标注宿主执行，不能代替 iOS GUI、进程终止、设备 Keychain 或 Mac 与 iOS 双端验收。

## 连接服务器与恢复

1. 在“对战”页填写服务器提供的 **HTTPS origin**，例如 `https://battle.example.com:8443`，不带 `/api` 路径、用户名、查询参数或授权令牌。不能使用 HTTP 或关闭证书校验。
2. 公网可信证书通常无需额外设置。局域网自签名环境展开“局域网服务器的 CA 证书”，粘贴服务同批生成的公开 PEM CA；证书名称/SAN 仍须与访问地址匹配。不要粘贴私钥。iOS 首次访问可能询问局域网权限。
3. 保存配置后，在大厅显式创建游客身份。选择平台已有卡组及冻结版本，点击“校验并使用此版本”，再创建邀请房间或输入朋友的房间码。双方准备后进入共用对战桌面；需要操作时取得操作权。
4. 离开对战页不会认输。原生层保存身份、本人可见局面/游标和待确认操作；回到原服务器后可继续对局或打开本人回放。iOS 只使用随包资源和原生连接，不需要终端授权码，也不提供“打开另一个窗口”。

令牌、真实 controllerId、私有快照和原始请求保存在原生 AES-256-GCM 日志；随机密钥由不参与同步的 `WhenUnlockedThisDeviceOnly` Keychain 项持有。日志绑定服务器地址及 CA 信任身份，换地址/CA 会选用独立配置，不会给旧记录改标签。GUI 仅保存导航与重试 ID，取得的是本机临时操作租约，不接触远程凭据。加密日志与 SQLite 属于应用数据，卡图缓存不含这些资料。

命令发送前先持久化原 commandId、请求体及控制身份。响应未知时，使用“恢复待确认操作”查原回执；必要时只重发原请求。控制权恢复沿用原 requestId/controllerId；不会制造新操作绕过未知结果。刷新令牌轮换响应未知时停止复用该刷新令牌，身份创建结果未知时停止重复创建游客；请保留应用数据，勿通过清缓存、改服务器或重建游客掩盖问题。存在未确认操作时会阻止切换配置。密钥缺失、损坏日志或保存失败会报错并保留资料，不降级为明文或覆盖旧记录。

进入后台会退役原生推送连接并清除临时操作权；前台刷新原身份、快照和推送，操作权需重新取得。晚到帧不会从已退役的连接写入或展示。已在专用 iOS 模拟器实际验证 Home 后补齐对手期间的操作、清除旧操作权、专用 HTTP/WSS 故障后按本人游标重连，以及已提交命令响应被截住时终止/重开进程并找回原回执；命令未重复提交，控制身份跨进程保留。这不等于真机锁屏、系统因资源压力回收、待确认刷新令牌轮换或延迟旧流帧注入均已通过。具体设备保护与完整恢复矩阵仍需 iOS 实机验证。当前没有后台常驻运行承诺。

网络展示只采用与服务器语义目录身份一致的本地卡名、中文说明及卡图映射；不同身份使用文字降级，不猜测卡面。图片仍走原生 Raw/CDN 下载和已验证缓存；服务器不转发卡图。展示目录可按下文的 JSON 流程单独更新；App 壳、原生能力与数据库结构变更仍需要更新 App。

首次运行没有缓存时仍可离线查目录和中文说明；原图缺失显示占位。已完成的保存是原生事务，不依赖退出时 JavaScript 再提交。前台刷新保留内存中的未保存表单；收藏和卡组用原版本检查，拒绝覆盖其他修改，可显式重新载入，卡组还可另存副本。系统终止后只保证已提交资料，未保存草稿请先点保存；不把后台内存保留等同于杀进程后的草稿恢复。

## 卡组管理与桌面交换

在底部进入“卡组”，可以新建、改名、编辑数量、另存副本和按名称查找卡组。“将草稿另存为新卡组”也可以复制已保存的卡组，原卡组保持不变。存在未保存修改时，先保存、另存，或用“重新载入（放弃草稿）”恢复保存版本，再切换卡组或导入。

桌面 App 的卡组 **CSV 和文本导出均可直接导入手机**，无需转换 JSON：

1. 在桌面构筑页面导出 CSV 或 TXT，通过 AirDrop、iCloud Drive 等保存到手机“文件”。
2. 手机“卡组 → 选择卡组文件”，在系统面板选择“我的 iPhone”、iCloud Drive 或已启用的文件提供器中的 `.csv`、`.txt`、`.json`。选取只读取内容，取消不会改变资料。
3. 核对自动展开的内容，点击“检查并新建卡组”。导入失败会保留原卡组和输入内容，成功新建独立卡组，再按需改名保存。也可展开“导入卡组文本”直接粘贴。
4. 手机选择卡组后，在“卡组文件格式”选 CSV／TXT／JSON，再查看可复制内容，或点“分享卡组文件 → 存储到文件”。CSV 默认为桌面和手机的通用交换格式；桌面可用原有卡组导入流程读取手机 CSV／TXT。

CSV 保持桌面格式，包括 UTF-8 BOM、CRLF 和转义；必须包含不重复的 `printingId`、`quantity` 列，列顺序可交换。TXT 每行使用“数量 印刷ID”，例如 `2 CN:扩充包编号:卡号`。JSON 使用 `name` 和 `entries: [{printingId, quantity}]`；有 `schema` 时须为 1，有 `catalogVersion` 时须匹配本机目录。CSV／TXT 不包含卡组名称，默认命名“导入的卡组”。每文件最多 128 KiB、500 行，按完整内容校验后才保存；仅接受准确的印刷 ID，不按同名卡猜测印刷版本。未知 ID 请先核对/更新手机展示目录，不通过删除版本字段绕过规则身份检查。

“删除卡组”须先结束未保存编辑，并在显示卡组名称的确认区再次确认。此操作删除可编辑卡组且无法撤销；需要保留时先分享导出。原生层按卡组版本校验，确认期间已被其他操作更新则拒绝删除，重新载入后再处理。冻结版本与历史对局保留，既有对战引用的内容及 hash 不变。

维护时验证 CSV/TXT/JSON 往返、错误内容不部分写入、过期版本删除拒绝、删除后冻结快照重开，并在设备上实际完成文件选取、显式导入、分享接收、取消删除及确认删除。接收文件须只读核对 BOM、列名、印刷 ID 与数量，不以分享面板出现代替内容正确。

## 收藏交换与撤销

收藏页选择桌面端导出的 UTF-8 CSV，或粘贴 CSV 内容，先预览新增张数再确认。格式为：

```csv
printingId,quantity,condition,notes,wishlist
<准确印刷ID>,2,良好,我的备注,true
```

每批最多 500 行、1 MiB，每项数量 0–9999，备注最多 500 字。只接受准确 printingId；未知 ID 请先安装匹配目录，不自动猜同名印刷。数量按版次与品相**相加**，愿望单合并；这是资料交换，不是跨设备自动同步或全量覆盖。导出保持桌面 CSV 列名、BOM 和引号/换行转义；备注中的公式前缀作文本保护。

同一规范化批次重复提交不会重复增加数量。在“导入批次”中可撤销导入数量，之后新增的数量及单独修改过的备注/愿望单会保留；数量已被减到不足撤销时整批拒绝，原资料不变。已撤销批次不能再次作为新批次增加，需显式编辑收藏。分享只导出收藏字段或卡组文件，不包含游客凭据、controllerId、私有回放或数据库。

文件选择取消不会导入；选到卡组/收藏文件后仍需确认导入。iOS 使用 UIDocumentPickerViewController 与 UIActivityViewController，外部文件只在本次系统授权期间读取，不向 GUI 返回绝对路径。分享副本保存在受保护的临时目录，完成后清理，异常终止残留在下次启动清理；系统面板、iPad 弹出位置、iCloud 文件提供器及实际接收文件仍须设备验证。

## 单独更新展示目录

卡表、中文说明与图片索引可以作为一个**不含原图、不含规则**的 JSON 文件更新，无需重装 App。维护者先从批准资产准备资源，再生成发布目录：

```sh
python -B scripts/native/ios.py prepare --assets <批准资产目录> --output var/ios-candidate
python -B scripts/native/ios.py bundle \
  --resources var/ios-candidate/resources --output var/ios-display-release
shasum -a 256 var/ios-display-release/mobile-display.json
```

输出 `mobile-display.json`、对应 `.sha256` 和 `DISPLAY-RELEASE.json`。目录必须为新目录；工具校验内部文件哈希和目录身份，不打包图片、规则、Python 或用户数据。生成不等于发布；是否提供下载及资料再分发权限需按来源条件单独确认。

在“设置 → 展示资料更新”填写发布者提供的 64 位 SHA-256，再选择 JSON 文件，或填写公开 HTTPS 文件 URL 后下载。URL 不带凭据/查询参数，不跟随重定向；不支持 HTTP 或绕过 TLS。私有分发环境可先手动下载文件再通过系统选择。SHA 应从可信发布说明核对，不把文件自带的哈希当作发布者签名。

预览包含实际文件 SHA、目录版本、增删张数和效果映射变化；确认后才应用。下载或校验失败不切换，收藏/草稿/冻结版本引用的 ID 缺失则拒绝应用。目录先完整持久化，再由 SQLite 原子提交激活指针；重开读取同一完整资料包。损坏的已激活目录会报错并保留文件，不偷偷降级重写身份。

待确认的命令、控制权、会话轮换或大厅操作会阻止应用更新；先到原身份恢复。目录改变不修改旧冻结 hash 或原 commandId，新目录版本下提交卡组需显式重新冻结。规则执行始终由服务器批准发布决定，安装目录资料不会获得新规则能力，也不会运行旧规则；应让客户端展示资料与服务器发布一致。

展示目录空间上限 512 MiB，单包最多 80 MiB。“清理未使用展示资料”只移除不再使用的下载目录，保留当前目录、待安装包、收藏、卡组与对战记录。原图仍按需使用 Raw/CDN 及 blob 校验缓存，不引入图片 ZIP 或桌面 Git 路径。

## 迁移与数据保护

本地数据库由 schema 1 事务迁移到 2：原收藏数量/愿望单归入未标注品相，保留卡组 ID、版本和冻结内容 hash；失败整批回滚。迁移只涉及产品数据，不兼容旧规则执行器。较新、损坏或未识别的已有数据库会拒绝打开，不重建或清库。不能把桌面 SQLite 直接复制为移动数据库，请通过卡组 CSV/文本/JSON 和收藏 CSV 交换。

iOS 原生层给 SQLite/WAL/SHM、加密日志和临时分享文件设置完整文件保护；锁定通知独立于 JavaScript 退役网络流，解锁后读取已提交数据，图片可按需重新读取。Keychain 密钥只在本设备解锁时可用，`online` 日志目录主动排除系统备份，避免带着无法迁移的密钥绑定记录恢复到另一设备。收藏/卡组不作这项排除，但系统备份、恢复和跨设备身份交换尚未验收，不能据此承诺自动找回游客身份。升级、卸载或迁移前请导出需要的资料，不要删凭据目录解决网络故障。

## 验证

大厅布局回归须包含 WebKit，而不只以 Chromium 的窄视口代替 iOS。WebKit 原生下拉框的完整规则哈希曾在 375px 竖屏将文档撑到约 545px，控件自身边界仍在视口内；只检查控件 bounding box 会漏报。移动层以受约束的 select 外观、省略显示及单列/换行修复，保留系统选项操作，不以隐藏整页横向溢出来掩盖问题。`apps/mobile/e2e/online.spec.ts` 同时检查文档 scrollWidth，覆盖 320、375、390、414、844、1024px，以及 XR 尺寸已有服务器/身份与历史对局时直接进入大厅、翻页和重开。开发环境可将锁定 Playwright 的 WebKit 安装至私有缓存，再以 `PTCG_TEST_BROWSER=webkit PLAYWRIGHT_BROWSERS_PATH=<私有缓存绝对路径>` 运行；默认 Chromium 可继续使用 `PTCG_BROWSER_PATH`。浏览器 WebKit 合成测试与 iOS App 实机验证分别记录。

```sh
python -m pytest -q tests/packaging/test_mobile_assets.py
npm exec -- playwright test --config apps/mobile/playwright.config.ts
cargo +1.90.0 run --locked --manifest-path apps/mobile/src-tauri/Cargo.toml \
  --no-default-features --example offline_probe -- <prepare输出目录>/resources <bundle输出目录>/mobile-display.json
cargo +1.90.0 build --locked --manifest-path apps/mobile/src-tauri/Cargo.toml \
  --no-default-features --examples
PTCG_MOBILE_NETWORK_PROBE=<绝对路径>/network_probe \
PTCG_MOBILE_PROTOCOL_PROBE=<绝对路径>/protocol_probe \
PYTHONPATH=packages/battle-protocol/src:packages/online-client/src \
python -m pytest -q tests/mobile
```

布局测试用合成原生命令适配器，覆盖手机/iPad 横屏、安全边界、卡牌点击、收藏、卡组编辑重开、来源设置、共用大厅/Arena/回放、CSV/资料更新入口、取消文件操作以及挂起时的晚到控制权和未保存表单冲突。可通过 `PTCG_BROWSER_PATH` 指定隔离测试浏览器可执行文件。`offline_probe` 使用真实展示元数据和一次性临时 SQLite，检查收藏/卡组/冻结版本重开、网络展示目录映射，以及可选展示资料包的校验/应用/重开，不访问真实 profile、不下载图片；它是宿主原生探针。`tests/mobile` 使用临时回环 HTTPS/WSS 合成服务及当前共享 Python 协议验证器；需要测试 Python 环境包含 pytest、cryptography 及共享协议验证依赖。未设置探针路径会跳过相应测试，跳过不算通过。合成服务不执行规则；这些测试不代替 WKWebView、真实 API 对局或设备 Keychain 验收。

原生界面驱动与专用环境执行步骤见 [XCUITest 验收驱动](../tests/mobile/ios-ui/README.md)。只对明确授权的专用模拟器运行，显式选择设备和单个有状态用例；实际配置、Runner 构建及 xcresult 留在私有目录。

设备验收须记录：当前源码摘要、SDK/工具链、系统版本、设备、签名方式、资源版本和真实结果。分别检查离线重开、中文/缩放/安全区、旋转、键盘、触控详情、磁盘不足、损坏数据库保留、缓存回收、来源切换与升级。网络重点验证原 commandId 恢复、会话轮换、控制权、游标补漏、本人回放、锁屏/后台/强制终止以及跨桌面完整自然对局；分别记录真实服务执行和合成测试来源。

项目已按用户确认的 iOS、桌面跨端对局/回放及升级安装范围完成本轮功能验收；最低系统无需实机测试。这里保留的是后续构建可复用的验证步骤，不是每个新构建自动通过的记录。各候选真实结果仍按来源单独保存。TestFlight 有限分发的签名上传、许可/隐私材料单独准备，详见[构建与分发说明](构建与发布.md)。
