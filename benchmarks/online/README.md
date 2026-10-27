# 在线容量验收

P6-A 已提供固定服务/SQL 对照负载，P6-B 增加集中推送的空闲订阅微基线；P6-C 增加确定性调度与并发 HTTP 验证；常驻 worker 和 P7 容量矩阵尚未完成。

使用独立 PostgreSQL 测试容器和新数据库。`PTCG_V2_TEST_DSN_FILE` 指向私有 DSN 文件，数据库名必须为 ptcg_p4a、host 必须为 127.0.0.1；测试为每个用例创建独立数据库并在结束时删除，不能使用用户数据库。

```sh
# 在已安装固定 API 依赖的测试 Python 环境运行；报告目录为新的 var/p4d 子目录。
python -m pytest tests/online_v2/test_p6_baseline.py -q
# 完整证据还需要 PTCG_V2_REAL_INPUTS_FILE、PTCG_V2_TEST_IMAGE、
# PTCG_V2_TEST_DB_CONTAINER、PTCG_V2_REPORT_DIRECTORY（均显式配置）。
mkdir -p var/p4d/your-run
python -m pytest tests/online_v2 -q --junitxml=var/p4d/your-run/results.xml
python scripts/migration/p4d_report.py var/p4d/your-run
# 先审阅并更新 releases/p4d-acceptance.json，再生成 P6-A 聚合报告：
python scripts/migration/p6a_report.py var/p4d/your-run
```

固定两种模式：有界 NullConnectionPool 与 ConnectionPool；每种 3 次，每次 5 局，每局 20 次空闲轮询、4 条命令、4 次同请求重试、双视角回放和隐私核对。没有网络、动画或真实规则计算的容量结论。真实规则另测 5 步冷恢复，结果也不是长局/1000 步 SLA。详细配置、计时语义、回退与后续阶段见 [P6 实施记录](../../docs/P6多局调度与性能实施记录.md)。

P6-B 的 `test_stream_idle_sql_is_constant_and_private_frames_are_not_loaded` 测量同一场合成对局的 1／8／32 条订阅各 10 轮空闲检查；不是多场活跃对局压测。完整验收后先生成并更新 P4-D 报告，再运行 `python scripts/migration/p6b_report.py var/p4d/your-run`。P6-A 报告作为历史检查点保留，不能用新源码摘要覆盖旧结果。

P6-C 的 `test_scheduler_fifo_round_robin_and_duplicate_merge` 用显式屏障控制执行顺序，验证 3 组、7 个不同任务和 3 次合并重试；并发 HTTP 测试验证排队后的撤销／接管／认输、回执与不同调用方身份。完整服务验收后生成 `python scripts/migration/p6c_report.py var/p4d/your-run`；旧 P6-A/B 报告不覆盖。这些是正确性与有界性证明，不输出吞吐、容量或延迟 SLA。
