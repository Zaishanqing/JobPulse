# 本地开发入口

本项目是独立仓库，CI、测试、启动和 Compose 入口均以仓库根目录为基准，
不依赖任何外部目录。

仓库没有覆盖所有 Python 服务和前端的统一安装命令。按所有者目录安装与运行：

| 目标 | 执行目录 | 实际入口 |
| --- | --- | --- |
| 集成栈 | 仓库根目录 | `docker compose -f infra/compose/docker-compose.candidate.yml ...` |
| 主后端 | `apps/api/` | 先安装 `../../packages/contracts`，再 `pip install -e .`；`uvicorn app.main:app --reload` |
| 统一前端 | `apps/web/` | `npm ci`；`npm run dev` |
| Knowledge Graph | `services/knowledge-graph/` | 先安装 `../../packages/contracts`，再 `pip install -e .`；`uvicorn app.main:app --reload` |
| Emerging Discovery | `services/emerging-discovery/` | 先安装 `../../packages/contracts`，再 `pip install -e .`；`uvicorn app.main:app --reload` |
| Trend Intelligence | `services/trend-intelligence/` | `pip install -e .`；`python -m app.entrypoint api` |
| Matching Service | `services/matching-service/` | 先安装 `../../packages/contracts`，再 `pip install -e .`；`uvicorn app.main:app --reload` 或 Docker entrypoint |
| Embedding Service | `services/embedding-service/` | 固定模型 revision 后使用模块 Compose/Docker |
| JD Extraction | `services/jd-extraction/` | 先安装 `../../packages/contracts`，再 `pip install -r requirements.txt`；`uvicorn src.main:app --reload` |
| CV Extraction | `services/cv-extraction/` | 先安装 `../../packages/contracts`，再 `pip install -e .`；`uvicorn api.main:app --reload` |
| Crawler | `services/crawler/` | Python 3.11+；先安装 `../../packages/contracts`，再 `pip install -e .`（真实浏览器采集另装 `[browser]`）；`python run.py` |

仓库级 architecture/contract/routing 测试由 `tests/run-ci.sh` 执行；它不会把这些测试复制到
`apps/api/tests`。模块测试使用 `scripts/test-all.sh <Suite> JobPulse`，Crawler 的 service-local
测试位于 `services/crawler/`；未物化的历史 repo integration 保持在迁移清单中，不创建假的
`services/integration_tests/`。

共享契约位于 `packages/contracts/jobgraph_contracts/`。服务间不得通过修改 `PYTHONPATH` 导入
其他服务内部模块；跨服务类型应来自共享契约或本服务的边界 DTO。

仓库级模块测试入口见[测试策略](../testing/test-strategy.md)。Bash 入口接受十个套件名，
PowerShell 入口只支持其中八个；两者都不代表 JobPulse parity 已完成，外部数据库、Provider、
浏览器和模型依赖仍需按套件边界显式准备。
