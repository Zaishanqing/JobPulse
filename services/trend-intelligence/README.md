# 趋势情报服务

`services/trend-intelligence/` 拥有趋势来源与采集、Snapshot/Bundle、分析运行、岗位技能历史、
变化分析、预测、配置、回放、回测和评估数据集。主后端只提交任务、轮询结果并生成门户投影。

统一进程入口为：

```powershell
python -m app.entrypoint api
python -m app.entrypoint worker
python -m app.entrypoint migrate
```

API 健康端点是 `/health/live` 与 `/readiness`，内部接口使用
`TREND_INTELLIGENCE_INTERNAL_TOKEN`。数据库由本目录 Alembic 管理。当前 Compose 在独立
`trend_intelligence` database 上运行 API 与 worker；API entrypoint 会在启动前执行迁移。

Acquisition 子路由位于 `/internal/v1/acquisition`。接口存在不表示持续在线来源已配置；实际
Connector、凭据、来源健康和数据覆盖必须分别确认。
