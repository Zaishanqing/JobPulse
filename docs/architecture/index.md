# JobPulse 当前架构文档

运行时事实以当前代码、路由注册、Alembic 和
`infra/compose/docker-compose.candidate.yml` 为准。

## 核心入口

- [系统总览](system-overview.md)
- [系统接口地图](system-interface-map.md)
- [统一网站整合架构](unified-site.md)
- [Knowledge Graph 集成契约](knowledge-graph-contract.md)
- [Knowledge Graph 运维](knowledge-graph-operations.md)
- [部署拓扑与运行边界](../deployment/deployment.md)
- [主后端架构](../../apps/api/docs/backend-architecture.md)
- [主后端 API 总览](../../apps/api/docs/api-overview.md)
- [技术说明索引](../technical/README.md)

## 当前架构边界

- `apps/web` 只访问 `apps/api` 的 `/api/v1`。
- 主后端是账号、JD/CV、招聘和治理的所有者，也是跨服务 BFF。
- Knowledge Graph、Emerging、Trend 和 Matching 是独立进程；有状态服务拥有独立数据库、迁移与事务。
- `packages/contracts` 只保存跨运行时契约，不保存业务实现。
- 当前 Compose 默认编排核心在线异步拓扑；`full` profile 进一步启用 JD/CV Extraction、
  Qdrant/语义 vector worker 和 crawler。
- profile 中出现表示角色已进入统一 Compose 拓扑，不等于外部模型、站点凭据或离线 Bundle
  已完成端到端业务联调。

## 数据入口

- [JD 离线 Bundle 导入](../user-guide/data-import.md)
- [真实数据审核与发布链](../../apps/api/docs/real-data-pipeline.md)
- [CV Extraction 架构](../../services/cv-extraction/docs/architecture.md)
- [JD Extraction](../../services/jd-extraction/README.md)
- [Knowledge Graph](../../services/knowledge-graph/README.md)
- [Emerging Discovery](../../services/emerging-discovery/README.md)
- [Trend Intelligence](../../services/trend-intelligence/README.md)
- [Matching Service](../../services/matching-service/README.md)
