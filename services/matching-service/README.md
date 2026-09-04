# 匹配服务

## 岗位分类 v3

岗位身份统一使用 `position-taxonomy.v3.0.0 + position_code`。只有 JD/CV
分类已解决、taxonomy 一致、职级与领导范围兼容且岗位样本支持为 `sufficient`
时，结果才允许进入 `strong_match`。未解决分类、版本不兼容或零/低样本画像不得
产生强匹配结论。

> 文档类型：模块入口
> 维护状态：active
> 适用范围：`services/matching-service/`
> 事实源：服务代码、配置、迁移与测试
> 责任角色：Matching Service 维护者
> 最后复核：2026-07-31

独立人岗匹配服务，负责严格画像校验、确定性匹配、可选语义召回、差距分析、
学习路径建议、异步评估与审计。服务不读取其他模块数据库。

## 主要功能

- 校验 CV 与岗位画像的版本、授权和数据血缘。
- 计算技能覆盖、缺失与弱项，生成可解释匹配报告。
- 基于差距生成学习路径，支持异步任务、重试、审计和指标。
- 通过主后端 HTTP Contract 读取获授权画像，通过 Outbox 接收索引事件。
- 可选接入 Embedding/向量召回；未启用时保持确定性规则评分。

## 最短启动

前置条件：已安装本模块依赖。执行目录：
`<repo-root>/services/matching-service`。

```powershell
pip install -e ../../packages/contracts
pip install -e .
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

成功信号：`GET /health` 返回成功。生产拓扑需要 PostgreSQL、Redis、迁移和有效密钥，
具体环境变量与进程模式以 `app/infrastructure/*configuration.py`、Docker entrypoint 和
运行时 OpenAPI 为准。

当前 Compose 已配置 PostgreSQL、Redis、API、worker、dispatcher 以及主后端画像 Contract。
单独开发时可在 `MATCHING_RUNTIME_MODE=development` 下显式选择 HTTP 上游和内存任务设施。

可选 CPU BGE-M3 Dense shadow 链路使用仓库根目录的
`config/semantic-demo-contract.env`。fragment 派生、索引与版本均以该文件为准；
链路说明见 [`apps/api/docs/matching-semantic-demo.md`](../../apps/api/docs/matching-semantic-demo.md)。

Evidence 语义候选召回由匹配服务语义检索链路（BGE-M3 + Qdrant）提供，
`semantic_shadow_evidence` 通过 `matching-evaluation-result.v1` 只读回传；
契约与模型版本见 `config/semantic-demo-contract.env`。服务不可用时状态为
`semantic_shadow_status=unavailable`，不会静默生成等价语义结果。

## 最短验证

```powershell
python -m pytest
python -m ruff check .
```

## 当前限制

默认匹配核心为确定性规则，语义召回和向量索引默认关闭。生产环境仍需使用独立强密钥，
并验证数据库、Redis、备份和容量配置。

## 文档

服务 API 以运行时 `/docs` 和 `app/api/router.py` 为准；跨系统方向见
[系统接口地图](../../docs/architecture/system-interface-map.md)。
