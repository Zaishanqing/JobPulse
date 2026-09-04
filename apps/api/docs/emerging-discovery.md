# EMERGE v3.2 新兴岗位发现说明

本文描述当前新兴岗位发现链路：运行编排、算法判定、接口与治理衔接。正式判定统一
使用 **EMERGE v3.2**（两阶段解释 + 六类时序证据 + 硬门控），不再使用任何加权评分
作为产品口径。

## 正式判定链

```text
Web 前端（运行 EMERGE v3.2）
  -> JobPulse BFF（/api/v1/position-clusters/tasks，固定算法与输入数据）
  -> emerging-discovery（discovery.v2 编排、任务状态、结果持久化）
  -> knowledge-graph 内部接口 POST /api/v1/integrations/emergence/v3.2/evaluate
       （唯一策略实现 EmergenceV32Policy）
  -> BGE-M3（BAAI/bge-m3，revision 5617a9f61b028005a4858fdac845db406aefb181）
       + 版本化成熟职业参考库
```

浏览器不能直接调用 knowledge-graph。EMERGE v3.2 策略只在 knowledge-graph 内实现，
不得在多个服务复制策略，也不得在 v3.2 不可用时回退到任何旧算法或词法相似度。

## 运行协议

发现运行使用服务端配置的已验证输入数据。运行时适配器
`apps/api/app/infrastructure/discovery_datasets.py` 会校验数据版本、哈希与窗口
完整性；数据缺失或校验失败时返回 `DISCOVERY_DATASET_NOT_READY`。输入成员使用
稳定虚拟 ID，只投影清单中已有的职责与技能，不创建、不调度、不重跑抽取任务。

`discovery.v2` 要求每个请求携带三个历史窗口，禁止使用未来窗口数据；`request_id`
由算法、窗口、事实身份与配置哈希确定性生成，相同输入幂等复用已落地的 Run。每个
观察窗对应一次独立事务；后继 Run 从 PostgreSQL Repository 读取前序 Cluster、
Candidate 与 Lineage，BFF 不在进程内重算。

## EMERGE v3.2 算法语义

### Stage 1：相对成熟职业的结构解释

从版本化成熟职业参考库检索 top-k 参考，组合相似度为：

```text
0.40 * title_similarity + 0.35 * skill_similarity + 0.25 * responsibility_similarity
```

输出关系包括：`same_or_not_novel`、`renaming`、`tool_shift`、`specialization`、
`hybridization`、`unexplained_structural_novelty`、`insufficient_evidence`。
结构门允许专业化和混合化通过；“无法解释的结构新颖性”只有在参考职业不是 `OTHER`
兜底类、参考核心技能非空、存在领域重叠、继承核心成立且解释组合分低于阈值时
才可靠通过。

### Stage 2：以职业簇为单位的六类时序证据

JD 是 observation，职业簇才是最终判定单位。六类证据层：

1. re-observation persistence（同一 JD / 同一内容哈希被重复抓取）；
2. independent posting persistence（不同独立招聘发布身份）；
3. enterprise diffusion；
4. source diffusion；
5. structural evolution（内容跨窗口变化）；
6. market growth（每窗口独立发布/企业变化）。

最终不是加权求和，而是硬门控：

- 独立发布门：`independent_postings >= 2` 且 `distinct_dates >= 2`；
- 扩散门：`enterprises >= 2`，或同时满足 `sources >= 2` 与
  `independent_postings >= 2`；
- 时序门：独立发布持续、结构演化或市场增长至少一项可用；
- 最终状态：`insufficient` / `not_emerging` / `weak` / `emerging`；
- `emerging` 的 `evidence_level=short_window`，不得包装成长期市场定论。

同一 `source_record_id` 或相同内容哈希的多次抓取不能独立触发 `emerging`；
`re_observation_only` 簇永远不是正向新兴信号。

## API

主系统（管理员，统一前端入口）：

- `POST /api/v1/position-clusters/tasks`：请求体 `algorithm` 固定为 `emerge_v3_2`，
  `dataset_id` 由服务端配置固定；
- `GET /api/v1/portal/admin/discovery-runs`
- `GET /api/v1/position-clusters`、`GET /api/v1/position-clusters/{cluster_id}`、
  `GET /api/v1/position-clusters/{cluster_id}/jds`
- `GET /api/v1/portal/admin/discovery-candidates`、
  `GET /api/v1/portal/admin/discovery-candidates/{candidate_id}`、
  `GET /api/v1/portal/admin/discovery-candidates/{candidate_id}/trajectory`
- `POST /api/v1/portal/admin/discovery-candidates/{candidate_id}/enter-governance`
- `POST /api/v1/emerging-positions/from-cluster/{cluster_id}` 及后续审核/发布接口

发现服务（内部 Bearer Token）：

- `POST /api/v1/discovery-runs`、`GET /api/v1/discovery-runs/{run_id}`
- `GET /api/v1/discovery-runs/by-request-id/{request_id}`
- `GET /api/v1/discovery-runs/{run_id}/lineage-graph`
- `GET /api/v1/clusters/{cluster_id}/trajectory`、`/memberships`
- `GET /api/v1/candidates`、`/candidates/{candidate_id}`、`/trajectory`

knowledge-graph 内部接口（内部服务账号，浏览器不可访问）：

- `POST /api/v1/integrations/emergence/v3.2/evaluate`

## 候选生命周期与治理衔接

候选生命周期状态链为
`weak_signal → incubating → emerging_candidate → stable_emerging_role`，另有
`dead / noise`。该状态与 EMERGE v3.2 的 `emerging` 判定是不同概念，页面分开展示，
不能互相替代。

进入治理必须调用服务端门禁
`POST /api/v1/portal/admin/discovery-candidates/{candidate_id}/enter-governance`：

1. 候选不存在 → 404；
2. `status != stable_emerging_role` → 409 `candidate_lifecycle_gate_rejected`；
3. `current_cluster_id` 为空 → 409 `candidate_lifecycle_cluster_missing`；
4. 当前 Cluster 未投影到主系统岗位簇 → 409 `candidate_lifecycle_cluster_not_projected`；
5. 需同时具备 `emerging.discovery.manage` 与 `emerging.candidate.manage` 权限；
6. 不自动 review / publish / promote。

## 边界

- 不修改输入数据、窗口、标签、阈值或权重；
- 不为缺失时间字段的记录使用导入时间或抓取时间替代；
- 不允许静默回退到任何旧版加权评分或词法相似度算法；
- 不重新抽取已审核输入样本，也不把旧审计批次当作正式输入；
- 前端运行请求以服务端实际采用的算法和数据集版本为准，不由浏览器字符串决定。
