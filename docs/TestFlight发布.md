# iOS TestFlight 有限分发

当前产品支持 macOS Apple Silicon、Windows x64 与 iOS；桌面开源源码自行编译，iOS 采用 TestFlight 邮件邀请。本文是发布者手顺，开发完成与 Apple 签名、上传、审核通过分别记录。SDK 无签名 ZIP、XR 开发签名 IPA 均不是已经发布的 TestFlight 包。

## Release 归档准备

需要批准的展示资料、锁定 Rust 1.90.0/Node/Cargo 缓存、Xcode、CocoaPods 和 XcodeGen。资源和工具准备见 [iOS 使用与构建](iOS开发与验收.md)。构建只在全新私有目录中进行，不读取真实用户 profile、登录 Apple 账号、创建证书、安装 App 或上传。

```sh
python3 -B scripts/native/ios.py testflight \
  --assets /approved/product-assets \
  --work var/testflight-work --output release/testflight-candidate \
  --rust-bin /approved/rust-1.90.0/bin --cargo-home /approved/cargo \
  --tools-bin /approved/ios-tools/bin --gems /approved/ios-tools/gems \
  --team YOURTEAMID --build-number 1
```

替换实际路径与十位 Team ID；Build 使用 1～9999 的递增整数，上传过的组合不能重复。可显式 `--target-cache` 复用专用缓存。脚本生成真实 iPhoneOS **Release** 归档，禁用构建阶段签名；它使用真实团队标识，不使用模拟器身份或占位团队。保留源摘要、当前主图/18 个尺寸、展示资料 pin、许可闭包和根目录隐私清单校验。许可原文证据不完整就停止该入口，不以 SPDX 字符串替代全文。

输出包括 `PTCG-Lab-ios-release-unsigned.xcarchive.zip`、SHA-256、`ExportOptions.plist`、`TESTFLIGHT-HANDOFF.json` 与操作说明；另保留设备 SDK App ZIP 和常规构建审计。交接状态是 `ready-for-operator-signing`，不是 `distributionSigned` 或 `uploaded`。不要直接把这个 ZIP 拖到 Transporter 当作 IPA。

## Apple 账号、签名与上传

1. 确认 Apple Developer Program 与 App Store Connect 权限，创建 App 记录。当前 Bundle ID 为 `org.ptcglab.mobile.preview`，版本为 `0.8.0`；与构建保持一致。
2. 核对 ZIP SHA，使用 `ditto -x -k <归档ZIP> <全新解压目录>` 保留权限，打开解压出的 `.xcarchive`，由 Xcode Organizer 使用对应实际团队完成分发签名与 Validate App。
3. 选择 **Distribute App → App Store Connect → Upload**。计划外部测试时不选择 Internal Only。也可先 `xcodebuild -exportArchive -archivePath <归档> -exportPath <新目录> -exportOptionsPlist <本包ExportOptions.plist>` 导出签名 IPA；自动管理 profile 所需账号权限由操作者决定，不把证书、profile、私钥或 token 写进 Git。
4. 处理完成后，在 TestFlight 补充 What to Test、反馈邮箱、审核联系人和操作说明，完成加密信息问卷，再创建测试组并选择构建。先内部确认，再提交外部 Beta 审核；通过后按邮箱邀请。
5. 每份构建有 90 天测试期限，后续上传递增 Build。上传、Beta 审核通过和实际分发必须各自有证据，不由本地编译成功推断。

官方依据：[Tauri 分发](https://v2.tauri.app/distribute/app-store/)、[Apple 上传](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds)、[外部邀请](https://developer.apple.com/help/app-store-connect/test-a-beta-version/invite-external-testers/)。

## 隐私、网络与加密信息

`apps/mobile/src-tauri/PrivacyInfo.xcprivacy` 由 Xcode 资源阶段复制到 App 根目录。当前没有广告或跨 App 跟踪；声明游客用户/设备标识与游戏内容用于对战功能，并与游客身份关联。文件时间元数据用于 App 容器内缓存/资料，运行计时用于网络超时和重连；原因代码分别为 C617.1、35F9.1。源码清单按当前实现维护，不能给未实现用途或追踪随意加声明。

本地收藏、卡组草稿和备注保存在设备上；只有用户提交网络卡组/进行对战时向所选 API 发送相应冻结卡组、身份和合法命令。原图来自用户选择的 Raw/CDN，服务器/图源可能处理 IP 和请求记录；运营者须按实际保留方式补充隐私政策、App Store Connect 标签、支持邮箱和数据删除渠道。不要直接填写“未收集任何数据”。若启用额外分析、日志或第三方 SDK，重新核对声明。Xcode Organizer 的 Generate Privacy Report 和 Apple 的上传检查仍由发布者执行。

目前原生 HTTPS/WSS 使用 rustls/系统验证，本地恢复日志使用 AES-GCM。问卷须按实际实现及分发地区回答，不自动把 `ITSAppUsesNonExemptEncryption` 写成 NO。构建工具准备事实资料，不替代运营者的豁免/材料判断。

审核人员和外部测试者若测试网络对战，应能访问真实 HTTPS API 和卡图源；本地 `10.*` 测试地址不能当作公网入口。审核备注说明游客身份、导入/冻结测试卡组、创建/加入房间、对局和本人回放步骤；准备合法可分发的示例卡组和可运行服务，不把私人 token 或 profile 发给审核方。

参考：[隐私数据声明](https://developer.apple.com/documentation/bundleresources/describing-data-use-in-privacy-manifests)、[所需原因 API](https://developer.apple.com/documentation/bundleresources/app-privacy-configuration/nsprivacyaccessedapitypes/nsprivacyaccessedapitypereasons)、[加密问卷](https://developer.apple.com/help/app-store-connect/manage-app-information/overview-of-export-compliance/)。

## 许可和资源

`licenses/mobile/supplemental.json` 绑定缺失原文的精确 crate 版本、归档 SHA、原提交和文本 SHA。构建无网络补充上游声明、标准条款、作者元数据和源码获取地址，并检查完整闭包；声明齐备不等于对素材或 Apple SDK 作了分发授权。

公开 MIT 只覆盖自有源码。卡牌文字、卡图和标志权利、第三方 SDK 条款与分发场景分别审阅，见 [第三方声明](../THIRD_PARTY_NOTICES.md)。保留生成的署名文件和 MPL 源码获取说明，不上传用户库、测试身份、运行环境或下载的整套卡图。

当前开发交付可以完成，具体运营方的账号签名、实际上传/审核、公网部署及其容量目标在发布时执行；开发状态不自动转换成“已上架”或固定并发保证。
