# CV 抽取服务

`services/cv-extraction/` 是独立 CV 结构化抽取 Provider。它负责文档预处理、DeepSeek 调用、
严格 Schema/Evidence 校验、确定性修复、技能归一化、岗位分类、MatchFeature 和审计产物；
不拥有主系统 Resume、ValidatedCVSnapshot 或 Matching 业务表。

HTTP 服务入口位于 `api/main.py`，CLI 批处理入口位于 `scripts/run_extract.py`。主后端通过
`jobgraph_contracts.cv_extraction_http` 的版本化 DTO 调用服务，并在自身事务中完成上传任务、
人工确认和 Snapshot 生命周期。

```powershell
pip install -e ../../packages/contracts
pip install -e .
python scripts/run_extract.py --help
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

生产调用需要 `CV_EXTRACTION_INTERNAL_TOKEN`、DeepSeek 配置及可解析的资源 manifest、
normalization map、skill taxonomy 和 position taxonomy。详细边界见 [文档索引](docs/index.md)。
