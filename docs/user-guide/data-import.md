# JD 与 CV 数据导入（当前 JobPulse 入口）

当前代码提供离线 JD Bundle 导入、业务 API 导入，以及受控的预计算 CV 结果脚本。
仓库不提供 `full_flow_kit` 或 `compose-readiness.ps1` 入口。

## JD 离线 Bundle

先启动并迁移主数据库，然后从 `apps/api/` 执行：

```powershell
python -m alembic upgrade head
python -m app.offline_import.cli verify <bundle.zip>
python -m app.offline_import.cli import <bundle.zip>
python -m app.offline_import.cli history
python -m app.offline_import.cli show <bundle-id>
```

Importer 校验 `nfbs-jd-bundle.v1` manifest、文件 hash、记录身份和父链；重复导入已完成 Bundle
返回 `no_op=True`。`--allow-gap` 只用于操作员确认父 Bundle 无法获得的恢复场景，`--retry`
只用于失败/导入中批次的显式恢复。

导入结果是 `SourceJD/SourceJDVersion` 及 rule ExtractionTask，不等于已经完成 Validation、
人工审核、发布、KG 构建或匹配索引。后续状态必须通过主后端 API/审核界面推进。

## CV 数据

常规入口是统一前端/主后端的简历上传 API，流程为：

```text
upload -> SourceCVVersion -> CV Extraction -> review/confirm -> ValidatedCVSnapshot
```

若已经有与当前 Contract、taxonomy 和原文严格匹配的预计算产物，可从 `apps/api/` 使用：

```powershell
python scripts/import_precomputed_cv_snapshots.py --help
python scripts/import_precomputed_cv_results.py --help
```

这两个脚本针对预生成批次与工作簿做严格校验，不是任意 CV 文件的替代 Provider。具体参数以
`--help` 和脚本当前实现为准，禁止为了导入而伪造原文、Evidence、岗位分类或版本字段。

## 部署与验证边界

当前 Compose 基础栈已编排 Extraction、Validation 和 KG Outbox worker；CV profile 同时编排
Provider 与独立 CV worker。启用 CV 全链仍需设置 `CV_EXTRACTION_ENABLED=true` 和有效模型凭据。
运行后应以 API 状态、审核记录、发布版本和下游服务资源逐阶段确认，不能用“导入成功”代替整链成功。
