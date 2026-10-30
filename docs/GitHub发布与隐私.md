# 源码发布与隐私

main 的 Git 跟踪树仅包含产品源码、正式文档、锁定依赖与必要测试源码。禁止提交个人数据库、凭据、卡组/对局私有状态、卡图缓存、完整运行资产、环境副本、生成的测试数据和阶段验收报告。

```sh
python3 scripts/public/audit.py --check
python3 scripts/public/export_source.py --output exports/PTCG-Lab-source.zip
python3 scripts/public/audit.py --check --artifact exports/PTCG-Lab-source.zip
```

公开导出使用允许列表，检查秘密模式、机器路径、符号链接与内容哈希，包含精确文件清单并排除 Git 历史。工作树审计同时检查 tracked 和未忽略的 untracked 文件。`--history` 可检查历史对象，但不会改写历史；整理 main 当前树不代表抹除旧提交。若分发目标只需要产品源码，使用已审计的源码 ZIP。

完整资料与引擎需合法取得，导入命令见 [README](../README.md)。首次导入只选择正式运行资产，不复制测试夹具、用户数据库或凭据。不能将下载上游卡牌 JSON 当作完整规则安装。

`release/`、`exports/`、`var/`、`data/`、`runtime/`、`.catalog/` 和本地缓存均是忽略的私有/生成目录，不属于 Git 发布树。清理分支不会删除这些实际目录或停止服务。二进制、API 镜像和离线卡图包另行生成、核验并决定分发，不随源码 commit/push 自动上传。

源码含图标、原创桌垫与轻量卡图路径索引；不含全卡图。卡牌、图片和标志的权利归属见 [第三方声明](../THIRD_PARTY_NOTICES.md)。生产镜像只复制明确批准组件，资源包不包含客户端身份或用户资料。
