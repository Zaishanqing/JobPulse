# 真实数据审核与发布链路

## 岗位目录 v3 受控切换

主系统是 `position-taxonomy.v3.0.0` 唯一权威源。Extraction 只输出岗位代码，
主系统校验版本、active 状态和分类状态后绑定内部 UUID；发布使用
`jd-publication-snapshot.v3` / `published-jd-fact.v3`，同时携带技能和岗位目录快照。
KG 的服务内岗位身份等于 `position_code`，不按名称猜测；只有
`sample_support_status=sufficient` 的岗位可生成正式画像。Matching 仅在 JD/CV
岗位身份、目录版本、职级和领导范围兼容时允许强匹配。

历史迁移先生成独立 JD/CV v3 run。JD 迁移不是覆盖旧
`JDParseResult` 或 `JDPublication`：`scripts/apply_position_v3_to_existing_jds.py`
为每条历史事实创建新的 JD 版本、v3 解析草稿、人工审核任务和 Validation 血缘。
审核人员批准且 Validation 通过后，
`scripts/publish_position_v3_migrated_jds.py` 才创建新的不可变 v3 发布快照，并在
发布成功后废弃旧 JD 版本。随后导出主系统岗位目录、先导入 KG 目录，再导出和导入
KG release、重建 sufficient 岗位图谱并触发 Matching 重索引。v2 产物只读保存，
不参与运行时双读。

## 数据流

1. Extraction 保存完整 JD、Evidence 和归一化结果；未解析技能继续保留在解析结果与审核队列中。
2. Validation 校验 Extraction bundle。应用裸配置默认 `DATA_VALIDATION_MODE=off`；生产角色对齐 Compose 显式使用 `enforce` 并运行 Validation worker，使 PASS/WARN/BLOCK 形成发布门禁。WARN 需人工批准，BLOCK 不可发布。
3. 单条 JD 审核确认岗位分类、职责和其他非技能结构化字段。技能采用逐项分流：`resolved` / `manually_confirmed` 进入下游，`unresolved` / `ambiguous` / `conflict` / `pending` / `rejected` 保留在解析结果但不进入技能投影。JD 审核与发布不再要求至少存在一个已归一化技能。
4. 发布快照使用 `resolved-skills-only.v1` 技能投影，并记录未进入技能投影的数量；职责、学历、经验等其他有效字段仍可发布。发布门禁严格核对同一份人工确认投影与 Validation 快照，不再把完整原始解析层误当作下游投影进行比较。完整未解析内容保留在主系统解析结果中，不会被删除。
5. KG 只按 `requirement_id` 和权威技能 ID 导入，不再通过同名或别名猜测所属要求。构建完成后需清空审核任务并通过发布门禁，才能生成 GraphVersion。
6. Matching 的规则匹配可独立工作。只有显式设置 `MATCHING_VECTOR_INDEX_ENABLED=true` 且部署向量索引能力时，主系统才发送向量索引事件。

在线 Extraction 入口默认接入岗位分类：JD 的 `POST /api/v2/extractions` 在返回 bundle
前生成 `normalized_result.job_classification`；CV 主后端固定请求
`POST /api/v3/cv-extractions`，接收
`normalized_result.position_classifications`。CV 中每个期望岗位或历史岗位保持独立
来源身份；重复身份或覆盖缺失由 Validation BLOCK，未解析分类产生 WARN。完整
normalized payload 随审核确认写入 `ValidatedCVSnapshot`，不会由主后端根据岗位名称
重新分类。

## 前端操作

### 审核顺序

1. 在“技能归一化审核”中逐项处理 Extraction 未能唯一映射的技能：选择主系统标准技能，或标记为“仅保留原文”。每项技能独立处理，不要求先处理完同一 JD 的全部技能。
2. “JD 数据中心”默认展示总量、待审核、已确认、已发布和失败统计；点击“查看全部 JD”后才按 20 条一页读取明细。它只负责导入、解析和只读查看，不承担修订、确认或发布。
3. 审核人员在同一审核界面选择标准岗位、对照原文核对职责和其他准入条件；每次人工修改都会形成新的审核后 Extraction 投影并重新触发 Validation，避免旧验证结果与当前内容错位。未确认技能仍保留在 JD 解析结果中，但不进入该发布投影；旧 Validation 审核待办会由最新人工确认版本替代。管理员确认无误后等待本次 Validation 完成并直接发布。
4. JD 发布后按标准岗位聚合构建图谱。“图谱结论审核”只处理系统归纳出的技能关系、职责、要求和整图发布结论，不再重复创建技能归一化任务。
5. “岗位全景”只展示已有发布版本的岗位，并同时展示参与构建的 JD 数和最终标准技能数；它不是全部 JD 列表。

图谱结论审核只返回每个标准岗位最新且包含有效样本的构建批次；职责和任职要求聚合同时返回 `evidence_ids` 对应的原文，不再把历史构建任务和无证据空壳混在当前队列中。零技能关系的构建不会生成整图发布审核。历史版本仍保持不可变，旧错误审核记录不作为当前可操作任务展示。

- “审核中心 / JD 与数据质量审核”：领取 JD 或 Validation 任务，在当前界面查看明确阻断项、选择标准岗位、对照 Evidence 核对字段并通过发布或退回。
- “归一化审核”：查看原文 Evidence，搜索并选择标准技能；“仅保留原文”表示该技能不进入下游，不删除原始记录。
- “审核中心 / 图谱结论审核”：查看系统准备采用的内容、支持它的 JD 原文和影响范围，再确认采用或退回修正。
- “图谱构建”：检查门禁，处理完所有待审核项后发布版本。

上述工作台使用页内标题、字段标签和普通错误文字说明操作，不再使用大面积带边框提示框。

标准岗位下拉框中的选择只是待保存值；界面会明确区分“已选择但尚未写入”和“当前 JD 版本已保存”。只有保存接口成功并重新读取审核上下文后，才允许执行最终审核与发布。Extraction 中存在但归一化层缺失的技能按“未归一化”保留，不会再导致岗位保存事务回滚。

## 配置边界

- `DATA_VALIDATION_MODE=off` 是应用裸配置默认值；当前 Compose 默认设置 `enforce` 并启动 Validation worker。
- `MATCHING_VECTOR_INDEX_ENABLED=false` 是无向量基础设施时的默认值；不要只启用 Matching API 就发送向量事件。
- CV Demo 快照服务、资源和预计算目录已移除；仓库不提供 Demo 数据、Demo seed、Demo 快照或 Demo 流程脚本。真实批次数据位于 Git 忽略的运行目录，并通过审核链路导入。
- v3 迁移在 SQLite 测试库中使用 Alembic batch mode 创建岗位治理约束，并识别历史 `create_all` 已创建的 `cv_position_classifications` 表；生产运行时仍只支持 PostgreSQL，不提供 SQLite 降级路径。

## 标准岗位目录

主系统启动时执行 `scripts/sync_position_taxonomy_catalog.py`，将
`config/position_taxonomy_catalog.v3.json` 中的 20 个岗位族、112 个标准岗位同步到
数据库。岗位族只用于聚合，`position_code` 才是跨系统岗位身份。目录岗位包含定义、
别名、纳入/排除条件、易混岗位规则、生命周期和样本支持状态。管理员 CRUD 接口不得
创建、修改或删除这些权威目录岗位；目录同步是唯一写入口。

主系统岗位分类严格接受 `job-position-classification.v3`：
`position_code`、Top-K 候选、`career_level`、`leadership_scope`、
`technology_focus_codes`、`industry_context_codes`、
`observed_skill_domain_codes`、状态、置信度、原因、Evidence 和策略版本。
Extraction 不输出主系统 UUID；主系统仅对 `resolved/manually_confirmed` 且目录版本、
岗位 active、状态不变量和 Evidence 完整的结果绑定 `StandardPosition.id`。岗位、
职级、领导范围、技术方向和行业分别审核，修改岗位不会清空其他维度。

岗位目录导出与 KG 导入统一使用 `resolved-position-catalog.v3`。当前生产链只按 `position_code` 绑定 `position-taxonomy.v3.0.0` 的 active 岗位，不按名称或岗位族创建占位岗位。
KG 首次把旧主系统 UUID 岗位身份迁移为 `position_code` 时，会在同一数据库事务内临时
暂停 `graph_versions/relation_claims` 的更新触发器，仅允许岗位外键 `ON UPDATE
CASCADE` 完成身份换码，并在提交前恢复不可变保护。后续重复导入会同步权威目录的岗位
名称、岗位族、`active/deprecated` 和 `sample_support_status` 变化；已发布图谱事实本身
仍不可修改。

图谱构建阶段默认逐岗位执行，适合演示环境或低配机器；高性能本机可传 `--graph-build-workers N` 让多个岗位的图谱构建与发布并发执行。

KG 的已发布 JD 事实映射完整保留 v3 岗位分类状态和多维字段，不读取或重新生成
`canonical_name / job_family / category_code / subcategory_code` 等旧岗位字段。

批量回放在确认解析结果后会等待异步 Validation 门禁完成，再发布 JD；`validation_pending` 表示校验仍在处理，不能作为导入失败直接跳过。对于已经完成外部审核的最终审计包，可显式传入 `--approve-validation-warnings`；脚本同时按 `source_jd_id` 与 `extraction_task_id` 只批准当前 JD 当前投影对应的 WARN 审核任务，BLOCK 仍然关闭发布门禁。JD 查询响应同时返回 `source_jd_id / source_jd_version_id / extraction_task_id`，用于核对这条审核血缘。Compose 主后端健康探针为完整 `/readiness` 检查预留 10 秒请求时间，避免依赖检查约 4 秒时误判服务不可用。

若某条已在同一次全量重建中独立完成，可用重复参数 `--skip-source-document <Extraction document_id>` 从后续批量运行中明确排除；报告会将其记录为 `explicitly_skipped`，不做旧记录兼容或模糊匹配。

受控审计包脚本位于 `scripts/import_audit_jds_concurrent.py`，其并发、重试和过滤参数以 `--help` 为准。发布事实由基础栈中的 `kg-outbox-worker` 投递。当前可执行的数据入口与限制见 [JD 与 CV 数据导入](../../../docs/user-guide/data-import.md)。

恢复导入时，已批准的 Validation WARN 不要求重新生成待审核任务；已有 BLOCK 投影只有在当前审计包生成的投影发生变化时才重新写入。导入转换按 Validation 的事实身份规则移除完全重复项，保留第一条及其 Evidence。主系统 `jd_parse_results.experience` 使用 `TEXT` 保存完整经验摘要。KG 技能目录对相同 `skill_id` 获取 PostgreSQL 事务级锁后再 upsert，避免并行 Outbox 事件首次同步同一技能时产生唯一键竞态。

受控切换使用两次运行：第一次创建 v3 JD/CV 版本并执行有界 Validation，遇到待人工
审核会在发布前停止；审核完成后用相同 `migration-run-id` 重跑，完成 v3 重新发布、
目录导入、release 导入、图谱重建和 Matching 重索引。旧发布快照始终不可变。
如果首次运行在 JD 版本提交后、Validation staging 前中断，相同 run 会复用已有 v3
ParseResult 并幂等补建 Validation 任务。若目录文件或 KG release 目录已经生成，重跑
会先校验完整目录字段、岗位 UUID/code 双向唯一、数量和 release 参数，并让主系统重新
生成当前权威目录后逐字段比较；完全一致时才复用。cutover manifest 只绑定岗位数量和
排序后的 `position_code` 清单，不使用目录哈希。参数或目录内容不一致时拒绝切换。

```powershell
python scripts/position_v3_cutover.py `
  --jd-report <jd-report.json> --cv-report <cv-report.json> `
  --jd-run-dir <jd-v3-run> `
  --cv-run-dir <cv-v3-run> --cv-workbook <cv-workbook.xlsx> `
  --position-catalog-output <position-catalog.json> `
  --kg-release-output <kg-release.json> `
  --release-id <release-id> --migration-run-id <migration-run-id> `
  --publisher-id <publisher-id> `
  --window-start <iso-time> --window-end <iso-time> `
  --git-commit <commit> --execute
```

检查目录而不写入可执行：

```powershell
python scripts/sync_position_taxonomy_catalog.py --check
```

全量重建后可用 `scripts/import_cv_workbooks.py` 导入一个或多个单列 CV 原文工作簿。脚本按正文哈希建立稳定来源身份，拒绝重复正文，等待 CV Extraction 完成后确认 Resume 并生成技能画像：

```powershell
python scripts/import_cv_workbooks.py --workbook /tmp/resumes.xlsx --workbook /tmp/resumes_20.xlsx
```

如果 CV Extraction 已产生通过的预计算运行目录，可以不再调用模型，而是将
`records/success/` 中的结果经主系统严格契约、Validation、Review Confirmation 和
immutable snapshot 流程导入。默认只校验不写库；加 `--execute` 才执行幂等导入：

```powershell
python scripts/import_precomputed_cv_snapshots.py `
  --run-dir /tmp/cv-run `
  --workbook /tmp/resumes.xlsx

python scripts/import_precomputed_cv_snapshots.py `
  --run-dir /tmp/cv-run `
  --workbook /tmp/resumes.xlsx `
  --execute
```

该命令不导入 `records/failed/`，输出中的 `model_api_calls` 固定为 `0`。dry-run 和执行模式
都会同时输出 `failed_precomputed_cv_count`、按 `error_type:stage` 聚合的
`failure_reasons`，以及带 `cv_id / row_index / error_message` 的 `failed_cases`；这使外部
模型服务错误、超时和契约失败可以直接进入 failure case 台账，而不会被成功导入数量掩盖。
