# 主后端 API 总览

主后端使用 FastAPI，业务接口统一位于 `/api/v1`，运行时 OpenAPI 位于 `/docs`。
成功响应通常使用 `{"code":0,"message":"success","data":...}`；具体请求字段、
权限和错误码以运行时 OpenAPI 与路由代码为准。

## 功能分组

| 分组 | 主要能力 |
| --- | --- |
| `auth`、账户与企业 | 注册登录、角色权限、企业资料 |
| `jds`、`source-jds`、抽取任务 | JD 创建、导入、抽取、验证、审核和发布 |
| `resumes`、CV ingestion | 简历管理、抽取状态和已验证画像 |
| `skills`、`positions` | 权威技能目录、岗位目录和岗位画像 |
| `knowledge-graph`、`portal` | 图谱同步、构建、审核、版本和前端聚合读取 |
| `emerging-positions`、discovery | 发现运行、候选生命周期、候选治理、岗位晋升 |
| `trend-analysis`、`trend-reports`、`trend-runs` | 趋势报告、能力演化和预测 |
| `matches`、`learning-paths` | 个人/企业授权匹配、差距和学习路径 |
| `review-tasks`、`feedback`、`evaluation` | 人工审核、反馈和质量评估 |
| `evidence`、`rag`、`innovation/insights` | 证据查询、受约束问答与统一 Insight Card |
| acquisition | 采集运行、快照、Bundle 与导入任务管理 |
| `tasks`、`system`、observability | 任务状态、健康、就绪和运行指标 |

JD 解析请求必须提交 `extraction_mode=llm|rule`。抽取详情返回 mode、provider、model 或
algorithm version 及各 Contract 版本；rule 结果审核前不能发布。CV 正式入口分为文件上传
LLM 抽取和命名 Demo Snapshot，不再提供 CV Extraction V1 单条或 batch 正式接口。

统一前端只调用主后端。主后端对 Knowledge Graph、Emerging Discovery、Trend
Intelligence 和 Matching 使用固定内部客户端，不暴露任意代理。跨模块方向见
[系统接口地图](../../../docs/architecture/system-interface-map.md)。

发现运行请求的 `time_windows` 表示算法上下文，`current_observation_window_id` 表示本次
正式观察窗。多个观察窗对应多个独立 Run/UoW；候选轨迹和 Lineage 由发现服务 PostgreSQL
历史生成。主系统只代理运行、查询与治理，不在 BFF 内重算生命周期。

### Evolution Event Portal 代理

`/portal/admin/knowledge-graph/positions/{position_id}/evolution-events` 是主系统
BFF 的 Evolution Event 列表入口（权限 `catalog.read_published`，reviewer / developer /
admin 可用）：

- 传 `from_version_id` + `to_version_id`（可选 `event_type`）时，透传 Knowledge Graph
  服务对应接口，返回单版本对事件；
- 不传版本参数时，BFF 基于既有 versions 读取，按相邻 GraphVersion 对聚合，返回
  `{position_id, versions[], version_pairs[], events[], count, event_type}`，供前端
  演化事件时间线一次加载完整跨版本数据。

`/portal/admin/knowledge-graph/positions/{position_id}/evolution-events/{event_id}`
返回单个事件详情（event_id 编码 from/to 版本对）。前端主时间线只消费 list，
list 已包含完整 Event（evidence / reason / metrics），不逐个调用 detail。
