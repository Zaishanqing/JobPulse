# CV 抽取运行与验证

> 文档类型：运维
> 维护状态：active
> 适用范围：`services/cv-extraction/`
> 事实源：模块代码、配置、Schema 与测试
> 责任角色：模块维护者
> 最后复核：2026-08-09

## 输入要求

输入支持 CSV、XLSX、XLS。原文列名必须明确匹配以下任一别名：

```text
cv_text | 简历原文 | 原始文本 | resume_text | CV原文
```

如果找不到原文列，该行会产生 `missing_required_input`，不会猜测使用第一列。

## 安装与运行

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 中配置 `DEEPSEEK_API_KEY`，然后运行：

```powershell
python scripts/run_extract.py --input data/resumes.xlsx --output output --model deepseek-v4-flash --continue-on-error --max-workers 20 --semantic-retry-attempts 2
```

`--max-workers` 默认值为 `20`；命令中保留显式参数，便于运行记录直接体现本轮并发配置。
抽取完成后会默认执行 `position-taxonomy.v3` 角色分类，并将一致的 MatchFeature、
能力验证、manifest 和研究报告写入
`output/runs_position_v3/<run_id>_position_v3`。岗位分类默认每个请求处理 1 个角色，
并发继承 `--max-workers`；可用 `--position-max-workers` 单独覆盖。

`run_extract.py` 默认直接读取相邻 JD 项目的权威词表和审核后的技能分类快照，无需再
维护 CV 私有技能 ID。可以通过 `--normalization` 和
`--skill-taxonomy-snapshot` 显式指定配套文件；二者身份不一致时运行会明确失败。

HTTP 模式下，主后端调用 `POST /api/v3/cv-extractions`。该接口在单次请求中完成抽取、
技能归一化和全部期望/历史岗位分类，并将结果放入
`normalized_result.position_classifications`。分类模型校验最多尝试
`POSITION_CLASSIFICATION_MAX_ATTEMPTS` 次，目录由 `POSITION_TAXONOMY_PATH`
指定；目录、岗位标题或 Evidence 缺失时请求明确失败，不跳过分类。

抽取校验阶段会在 Schema 校验前执行与 JD 同序的确定性字段校正：固定术语保护、共享前后缀复合技能展开、项目名按 name Evidence 修正、声明区原文能力短语恢复、原文明示熟练度回填、语言等级证据闭合、奖项明确范围词校正，以及 `name + item_type` 的权威类型校正。所有修正均写入审计记录；只接受原文连续短语和词表唯一映射，不做模糊匹配，也不会因项目名称错误直接删除已有原文支持的项目。

模型 JSON 中任意层级出现重复键时会被视为非法 JSON 并触发有界重试，不允许后值静默覆盖前值。来源分区覆盖属于全局完整性约束，会在局部 Schema/Evidence 修复前检查，避免重试额度先被局部错误消耗后才发现整段漏抽。

候选校验会一次汇总当前可确定发现的 Schema、Evidence、来源覆盖和语义问题；能够根据非标题 Evidence 区间唯一定位到现有经历的漏抽区块，会与该对象的其他错误合并为一次局部修复。只有无法唯一定位对象时才执行完整重抽。HTTP 客户端关闭 SDK 内置重试，由流水线已有的显式有界重试统一管理，避免隐藏重试叠加耗时。

受保护经历分区中若出现未被覆盖、且无法归属到现有对象的孤立日期，校验器会按配置携带相邻原文并授权向对应经历集合追加一个对象；不会为普通长文本自动新增经历。普通事实无法唯一归属时，校验器只授权修复配置距离内的候选经历，不再因此全量重抽取。即使候选因其他字段无法通过 Schema，也会预先汇总全部匹配字段证据、项目名称形态、角色误分类、重复对象和技能原子性问题，避免逐轮暴露错误。

局部 replace 是完整对象替换。修复层会按照字段契约重新闭合 `field_evidence`：非空且非 `unknown` 的匹配字段必须各有一个绑定，空字段不得残留绑定；模型遗漏但原对象仍能确定支持关系的字段证据会被保留。技能召回不依赖把JD词表注入提示词：候选通过 Schema 后，校验器使用共享JD权威词表检查原文明示的具名技术是否出现在对应技能区或经历技术栈。默认只检查具名编程语言、框架、库、数据库、工具和平台；方法论、领域知识及中文技术项必须在词表中显式声明 `source_coverage: true`，避免把“算法、缓存、部署”等普通概念强制技能化。

## 现有 CV 结果离线归一化并生成 MatchFeature

该命令不调用模型，也不修改 `annotations.jsonl`：

```powershell
python scripts/build_match_features.py `
  --run-dir output/runs/cv_v2_first_run `
  --normalization resources/normalization/2.0/normalization_map.yaml `
  --as-of-date 2026-07-18
```

`--as-of-date` 用于计算“至今”的工作经历，写入产物和 manifest，保证工作年限可复现。历史重归一化只消费已经通过抽取类型契约的正式标注，不修改抽取事实；不合法的旧标注会明确失败。无法解析的日期、薪资、岗位或技能保持 `unresolved`，不会近似映射。

原始抽取 run 的 MatchFeature 生成不会猜测岗位类别，role feature 先标记为
`position-taxonomy-v3-review-required`。默认后处理会立即分类并写入独立 v3 run；
只有显式使用 `--skip-position-classification` 时才停留在待分类状态。

建议显式使用 `--continue-on-error` 保留整批失败案例；失败对象不会作为成功结果导出。

## 本地验证

```powershell
python -m pytest tests -q -p no:cacheprovider
python -m compileall -q src scripts tests
python scripts/run_extract.py --help
```

测试使用本地假客户端，不调用真实 API。
