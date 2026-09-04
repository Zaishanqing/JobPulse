# 后端边界

主后端和独立能力服务遵循相同的依赖方向：

```text
API / Infrastructure -> Application -> Domain
                       Application -> Ports <- Infrastructure
```

Domain 保存业务规则，Application 编排用例与事务，Ports 定义外部能力，Infrastructure
实现数据库和 HTTP 适配器，API 只处理协议、身份和 DTO 映射。

## 主后端上下文

`app/contexts/` 按业务拆分 access、acquisition、catalog、cv_ingestion、data_validation、
discovery、emerging_positions、evaluation、evidence_independence、evidence_rag、
extraction_tasks、governance_feedback、insight_cards、jd_lifecycle、knowledge_graph、
market_intelligence、matching_learning、platform、review_value_ranking、source_jds、
talent_acquisition 和 tasks。组合根位于
`app/bootstrap/container.py`，数据库结构只由 Alembic 管理。
增量迁移遇到历史 `create_all` 已提前创建的同名表时，必须校验必需列后继续回填；
结构不兼容则明确失败，禁止静默跳过。

## 独立服务

- `knowledge-graph`：图谱事实、Evidence、审核和不可变版本。
- `emerging-discovery`：发现运行、聚类、EMERGE v3.2 判定、Candidate Observation/Transition 与 Lineage。
- `trend-intelligence`：多源趋势、算法配置、预测和回测。
- `matching-service`：画像校验、匹配、差距和学习路径。
- `embedding-service`：固定模型的 Dense Embedding 推理。

每个服务拥有自己的组合根、数据库、迁移和事务。主后端只通过版本化 HTTP DTO、
`jobgraph_contracts` 或 Outbox 调用服务，不导入服务 ORM、Repository 或内部实体。

## 数据与发布规则

- 一个写用例由 Application 层控制一次事务提交。
- 自动生成内容必须保留 Evidence、血缘和人工审核入口。
- 权威目录、已发布 JD 与图谱版本不可原地修改。
- 接口存在不等于外部 Provider 已完成生产验证。
- JD 抽取的 `HttpJDExtractionProvider` 与 `RuleBasedJDExtractionProvider` 由任务中的
  `extraction_mode=llm|rule` 显式选择，不存在自动 fallback。规则输出和未确认 CV
  Snapshot 受发布门禁约束。
- CV 文件上传只调用 LLM 服务；预置 Demo Snapshot 以独立数据集身份进入 Snapshot、
  Outbox 和 Matching，不作为文件抽取 Provider。
- Emerging 每个当前观察窗对应一个 Application UoW；后继 Run 通过 Repository 读取已提交
  的前序 Candidate/Lineage。同 request 幂等复用，乱序输入与中途异常不得留下半状态。

这些边界由主后端及各服务的架构、契约和迁移测试约束。

部署边界另看 `infra/compose/docker-compose.candidate.yml`：默认编排主 API、
Extraction、Validation 和 KG Outbox worker；`cv-extraction` profile 将 CV Provider 与独立
CV worker 作为同一运行链启动。
