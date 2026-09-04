# 动态职业感知技术说明

岗位动态感知按三条平级路径组织：**既有岗位演化**、**新兴岗位发现**、**趋势情报**。
三者共享 Evidence 与不可变版本语义，但各自有独立的判定单位和门禁。

## 既有岗位演化（GraphVersion）

- 知识图谱按 `(position_id, version_name)` 生成不可变 GraphVersion；
- 演化事件检测器在连续版本对上工作，输出事件必须绑定版本对与 Evidence；
- 系统对数据覆盖波动（来源集中、样本量不足）做显式抑制，避免把覆盖变化
  误判为真实演化；未知类型事件不默认置信度。

## 新兴岗位发现（EMERGE v3.2）

- 候选形成：多视角聚类（文本语义候选、技能共现与职责 TF-IDF）只负责形成职业簇，
  Embedding 只能提出候选，合并必须得到两类真实视角支撑；
- 正式判定：EMERGE v3.2 两阶段（Stage 1 成熟职业结构解释 + Stage 2 六类时序证据 +
  硬门控），最终状态为 `insufficient` / `not_emerging` / `weak` / `emerging`；
  `emerging` 的证据等级为 `short_window`，不包装成长期市场定论；
- 运行口径：由服务端固定算法版本与已验证输入数据执行，`discovery.v2` 生成两个
  因果请求；同一 `source_record_id` / `content_hash` 的重复抓取不能独立触发
  `emerging`；
- 候选生命周期与判定分开：`weak_signal → incubating → emerging_candidate →
  stable_emerging_role`，另有 `dead / noise`；进入治理需要
  `stable_emerging_role` 并通过服务端门禁，不自动审核或发布。

## 趋势情报（Trend Intelligence）

- 多窗口（周级）真实序列上的趋势状态：declining / rising / volatile / stable；
- 低量主题通过 absolute-support 门禁降级为 insufficient_evidence，不做趋势判断；
- ChangePoint 检测支持方向反转拐点，并按 ±1 窗口容差与人工标注对齐。

## 技术栈视图 / 能力等级视图

岗位与候选人按技能目录提供技术栈视图与能力等级视图，数据来自技能归一化、
Evidence 归属与能力等级判定，不依赖模型猜测。
