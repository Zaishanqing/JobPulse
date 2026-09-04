# JD / CV 结构化抽取技术说明

## JD 抽取

- 输入：多源采集 Bundle（版本化契约 `CrawlerJDEnvelopeV1` / `BundleManifestV1`）；
- 抽取：rule / LLM 双模式，输出结构化 requirement graph（原子要求、关系、分组）；
- 归一化：技能表达经词法 + 语义候选排序后自动接受或送人工复核；
- 发布：抽取结果必须经过审核；发布后进入岗位画像与图谱。

JD 解析准确率：Exact Span F1 `99.67%`。

## CV 抽取

- 输入：PDF / DOCX / 图片（OCR）上传；
- 输出：结构化字段（技能、经历、教育、项目、证书、语言等）与逐字段 Evidence；
- 审核：抽取结果必须人工确认后才生成 validated snapshot，供匹配使用；
- 技能归属与能力等级由 Evidence 支撑。

CV 提取准确率：Field F1 `99.45%`。

## 离线 Bundle

采集侧以版本化 Bundle 交付离线数据，含完整来源、时间与去重信息；导入链路
支持并发导入、幂等与审核。
