# JD 抽取服务

`services/jd-extraction/` 是独立 JD LLM 抽取 Provider。它接收版本化
`CrawlerJDEnvelopeV1`，输出 Extraction V2 bundle，并负责文本预处理、DeepSeek 调用、
Evidence 对齐、确定性字段修正、技能归一化、岗位分类和严格校验。它不创建主系统 JD Draft、
Publication、ValidationTask 或 Knowledge Graph 状态。

HTTP 入口是 `src.main:app`：`GET /health`、`GET /readiness` 和受 internal token 保护的
`POST /api/v2/extractions`。批处理入口位于 `scripts/run_extract.py`。

```powershell
pip install -e ../../packages/contracts
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8000
python scripts/run_extract.py --help
```

运行需要有效 `JD_EXTRACTION_INTERNAL_TOKEN`、资源路径和 DeepSeek 配置。JobPulse
`model-extraction` profile 传递 `DEEPSEEK_API_KEY`；默认主 Extraction worker 通过配置的
`JD_EXTRACTION_BASE_URL` 处理显式 `llm` 任务。
