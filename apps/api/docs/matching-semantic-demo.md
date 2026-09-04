# 匹配语义演示

> 当前状态（2026-08-21）：本页记录语义检索契约与运行方式。JobPulse 根 Compose
> 的 `semantic-demo` profile 已包含 Embedding、Qdrant、语义 Matching API 和
> vector worker。

该演示是 CPU-only 的 BGE-M3 Dense shadow 检索链路。固定值位于仓库根目录的
`config/semantic-demo-contract.env`，并由 `semantic-demo` Compose profile 与相关
脚本加载。

契约：

- model：`BAAI/bge-m3`
- revision：`5617a9f61b028005a4858fdac845db406aefb181`
- device：`cpu`
- dimension：`1024`
- 归一化 Dense 向量，使用余弦相似度
- derivation：`semantic-fragment.v1`
- collection：`matching_fragments_bge_m3_5617a9f_v1`
- index revision：`matching_fragments_bge_m3_5617a9f_v1`
- mode：`shadow`；启用 Dense 索引与检索，关闭语义评分、sparse 与 reranker

## 模型预取

Windows PowerShell：

```powershell
python scripts/prefetch_bge_m3.py --cache-dir .cache/embedding-models
```

Shell：

```sh
python scripts/prefetch_bge_m3.py --cache-dir .cache/embedding-models
```

Compose profile 将 `/models` 持久化到 `embedding_model_cache` 命名卷。

## 实链冒烟

```powershell
python scripts/smoke_matching_semantic_live.py
```

冒烟会执行模型、Embedding、Qdrant Schema、正式 Matching Outbox 索引与 Evaluation
检查。所有断言成功后才会输出 `SEMANTIC_LIVE_SMOKE_PASSED`。

## 完整演示

```powershell
python scripts/run_matching_semantic_demo.py --prefetch
```

在路径与所需服务可用时，该命令会校验 Compose、启动链路所需语义服务、等待就绪、
运行实链冒烟，并在真实查询返回语义 Evidence 后输出
`MATCHING_SEMANTIC_DEMO_PASSED`。当前基础 Compose 默认不启用语义。

演示默认使用已有的匿名化 Snapshot ID。当运行中的主后端使用不同夹具时，覆盖
`MATCHING_DEMO_CV_ID`、`MATCHING_DEMO_POSITION_ID` 与
`MATCHING_DEMO_TENANT_REF`。冒烟不会写入 PostgreSQL；两个契约端点必须先能返回所选
CV 与岗位画像，才能开始索引。

## Evidence 语义候选召回

匹配结果中的 `semantic_shadow_evidence` 由匹配服务语义检索链路（BGE-M3 向量 +
Qdrant）生成，覆盖职责、项目、技能、场景、低词面重合、负例、信息不足与跨租户
隔离等场景；模型、维度与索引契约固定于 `config/semantic-demo-contract.env`，
并通过 `matching-evaluation-result.v1` 只读回传，不参与正式评分改写。业务场景
维度不作为 Matching/KG 正式链路的评分维度，任务上下文由 responsibilities /
project 片段承载，主系统不从 raw text 关键词猜测行业场景。
