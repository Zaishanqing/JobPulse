# 趋势模块后端交付契约

版本为 `trend-delivery.v1`。所有成功响应保持 `{code,message,data,trace_id}` 外层结构；单资源 `data` 包含统一交付字段，列表 `data` 包含 `items/pagination/filters/sort/not_found_ids`。

前端消费列表接口时必须读取 `data.items`，不能把 `data` 本身当作资源数组。

统一字段：`resource_type/resource_id/status/progress/source_coverage/missing_sources/quality_flags/evidence_references/review_status/review_task_id/publication_gate`。`publication_gate.blockers` 是稳定的机器可读门禁码；展示文案由调用端维护。运行资源的 `publication_gate.applicable=false`。

趋势报告额外返回明确的数据模式：`analysis_mode=remote_multi_source` 表示报告已由趋势分析服务完成并投影；前端不得根据报告内容猜测来源。创建远程任务后，调用端必须轮询 `GET /api/v1/trend-analysis/tasks/{task_id}` 至 `canonical_status=succeeded`，该查询同时完成远程结果同步，然后再刷新报告列表。

接口清单：

- `POST/GET /api/v1/predicted-positions/tasks[/{task_id}]`
- `GET /api/v1/predicted-positions`、`POST /api/v1/predicted-positions/batch-query`
- `GET /api/v1/predicted-positions/{predicted_id}`
- `GET /api/v1/trend-runs`、`POST /api/v1/trend-runs/batch-query`
- `POST /api/v1/positions/{position_id}/trend-analysis/tasks`
- `GET /api/v1/trend-analysis/tasks/{task_id}`
- `GET /api/v1/positions/{position_id}/trend-reports`
- `POST /api/v1/trend-reports/batch-query`
- `GET /api/v1/trend-reports/{report_id}`、`POST /api/v1/trend-reports/{report_id}/publish`
- 审核沿用 `POST /api/v1/review-tasks`、`POST /api/v1/review-tasks/{task_id}/claim|approve|reject` 和 `GET .../history`。

分页从 1 开始，`page_size` 为 1–100。批量查询 `ids` 为 1–100 个 ID，响应按去重后的输入顺序返回，未知 ID 放入 `not_found_ids`；全部未知时仍返回 200 和空 `items`。筛选无结果同样返回 200，不以 404 表示空集合。

鉴权错误为 401，权限不足为 403，资源不存在为 404，冲突为 409，请求参数失败为 422，既有发布门禁失败保持兼容状态码 400，上游不可用为 503/504。错误响应仍保持统一外层结构。创建远程任务由请求指纹保持幂等，批量查询会对重复 ID 去重。

完整结构见 [OpenAPI](./trend-delivery.openapi.yaml)，TypeScript 示例见 [类型定义](./trend-delivery.types.ts)，六类联调数据见 [场景数据](./trend-delivery-scenarios.json)。

示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "schema_version": "trend-delivery.v1",
    "resource_type": "trend_report",
    "resource_id": "report-001",
    "status": "published",
    "progress": 1.0,
    "source_coverage": 0.95,
    "missing_sources": [],
    "quality_flags": [],
    "evidence_references": ["snapshot:arxiv:2401.00001"],
    "review_status": "approved",
    "review_task_id": "review-001",
    "publication_gate": {"applicable": true, "eligible": true, "blockers": []}
  },
  "trace_id": "req_example"
}
```
