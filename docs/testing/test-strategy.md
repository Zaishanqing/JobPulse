# 文档与模块测试策略

> 文档类型：质量
> 维护状态：active
> 适用范围：JobPulse
> 事实源：`scripts/test-all.ps1`、`scripts/test-all.sh`、`tests/run-ci.sh` 与模块测试配置
> 责任角色：质量维护者
> 最后复核：2026-08-21

模块 runner 按 service owner 提供套件；repo-level architecture、contract、routing 和
runtime-ownership 测试由 `tests/run-ci.sh` 单独执行，不复制到 service tests。不存在额外的
`Core`、`Full` 或 `All` 聚合套件。Bash 与 PowerShell 入口的套件集合并不相同：
`scripts/test-all.sh` 接受下表全部十项，`scripts/test-all.ps1` 当前不支持
`TrendIntelligence` 和 `EmbeddingService`；Bash Frontend 入口还包含 lint、typecheck 和 build。

| 套件 | 主要范围 |
| --- | --- |
| `Main` | 主后端领域、应用、接口、持久化与集成测试 |
| `KnowledgeGraph` | 图谱服务及其迁移、契约和发布链路 |
| `MatchingService` | Matching API、worker、dispatcher 与持久化 |
| `EmergingDiscovery` | 发现算法、正式 Run 协议、候选轨迹与 PostgreSQL 集成 |
| `TrendIntelligence` | 趋势 API、采集、变化分析、持久化与 worker |
| `EmbeddingService` | Embedding API 最小测试与配置加载验证 |
| `Crawler` | 爬虫 API、任务与数据契约 |
| `JDExtraction` | JD 抽取、归一化与输出契约 |
| `CVExtraction` | CV 抽取、归一化与输出契约 |
| `Frontend` | 前端类型、单元测试与构建门禁 |

测试源已物化不等于根路由已经完成候选 parity 验证。当前 Bash `Crawler` 分支只执行
`services/crawler/unified_api/tests`，repo-level 测试由 `tests/run-ci.sh` 执行；Main 的
affected-mode core guardrail 位于 `apps/api/tests/`，不复制仓库级测试。外部依赖或未执行的
collect-only 结果不得写成全套通过。

共享契约、组合根、认证授权、数据库迁移、Outbox 或跨模块接口变化，至少运行所有受影响
模块；无法静态确认影响范围时逐个运行根套件。外部 Provider、真实网站和大模型联机测试
必须显式启用，并记录凭证、网络、数据授权与失败语义。

纯 Markdown 更新的最低验证为：

```powershell
python scripts/docs/check_markdown_links.py
git diff --check
```

修改文档生成器、链接检查器、示例配置或可复制命令时，还要运行相应脚本或 Compose 配置
检查。活跃文档不保存会快速失真的累计测试数量；阶段结果写入 CI/评估报告，并注明提交、
环境、命令和 PASS/FAIL。

## 路由与运行边界

各模块的 resolver、API_PATH、MODULE_PATH 和 test runner 均以仓库根目录为基准，
路径变化不会隐式落到其他目录。统一覆盖率结果以 `scripts/coverage-all.py` 生成的
`artifacts/coverage/summary.md` 为准。

使用不可变历史 Git blob 的 D5 数据测试还要求 checkout 包含对应对象。浅克隆
可能无法执行 `git cat-file <historical-blob>`；测试环境应提供完整 history、显式
fetch 对象或经哈希校验的 `D5_BUNDLE_CACHE_DIR`，不得在缺对象时生成替代样本。
