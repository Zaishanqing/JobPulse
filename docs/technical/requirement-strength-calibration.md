# 岗位要求强度校准与 JD 通胀诊断

> 文档类型：正式技术说明  
> 方法版本：`requirement-strength-calibration.v1`  
> 适用范围：标准岗位画像中的 required 技能  
> 事实源：去重 JD、结构化 requirement modality、企业与来源、Evidence、GraphVersion

## 问题定义

JD 通胀不是“原文不真实”，也不等同于文本抄袭。它指单条 JD 将某项真实能力声明为
必备要求，但同岗位的独立市场样本不足以支持把它提升为标准岗位的普遍必备能力。

系统因此不删除或改写企业原始要求。原始 requirement、Evidence 和完整岗位图谱继续保留；
校准层只决定该要求是市场稳定核心、企业专项要求，还是需要抑制的通胀风险，避免单一企业的
理想化技术栈直接抬高标准岗位画像门槛。

```text
单条 JD required 技能
→ 同岗位去重 JD 聚合
→ required prevalence / purity / supporting JD
→ 跨企业、跨来源与 leave-one-out 检查
→ market_supported / enterprise_specific / inflation_risk
→ 标准岗位画像与逐 JD 可解释诊断
```

当前版本只处理技能要求。学历、年限、证书和职责范围不进入本算法，避免用一套未经验证的
规则覆盖不同类型的要求。

## 市场统计

对岗位 `P`、技能 `s`，在构建所选的有效样本和版本化去重映射上计算：

- `supporting_jd_count`：提及该技能的去重 JD 数；
- `support_ratio = supporting_jd_count / total_dedup_jd_count`；
- `required_supporting_jd_count`：将该技能声明为 required 的去重 JD 数；
- `required_prevalence = required_supporting_jd_count / total_dedup_jd_count`；
- `required_purity = required_supporting_jd_count / supporting_jd_count`；
- `enterprise_count`、`source_count`：支持样本覆盖的已知企业和来源数。

同一 JD 重复写同一技能只计一次；复制簇按版本化的 `document_deduplication` 只计一个市场样本。
企业和来源缺失时不会伪造独立性。

## 分类规则

正式 required gate 复用 Position Profile 已固定的阈值：

```text
required_prevalence >= 0.15
AND required_supporting_jd_count >= 3
AND required_purity >= 0.50
```

分类如下：

| 状态 | 规则 | 画像含义 |
| --- | --- | --- |
| `market_supported` | required gate 全部通过 | 市场支持的稳定必备能力 |
| `enterprise_specific` | gate 未全通过，但 `enterprise_count >= 2` 或 `supporting_jd_count >= 3` | 少数企业或专项场景要求；保留为上下文，不作为标准岗位普遍必备能力 |
| `inflation_risk` | gate 未通过，且跨企业/跨 JD 支持仍不足 | 保留原始事实，但抑制其成为标准岗位必备能力 |
| `not_applicable` | 当前聚合 modality 不是 required | 不参与 required 通胀判断 |

失败原因使用稳定 reason code：

- `LOW_MARKET_REQUIRED_PREVALENCE`；
- `INSUFFICIENT_CROSS_JD_REQUIRED_SUPPORT`；
- `LOW_REQUIRED_PURITY`；
- `INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT`。

阈值来自该 GraphVersion 的 `dependencies.position_profile_thresholds`，因此历史画像可复现；调整
阈值会改变 `build_config_version`，不会静默改写历史结果。

## Leave-one-out 与逐 JD 风险

对每条 JD 的每个去重 required 技能，诊断同时返回：

- 移除当前 JD/复制簇后的 required JD 支持数；
- 移除当前企业后的其他企业支持数；
- 移除当前来源后的其他来源支持数。

这使单条 JD 自己不能成为自己的“市场共识”证据，也能区分同一企业多渠道投放与真正的
跨企业扩散。

JD 级指标为：

```text
inflation_ratio = inflation_risk required 技能数 / required 技能总数
```

风险展示分档为 `0–20% low`、`>20–40% medium`、`>40% high`。这些是产品风险分级规则，
不是统计学真值，也不是对企业招聘行为的事实认定。

## Position Profile 输出

`position-profile.v3` 的每个正式技能关系新增：

- `requirement_market_status`；
- `inflation_risk`；
- `inflation_reason_codes`；
- `enterprise_count`、`source_count`。

顶层 `requirement_inflation` 包含岗位汇总和逐 JD 诊断。即使某个单点技能因支持不足未进入
`skill_relations`，它仍出现在逐 JD 诊断中，并通过 `evidence_id` 回到原始 Evidence。

示例：

```json
{
  "skill_id": "SKILL_TENSORFLOW",
  "skill_name": "TensorFlow",
  "jd_modality": "required",
  "market_status": "inflation_risk",
  "inflation_risk": true,
  "reason_codes": [
    "LOW_MARKET_REQUIRED_PREVALENCE",
    "INSUFFICIENT_CROSS_JD_REQUIRED_SUPPORT",
    "INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT"
  ],
  "market": {
    "support_ratio": 0.02,
    "supporting_jd_count": 1,
    "required_supporting_jd_count": 1,
    "required_prevalence": 0.02,
    "required_purity": 1.0,
    "enterprise_count": 1,
    "source_count": 1,
    "leave_one_out_required_jd_count": 0,
    "leave_one_out_enterprise_count": 0,
    "leave_one_out_source_count": 0
  }
}
```

## 与既有质量分的关系

既有 `inflation_score` 是文档质量权重的一部分，关注技能词密度或职级、经验与职责范围错配，
可降低异常 JD 的有效样本权重。要求强度校准则发生在同岗位市场聚合之后，回答“这项 required
要求是否具有市场共识”。两者是互补信号，不能互相替代：

- 文档级质量分影响样本权重；
- 市场校准决定 required 要求的正式强度与可解释状态；
- 抄袭/重复检测负责传播校正；
- Evidence 和岗位版本负责追溯与复现。
