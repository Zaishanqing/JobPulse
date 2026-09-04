# Evidence RAG BFF 契约

## 目标

主系统 BFF 提供正式的 Evidence RAG Endpoint：服务端装配权限和版本，调用真实
Embedding / Qdrant 检索，再调用真实 DeepSeek 生成回答，只返回本次检索得到的
Evidence Reference。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/rag/evidence` | BFF 查询 |
| POST | `/api/v1/rag/evidence/index` | 管理员索引正式 Evidence |
| POST | `/api/v1/rag/evidence/invalidate` | 管理员失效索引（保留点） |
| DELETE | `/api/v1/rag/evidence` | 管理员删除索引点 |

查询请求：

```json
{
  "contract_version": "evidence-rag-query.v1",
  "business_object": {
    "object_type": "cv_profile",
    "object_id": "profile-1",
    "object_version": "v1"
  },
  "query_text": "是否具备 RAG 项目经验",
  "evidence_types": ["cv_evidence"],
  "version_scope": "single_object",
  "graph_version_id": 7
}
```

单对象请求的版本字段 `graph_version_id` / `graph_version` /
`business_version` 必须恰好提供一个。多对象请求使用
`version_scope: "multi_object"`，并在 `business_objects` 中为每个对象提供自己的
`object_version`；多对象请求不得再提供顶层版本字段。例如：

```json
{
  "business_object": {
    "object_type": "standard_position",
    "object_id": "position-a",
    "object_version": "27"
  },
  "business_objects": [
    {"object_type": "standard_position", "object_id": "position-a", "object_version": "27"},
    {"object_type": "standard_position", "object_id": "position-b", "object_version": "40"}
  ],
  "version_scope": "multi_object"
}
```

多对象中的对象类型必须一致、对象 ID 必须唯一且包含主对象；当前岗位比较将
`object_version` 作为该岗位的 `graph_version_id` 使用。`evidence_types` 为 `all`
时必须单独使用。请求体允许携带与前端契约一致的
`permission`，但 BFF 会忽略其中的 `tenant_ref` / `permission_scope` /
`assembled_by`，全部从登录态重新装配。

响应 `data` 遵循 `evidence-rag-response.v1`：

- `answered`：必须同时有 `answer` 和非空 `references`；
- `insufficient_evidence`：没有 `answer` / `references`，`error.code` 为
  `EVIDENCE_NOT_FOUND` 或 `EVIDENCE_INSUFFICIENT`；
- `failed`：没有 `answer` / `references`，`error.code` 为稳定错误码。

`references` 中每条都带 `tenant_ref`、`permission_scope` 和版本血缘。查询可命中
两类证据：

1. 查询者自身 `tenant_ref` / `permission_scope` 下的证据；
2. `jobgraph-platform-public` / `platform:public` 的平台公开证据，对任意已登录
   账号只读可见。

单对象中跨租户、跨权限或版本不一致的 Evidence 会被拒绝；多对象允许不同岗位使用
不同 GraphVersion，但每条 Reference 必须匹配其 `business_object_id` 对应的请求版本，
且仍受 tenant / permission / visible-evidence 约束。

## 权限与租户

服务端按登录角色派生：

| 角色 | tenant_ref | permission_scope |
|---|---|---|
| admin / developer / reviewer | `jobgraph-platform-public` | `platform:public` |
| personal_user | `personal:{account_id}` | `personal:{account_id}` |
| enterprise_user | 所属企业 ID | `enterprise:{enterprise_id}` |

任意已登录账号还额外可见平台公开证据 `jobgraph-platform-public` /
`platform:public`（只读）；公开证据的引用保留其原始 tenant/scope，不伪装成查询者
自己的 scope。

索引接口仅限 admin / developer，索引记录必须显式声明 `tenant_ref` 和
`permission_scope`。

索引记录可以同时携带 `graph_version_id` 和 `graph_version`，二者是同一图谱版本的
两种表达；查询侧仍只要求传入其中一种。`business_version` 用于 CV snapshot 等
非图谱版本，不能与 graph 版本字段同时出现。

## 配置

默认关闭，保持基础 Compose 行为不变：

```text
RAG_EVIDENCE_ENABLED=false
RAG_EVIDENCE_EMBEDDING_URL=http://embedding-service:8000
RAG_EVIDENCE_EMBEDDING_MODEL=BAAI/bge-m3
RAG_EVIDENCE_EMBEDDING_REVISION=5617a9f61b028005a4858fdac845db406aefb181
RAG_EVIDENCE_EMBEDDING_DIMENSION=1024
RAG_EVIDENCE_QDRANT_URL=http://qdrant:6333
RAG_EVIDENCE_COLLECTION=evidence_rag_bge_m3_5617a9f_v1
RAG_EVIDENCE_LLM_MODEL=deepseek-v4-flash
RAG_EVIDENCE_LLM_ALGORITHM_VERSION=deepseek-evidence-rag-answer.v1
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

未启用时查询返回 `failed` / `RAG_EVIDENCE_DISABLED`；Embedding、Qdrant 或
DeepSeek 不可用时返回 `failed` 和对应 `EMBEDDING_*` / `QDRANT_*` / `LLM_*`
错误码，不会退回本地规则或内存检索。
