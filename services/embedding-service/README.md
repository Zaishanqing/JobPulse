# JobPulse Embedding 服务

BGE-M3 独立 Dense Embedding HTTP 服务。第一版只运行 `BAAI/bge-m3`，输出 1024 维 Dense 向量，
相似度由下游按 Cosine 计算；不提供 Sparse、Reranker 或本地替代模型。

## 接口

- `POST /v1/embeddings`：批量输入 `inputs` 与 `normalize`，返回 `vectors`、`model_id`、
  `model_revision`、`dimension`、`normalized`、`usage`、`latency_ms`；
- FlagEmbedding 返回的 dense ndarray 会在服务边界转换为严格校验的 Python 向量；
  非 dense、维度不符或非有限输出都会失败关闭；
- `GET /health`：进程存活；
- `GET /ready`：模型完成加载；
- `GET /v1/models/current`：当前固定模型谱系。

默认限制为 64 条/批、4096 字符/条、30 秒和 2 个并发推理。空文本、超长、超批量、错误维度、
NaN/Inf 均明确拒绝。服务不记录请求正文，Uvicorn access log 也已关闭。

## 启动

复制 `.env.example` 为 `.env`，将 `EMBEDDING_MODEL_REVISION` 设置为固定 revision 的 Hugging
Face 40 位 commit SHA，然后执行 `docker compose up --build`。revision 是必填且只接受 commit SHA，服务
不会跟随 `main` 或可移动 tag 漂移。model ID 固定为 `BAAI/bge-m3`，dimension 固定为 1024。
容器环境变量会先按类型解析，再严格校验 `EMBEDDING_DIMENSION=1024` 与
`EMBEDDING_NORMALIZED=true`，不接受其他值。

本服务只建设向量基础设施，不改变 matching-service 的正式匹配分数。

当前 Compose 将本服务放在可选 `semantic-demo` 与组合 `full` profile 中，并配套
编排 Qdrant、语义 Matching API 和 Matching vector worker；启动这些容器仍不等于
语义结果已经改变正式匹配分数。

## Evidence 语义候选召回链路

`embedding-service → Qdrant → semantic retrieval` 是匹配服务语义链路的正式
运行链：向量模型、维度、索引与文本派生版本由 `config/semantic-demo-contract.env`
固定；服务不可用时匹配结果明确标记 `unavailable`，不加载本地替代模型，
不把依赖失败转换为空候选。
