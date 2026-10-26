# 在线容量验收

P6-A 已提供固定服务/SQL 对照负载，后续调度/推送和 P7 容量矩阵尚未完成。

使用独立 PostgreSQL 测试容器和新数据库。`PTCG_V2_TEST_DSN_FILE` 指向私有 DSN 文件，数据库名必须为 ptcg_p4a、host 必须为 127.0.0.1；测试为每个用例创建独立数据库并在结束时删除，不能使用用户数据库。

```sh
# 在已安装固定 API 依赖的测试 Python 环境运行；报告目录为新的 var/p4d 子目录。
python -m pytest tests/online_v2/test_p6_baseline.py -q
# 完整证据还需要 PTCG_V2_REAL_INPUTS_FILE、PTCG_V2_TEST_IMAGE、
# PTCG_V2_TEST_DB_CONTAINER、PTCG_V2_REPORT_DIRECTORY（均显式配置）。
python -m pytest tests/online_v2 -q --junitxml=var/p4d/your-run/results.xml
python scripts/migration/p4d_report.py var/p4d/your-run
# 先审阅并更新 releases/p4d-acceptance.json，再生成 P6-A 聚合报告：
python scripts/migration/p6a_report.py var/p4d/your-run
```

固定两种模式：有界 NullConnectionPool 与 ConnectionPool；每种 3 次，每次 5 局，每局 20 次空闲轮询、4 条命令、4 次同请求重试、双视角回放和隐私核对。没有网络、动画或真实规则计算的容量结论。真实规则另测 5 步冷恢复，结果也不是长局/1000 步 SLA。详细配置、计时语义、回退与后续阶段见 [P6 实施记录](../../docs/P6多局调度与性能实施记录.md)。
