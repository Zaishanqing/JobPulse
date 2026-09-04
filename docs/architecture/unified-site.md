# 统一网站整合架构

## 运行边界

统一网站只向浏览器暴露 `apps/web` 和 `apps/api`。React 前端使用 Ant Design，并通过主后端 `/api/v1` 访问所有业务能力。

```text
browser -> main-frontend -> main-backend /api/v1
                              |-- HTTP -> knowledge-graph-backend
                              |-- HTTP -> emerging-discovery
                              |-- HTTP -> trend-intelligence
                              `-- HTTP/Outbox -> matching-service
emerging-discovery -- HTTP published reference -> knowledge-graph-backend
```

Knowledge Graph、Emerging、Trend 与 Matching 都是独立进程、独立组合根和独立事务边界。当前 Compose 只通过容器网络暴露其 API，不映射宿主机端口；浏览器不持有内部服务地址或 Token。

## 登录、权限与审计

统一前端只使用主系统 `/api/v1/auth/login`、`/api/v1/auth/me` 和一个主系统 JWT。`auth/me` 返回角色对应的 permission 列表，前端同时据此限制导航和路由。

公开图谱、新兴岗位与 Evidence 查询使用 `catalog.read_published`、`emerging.read_published` 和 `evidence.read_public`。图谱构建、归一化审核、审核任务、版本管理、发现运行、候选编辑/发布和转标准岗位分别使用显式管理权限。`admin` 拥有全部权限；`reviewer` 另有 KG 归一化/审核、集成状态、采集只读和趋势审核权限；`developer` 拥有集成运维、采集管理、JD 发布、Matching 和趋势管理权限，但没有 KG 构建/版本或 Emerging 候选治理权限。后端是最终权限边界，隐藏菜单不代替服务端校验。

主后端以固定 HTTP Port 调用内部服务，并把主用户 ID、角色和请求 `trace_id` 转换为受服务账号保护的集成身份头。内部服务拒绝非服务账号伪造主系统操作者。上游错误由主后端映射为统一错误响应，上游 trace 保存在 `details.upstream_trace_id`。

## BFF 与接口

主后端 `/api/v1/portal` 是统一前端的查询聚合与知识图谱管理入口。它使用枚举化操作和固定路由映射，不提供任意 URL 代理。原有主系统和服务 `/api/v1` 接口继续保留；统一门户是新增兼容接口。

岗位详情由主系统业务画像与知识图谱已发布快照聚合。Evidence 的关系、摘录坐标来自知识图谱服务，JD 原文只从主系统读取；主后端校验坐标对应的原文片段后返回统一响应。

## 数据权威

| 数据 | 权威上下文 | 其他上下文持有内容 |
|---|---|---|
| 用户、角色、企业、招聘、简历、JD 原文和业务生命周期 | 主系统 | 不复制账户和 JD 业务状态 |
| 标准技能身份、名称、别名、分类编码 | 主系统能力目录 | 知识图谱保存通过版本化快照同步的消费副本 |
| 标准岗位规范身份与业务画像 | 主系统能力目录/招聘画像 | 知识图谱通过显式映射关联其图谱身份 |
| 图谱草稿、关系、Evidence、审核任务、不可变版本 | 知识图谱服务 | 主系统只保存映射与面向门户的组合结果 |
| 发现运行、输入/算法快照、岗位簇、Candidate Observation/Transition、Lineage 和 EMERGE v3.2 判定事实 | 新兴岗位发现服务 | 主系统保存候选治理投影、人工定义、审核、发布和晋升状态 |
| 趋势来源、采集配置、分析运行、预测与回测 | 趋势服务 | 主系统只组合面向门户的结果 |
| 匹配任务、报告、差距与学习路径 | Matching 服务 | 主系统提供经授权的岗位/CV/图谱画像并保存业务侧引用 |

主系统同步已发布 JD 前，先按稳定 ID 向知识图谱导入 `StandardSkillSnapshotV1`，再导入版本化 Published JD Fact。集成模式关闭知识图谱服务自身的标准技能写入；独立开发 Compose 才显式开启服务本地目录写入。

新兴岗位晋升采用主系统幂等流程创建标准岗位，并置为 `mapping_required`。完成主系统岗位与知识图谱岗位映射后变为 `mapped`，创建图谱构建任务后变为 `build_created`，避免跨服务双写事务和隐式同 ID 前提。

## 共享契约

仓库级 `packages/contracts/jobgraph_contracts` 只包含 DTO、值类型、枚举、字段约束、契约版本和错误码。目前承载 Extraction、Normalization、Evidence、Catalog、Discovery、Matching、RAG、Review、Publication 与离线 Bundle 等跨运行时契约。它不包含 ORM、Repository、Session、HTTP Client、状态机或服务装配。

## 数据库与部署

当前 Compose 为主系统、KG 和 Matching 分别启动 PostgreSQL；Emerging 与 Trend 共用一个 `analytics-postgres` 实例，但使用不同 database 和账号。默认链包含主后端 Extraction、Validation 和 KG Outbox worker；CV profile 另包含 Provider 与独立 CV worker。服务必须使用独立 database/schema，且不得读取其他服务的 ORM、Session、Repository 或业务表。

从 JobPulse 根目录启动候选拓扑：

```bash
docker compose --env-file infra/.env -f infra/compose/docker-compose.candidate.yml config --quiet
docker compose --env-file infra/.env -f infra/compose/docker-compose.candidate.yml up --build -d --wait
```

使用 `infra/.env.example` 时网站地址为 `http://127.0.0.1:53000`，主后端为
`http://127.0.0.1:58000`。知识图谱、新兴岗位发现、趋势和 Matching API 仅在 Compose
内部网络可访问。
