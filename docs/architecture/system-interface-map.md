# 系统接口地图

本页只说明跨模块方向；字段、鉴权和错误码以各服务运行时 OpenAPI 为准。

| 调用方 | 被调用方 | 通道 | 用途 |
| --- | --- | --- | --- |
| 统一前端 | 主后端 | HTTP `/api/v1` | 登录、业务工作台和管理操作 |
| 主后端 | Knowledge Graph | 内部 HTTP | 目录同步、JD Fact、图谱构建/审核/查询 |
| 主后端 | Emerging Discovery | 内部 HTTP | 发现 Run、对比、候选轨迹、Lineage 与治理投影 |
| 主后端 | Trend Intelligence | 内部 HTTP | 趋势运行、报告、预测和回测 |
| 主后端 | Matching Service | 内部 HTTP + Outbox | 画像读取、匹配任务、报告和学习路径 |
| 主后端 | JD Extraction | 内部 HTTP，可选 `model-extraction` profile | 默认 Extraction worker 处理 `rule`；profile 启用后可处理显式 `llm` 任务 |
| 主后端 | CV Extraction | 内部 HTTP，可选 `cv-extraction` profile | profile 同时启动 Provider 与独立 CV worker；需配合 `CV_EXTRACTION_ENABLED=true` |
| 爬虫/Extraction 产物 | 主后端 | 离线审核包 | 批量导入真实 JD |
| Matching Service | 主后端 | 内部 HTTP Contract | 获取获授权的 CV、岗位和图谱画像 |
| Emerging Discovery | Knowledge Graph | 已发布引用/稳定标识 | 关联已发布岗位、技能与 Evidence，不跨库写入 |

服务间禁止共享 ORM、Repository、Session 或业务表。主后端是统一前端的固定聚合入口，
不提供任意 URL 代理。

本表描述代码中的逻辑调用方向。`infra/compose/docker-compose.candidate.yml` 的默认链
包含主后端 Extraction/Validation/KG Outbox worker；CV 和语义异步链在各自 profile
中以 Provider/存储/worker 成套编排。分母以 `infra/compose/production-parity.yaml` 为准。

发现服务的 `time_windows` 是算法上下文；正式请求用
`current_observation_window_id` 明确本次 Run 的观察窗。多个观察窗必须形成多个独立事务，
后继 Run 从服务自身 PostgreSQL 读取历史 Candidate/Lineage，不能由调用方在进程内拼接状态。
主后端以 JD 发布快照的 `publish_date` 划分固定三日窗并逐窗调用；每次只发送当前观察窗
快照，不得使用采集时间、入库时间或当前日期替代 JD 发布日期，也不得把未来窗口样本送入
历史 Run。无受支持岗位簇的窗口仍提交为空结果成功 Run，以保留完整生命周期时间轴。
相同 `request_id` 与 payload fingerprint 幂等返回既有 Run；同 ID 不同 payload 返回冲突。

相关文档：

- [Knowledge Graph 集成契约](knowledge-graph-contract.md)
- [JD 离线 Bundle 导入](../user-guide/data-import.md)
- [主后端 API](../../apps/api/docs/api-overview.md)
- [Matching API](../../services/matching-service/README.md)
