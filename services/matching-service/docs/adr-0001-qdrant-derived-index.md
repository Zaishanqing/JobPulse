# ADR-0001：Qdrant 仅作为派生向量索引

- 状态：Accepted
- 日期：2026-07-29
- 范围：Matching Service B1 Vector Contract

## 决策

Qdrant 或任何未来替代向量存储只允许作为可重建的派生索引，不是候选人、岗位、画像、
Evidence、validated snapshot、匹配结果或版本链路的权威数据库。

权威事实仍保存在现有主框架数据库及 Matching Service 的持久化边界。向量记录必须携带
可追溯的来源、画像 fingerprint、Embedding model/revision 和 tenant_ref；point ID 由这些
稳定输入确定性生成。模型或 revision 改变时创建新 point ID，不覆盖旧版本身份。

所有写入和查询必须显式携带 tenant_ref。查询只返回同一 tenant 的 active 记录；向量
payload 在写入前执行 PII 门禁。索引丢失、过期或不可用时可以从权威数据重建，但不得因此
启用主框架本地匹配或任何评分 fallback。

## B1 边界

B1 只提供技术中立 Contract、Ports 和进程内 Fake adapters。它不接入真实 Embedding、
Qdrant 或语义评分，也不改变正式匹配分数。

## B4 实现

`QdrantVectorStoreAdapter` 通过 Qdrant REST API 管理 `matching_fragments_v1`。Collection 固定使用
unnamed Dense vector、`dimension=1024`、`distance=Cosine`；启动初始化会创建并验证 tenant、实体、
fragment、Profile fingerprint、Embedding model/revision 和 active payload 索引。现有 Collection
维度、距离或 payload schema 不一致时直接以 `QDRANT_SCHEMA_MISMATCH` 拒绝，不修改为兼容配置。

upsert 使用 B1 确定性 UUID point ID，重复写不会产生重复 point。search 强制包含 tenant、active、
Embedding model/revision 过滤，并支持 Profile、fragment type 和 entity ID 过滤。deactivate/delete
同样以 tenant 与 point ID 联合选择，不能仅凭 point ID 跨 tenant 操作。超时、网络、429、5xx 只做
有界重试；最终失败返回稳定错误，不切换内存向量库。Qdrant payload 只保存回构
`VectorSearchHit` 必需的已去 PII Fragment、索引字段和调用方显式安全 metadata。

B4 仍不把 Qdrant 接入正式匹配评分；Compose 只提供真实基础设施和独立 Embedding 服务，后续索引
Worker 才能消费本 Port。


