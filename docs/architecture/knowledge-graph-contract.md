# 知识图谱服务集成契约

## 服务与数据所有权

主系统是用户、企业、JD 原文与生命周期、V2 抽取/归一化事实、标准技能身份、标准岗位身份/业务画像和跨系统映射的事实源。
知识图谱服务保存能力目录消费快照、已发布 JD 结构化事实副本、Evidence、质量评估、构建运行、审核、发布版本和 KG 审计。双方只通过 HTTP JSON 交互；禁止跨库 Session、外键和查询。

SQLite 仅用于本服务的本地开发与快速单测；统一部署中主系统与 KG 均使用 PostgreSQL，PostgreSQL 方言、迁移 head、写事务和目录版本由 readiness 校验。

## 主系统公开接口

原集成路径继续使用 `/api/v1`。统一前端新增使用 `/api/v1/portal` BFF；成功仍为 `{code,message,data,trace_id}`，上游 trace 位于 `details.upstream_trace_id`。

| 方法与路径 | 主系统权限 | 说明 |
| --- | --- | --- |
| `GET /integrations/knowledge-graph/status` | 已登录 | readiness 与上游 trace |
| `PUT /integrations/knowledge-graph/mappings/{type}/{main_id}` | admin/developer | 显式编辑 position/skill 映射并验证 KG ID |
| `POST /integrations/knowledge-graph/jds/{id}/sync` | admin/developer | 阶段化同步 V2 事实 |
| `GET /integrations/knowledge-graph/jds/{id}/status` | 已登录 | payload hash、阶段、错误和 trace |
| `POST /integrations/knowledge-graph/positions/{id}/build` | admin/developer | 发起真实 KG build |
| `GET /integrations/knowledge-graph/positions/{id}/build-runs` | admin/developer | 构建运行列表 |
| `GET /integrations/knowledge-graph/build-runs/{id}` | admin/developer | 构建运行详情 |
| `GET /integrations/knowledge-graph/positions/{id}/graph` | 已登录 | 只读正式图谱 |
| `GET /integrations/knowledge-graph/positions/{id}/versions` | 已登录 | 已发布版本 |
| `GET /integrations/knowledge-graph/relations/{id}/evidence` | reviewer/admin/developer | 关系证据链 |
| `GET /portal/positions*` | 已登录 | 已发布岗位、图谱和聚合详情 |
| `GET /portal/evidence/*` | 已登录 | KG 坐标与主 JD 原文组合 |
| `GET /portal/admin/demo-tasks` | `integration.status.view` | JD/CV Extraction、Trend、Discovery、Matching 演示任务统一投影 |
| `/portal/admin/knowledge-graph/*` | admin + 对应 permission | 构建、草稿、归一化、审核、发布与版本操作 |

普通用户无法同步或构建；reviewer 无发布代理。发布、回滚仍只由 KG 自身 API、RBAC 与门禁执行。

Portal 岗位图谱、Matching 和 Trend 均读取 KG Published 数据。主系统调用
`position-profile.v2` 时显式传递 `view=published`，并以 KG `graph_version_id` 的字符串形式作为
跨流程统一 `graph_version`；Portal Published Graph Snapshot 同样返回该字段。服务间只通过
HTTP Contract 传递画像和版本，不跨服务数据库读取。

演示任务接口只投影当前演示链路，固定返回 `task_id`、`task_type`、`object_type`、
`object_id`、`service`、`status`、`progress`、`error`、`result_reference`、`created_at`、
`updated_at`。状态限定为 `pending/running/succeeded/failed/cancelled`，支持按任务类型、状态和
对象 ID 过滤；结果引用只使用现存 API 路径或服务资源标识。

## KG 集成端点

在不改变原公开接口语义的前提下增加：

- `PUT /api/v1/integrations/jds/{document_id}`：幂等创建/更新集成文档；原
  `POST /api/v1/jds` 重复 ID 仍为 409。
- `PUT /api/v1/integrations/catalog/skills/{skill_id}`：按 `StandardSkillSnapshotV1` 幂等导入主能力目录技能快照；路径 ID 必须与 DTO ID 一致。
- `GET /api/v1/integrations/positions`：developer/admin 可读取含未发布岗位的映射目录；
  原公开 `/positions` 仍只展示已发布岗位。
- `POST /api/v1/jds/{document_id}/normalized-result/import`：严格 V2 归一化导入。
- `GET /api/v1/graph/build-runs/{run_id}`：构建运行详情。

KG OpenAPI 已重新导出到 `services/knowledge-graph/schemas/openapi.json`。

## field_hierarchy_v2 适配

双方历史 V2 叶子结构不同，因此主系统使用显式 mapper，不直接透传同名 JSON：

- 主 `SourcedText.value` -> KG `SourcedText.text`；
- 主职责 `action` -> KG职责 `text`；
- typed company/employment fact -> KG fact DTO；
- 主扁平 normalized skill -> 根据 extraction skill item 精确关联 KG `requirement_id`；
- 主系统技能必须先存在分类完整的能力目录记录，并先同步技能快照；历史已确认映射继续使用原 KG ID，否则使用稳定主技能 ID；
- 集成部署禁止在 KG 归一化流程创建标准技能，未映射项只能关联现有能力目录技能或驳回；
- `schema_version` 必须为 `v2`，未知版本在主边界返回 422。

Evidence 的 `source_id/quote/start/end/alignment/occurrence_index` 全部保留。KG align 阶段重新
校验 `raw_text[start:end] == quote`，不使用 fuzzy match。

## ID 映射与幂等

主表 `knowledge_graph_entity_mappings` 的 `(entity_type, main_system_id)` 唯一，支持
`document/position/skill/graph_version`，保存 KG ID、SHA-256 payload hash、阶段、错误、trace
和时间戳。没有跨库外键。

岗位映射不会依靠主/KG 表结构或名称猜测。新晋升标准岗位先进入 `mapping_required`，确认映射后进入 `mapped`，成功发起构建后进入 `build_created`。

稳定 hash 对排序键、紧凑 JSON 计算。完整成功且 hash 未变化时不重复写 KG；内容变化后按
`document_imported -> extraction_imported -> extraction_aligned -> normalization_imported ->
quality_assessed/synced` 重试。失败保留最后阶段，已正确写入的数据不删除。

## 认证、审计与 trace

主系统先执行自身 JWT/RBAC，再以 `integration_developer` 服务身份调用 KG。服务 Token 只在
后端内存缓存，401 后刷新一次。主用户 ID/角色通过 `X-Main-User-Id`、
`X-Main-User-Role` 发送；只有 KG 当前 JWT 用户为配置的服务账号时才采信为审计上下文。
KG AuditLog 的 actor 仍是服务账号，同时保存受控 main actor context。

主 `X-Request-ID` 同时发送为 KG `X-Trace-Id`/`X-Request-ID`。主响应保留自己的 trace，
并显式返回 `upstream_trace_id`。已验证示例：`req_verify_<timestamp>`。

## 错误映射

401 会先刷新一次 Token；403/404/409/422 保持 HTTP 语义与 KG details；KG 5xx、连接失败和
超时统一映射为主 503，并返回主 trace、upstream trace 和 error_code。仅 GET/readiness
有限重试；写请求不盲重试。不存在静态业务 fallback。

## JobPulse 部署说明

上述是代码与 HTTP 契约。`infra/compose/docker-compose.candidate.yml` 已编排 KG migrate、
bootstrap、API、build worker 与主后端 KG Outbox worker；Outbox worker 在主 API、KG 和
Matching readiness 后启动并自动投递受支持事件。
