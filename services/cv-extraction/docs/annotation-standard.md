# CV 标注规范 V2

> 文档类型：标准
> 维护状态：active
> 适用范围：`services/cv-extraction/`
> 事实源：CV Schema、确定性处理、配置与测试
> 责任角色：CV Extraction 维护者
> 最后复核：2026-07-27

## 1. 范围与分层

抽取层只保存简历原文明示的事实，不输出标准技能 ID、归一化名称、匹配结论、评分、置信度或声明—经历求证结论。标准技能映射属于归一化层。

本版本保留 `personal_info`、`education`、`work_experience`、`project_experience`、`skills`、`languages`、`certificates`、`awards`、`publications`、`patents`、`research_outputs`、`self_evaluation` 十二个 CV 业务分区。字段名称允许因 CV 业务与 JD 不同而不同，但严格 Schema、Python 生成 ID、原子事实、Evidence、归一化分层、审计和有界修复约束与 JD 管线一致。

## 2. Evidence 硬约束

1. 每个教育、工作、项目、技能、语言、证书、获奖、论文、专利、科研产出和自我评价对象必须携带 Evidence；存在 `personal_info` 时也必须携带 Evidence。
2. 工作职责、工作成果、项目描述和项目亮点使用 `SourcedText`，每条事实分别携带 Evidence。
3. 工作与项目技术栈使用 `SkillItem`，每项技术分别携带 Evidence。
4. `evidence.quote` 必须是对应 `source_id` 文本中的连续精确子串，不得改写、拼接、跨 block 或省略中间文字。
5. entry 标题证据只能证明经历主体，不能替代职责、成果、描述、技术栈或亮点的证据。
6. `start`、`end`、`alignment`、`occurrence_index` 由 Python 确定，模型不得输出。
7. Evidence 无法精确对齐的对象不得进入成功结果。
8. `PersonalInfo`、`EducationEntry`、`WorkEntry`、`ProjectEntry` 分别使用对象专属的 `field_evidence[]` 字段名集合，不允许跨对象复用字段名。姓名、电话、邮箱、性别和出生年份不进入该集合。
9. 会进入 MatchFeature 的每个非空、非 `unknown` 标量字段必须单独绑定 Evidence。普通文本字段的值必须能在绑定引文中确定性找到；受控枚举和结构化日期只校验明确来源绑定。同一字段必须有且只有一个绑定。
10. `field_evidence` 只建立字段来源关系，不进行声明—经历求证，也不改变对应事实的原始业务语义。
11. 字段值与 `field_evidence` 的缺失、重复和悬空关系必须在对象 Schema 校验阶段拒绝；不得等到 MatchFeature 阶段处理，也不得由 Python 静默补造或删除业务事实。
12. `personal_info.name` 缺失但 `personal_info.evidence` 已精确选中纯姓名或“姓名 空格 岗位”等简历头部原文时，候选结果必须拒绝并修复；不得出现“Evidence 指向姓名、字段却为空”的伪闭合结果。

## 3. ID 与结构约束

1. `document_id`、`entry_id`、`item_id` 均由 Python 生成，模型不得输出。
2. 所有模型禁止额外字段，禁止空字符串。
3. 数组没有内容时输出 `[]`，不得输出 `null`。
4. 同一文档内 `entry_id` 唯一；顶层技能、工作技术栈和项目技术栈共享同一 `item_id` 唯一空间。

## 4. 技能抽取

1. `SkillItem.name` 必须是可复用的单个技术、工具、方法、知识领域或软技能实体。
2. 并列技能必须拆分；`C/C++`、`TensorFlow/PyTorch`、`SQLite/FTS5` 等斜杠两侧均为可独立复用具名技术的写法必须拆分，`CI/CD`、`GitLab CI/CD`、`TCP/IP`、`I/O`、`A/B 测试` 等固定术语或带产品限定词的固定术语不拆分。数字版本缩写按共同前缀展开，例如 `Vue2/3` 展开为 `Vue2` 与 `Vue3`，不得生成只有数字的技能项。中英文共享语义结构必须补全共享部分后再拆分，例如“短期/长期记忆分层”展开为“短期记忆分层”和“长期记忆分层”，`LLM/VLM Post-training` 展开为 `LLM Post-training` 与 `VLM Post-training`，不得机械生成“短期”或 `LLM` 残片。拆出的多个技能共同引用包含完整复合表达的原文 Evidence，不得虚构原文中不存在的独立 quote。
3. “相关能力”“相关技术栈”“开发经验”等描述性短语不得作为技能名。
4. 具名技术后附用途说明时，只抽取原文中可独立复用的具名核心，例如 `MCP工具协议` 抽取 `MCP`，`ReAct工具调用Agent主循环` 抽取 `ReAct`；不得把说明性后缀带入技能名。
5. `proficiency` 只能依据该技能所在同一原文语句中的明确程度词填写；“精通”为 `expert`，“熟练掌握/熟练使用/熟练/掌握/擅长”为 `proficient`，“熟悉/具备”为 `familiar`，“了解/理解”为 `know`。程度词遇到句号、分号或换行后不得向后继承；不能确定时填写 `unknown` 或省略。
6. 顶层 `skills` 只保存简历技能、自我能力等声明区域明确列出的技能；工作或项目中出现的技术进入对应经历的 `tech_stack`。
7. CET、IELTS、TOEFL 等语言证书只进入 `certificates` 和相应 `languages`，不得同时作为 `SkillItem` 重复输出。原文明示自然语言阅读、读写、听说、交流或考试水平时，也必须以该 source block 的逐字 Evidence 进入 `languages`；“可进行”等能力陈述不用于推断 proficiency。
8. 工作或项目 `tech_stack` 的 `proficiency` 必须由该技能自己的 Evidence 原句中的明确程度词支持；“使用、负责、基于、引入、参与”以及项目结果只能证明经历出现，不能在抽取层推断熟练度。
9. 技能 Evidence 所在 source block 必须直接支持该技能身份；不得把 `ReAct` 当作 `React`，不得从 `Auth.js`、文件名后缀或链接元数据推导 `JS`、`GitHub` 等技能。大小写不同且分别属于权威词表实体的名称按不同技能处理。
10. source-local taxonomy coverage 忽略仓库链接、项目地址等纯链接元数据；同一长技能名称重复出现时，每次出现都必须优先于其内部较短别名匹配，不能因重复文本额外要求短别名技能。

## 5. 证书与奖项边界

1. `certificates` 只保存职业认证、语言认证、执业许可或其他可独立验证的资格凭证。
2. `awards` 保存竞赛名次、奖金、荣誉称号、表彰和获奖证书。竞赛结果即使原文称为“证书”，也不得进入 `certificates`。
3. `CertificateKind.competition_award` 为既有字段契约的兼容枚举，V2 模型输出 Schema 禁止生成；不得让同一竞赛结果在 `certificates` 与 `awards` 之间随机选择。
4. 奖项 `level` 优先由奖项 `name` 中与结果绑定的明确范围词确定；仅当名称没有范围词时，才使用同一 Evidence 的明确范围词。`浙江省三等奖` 等具名省份属于 `provincial`；名称和 Evidence 均无明确范围词时必须清空模型推断值，不得保留猜测等级。
5. 同一竞赛、同一层级和同一奖次仅允许一个奖项对象；简介与经历详述重复提及时不得生成副本。不同区域赛、不同阶段或不同奖次仍分别保留。
6. 分类冲突必须在成功前拒绝，并通过有界跨 collection 修复移动对象；不得同时保留副本或依靠下游合并。
7. 项目原文中的排名、准确率、性能提升等成果指标只进入对应经历的 `achievements` 或 `highlights`；没有独立竞赛身份的“排名 2/204”等指标不得作为 `awards` 副本。

## 6. 经历抽取

1. 工作经历必须至少有 `company`；只有原文明示岗位名称时才填写 `position`，否则省略或输出 `null`。项目经历必须至少有 `name`。
2. `responsibilities` 只保存工作动作和职责，`achievements` 只保存结果、指标、交付或影响。
3. 项目 `description` 保存项目目标或整体说明，`highlights` 保存具体动作、结果和亮点。
4. 不得从技能列表推导项目或工作经历，不得把项目经历伪造成工作经历。
5. 项目 `name` 必须是原文中最短且可独立识别该项目的连续名词短语。不得包含项目符号、句末标点，不得使用完整任务句或成果句，也不得只使用项目涉及的单个技能、框架、模型或协议名称；没有显式标题时，应从原文选择同时包含业务对象或项目产出的最小项目识别短语，不得生成新标题。若项目对象及其动作事实有原文支持，但当前 `name` 不受其 name Evidence 支持，必须通过通用语义修复从该 Evidence 中改取合法项目识别短语，不得由 Python 按动作词裁剪，也不得仅因名称错误删除整个项目对象。
6. 分享、活动、讲座或会议名称不是技术项目标识，不得仅因存在参与、复盘或协作动作就创建 `project_experience`。
7. 原文没有学历层级时 `degree=unknown`，不得根据学校、专业或日期猜测学历；该教育特征进入匹配层时保持 `unresolved`。
8. 工作、项目和实践分区中的实质性原文块必须被该分区允许的 entry、职责、成果、项目事实或技术项 Evidence 覆盖。部分遮挡的公司名或项目名不导致整段经历失效；仍有 Evidence 的事实必须保留。
9. 实践、校园、学生工作和社会实践中的项目型对象进入 `project_experience`；组织/机构与任职角色进入 `work_experience`，职位名称不得充当项目名称。
10. “前端实习、研究助理、项目助理、负责人”等角色标题不是项目名；原文明示“博士课题、硕士课题、科研课题”时，实验室或课题组只作为 affiliation，不得据此虚构雇主或雇佣关系。
11. 论文进入 `publications`，明确专利进入 `patents`，科研项目、竞赛、数据集、开源软件、标准和技术报告进入 `research_outputs`。课程作业、普通项目或技能声明不得推断为科研产出；“在投”固定为 `submitted`，不得当作 `published`。作者/发明人顺序和年份只在原文明示时抽取；抽取层不按 venue、顺序或专利状态计算分数。

## 7. 归一化边界

1. 模型只根据通用类型定义输出原始技能名称、`item_type` 和原文熟练度，不接收完整标准技能词表或标准技能 ID；提示词只提供本份原文中精确出现、且最终确定性门槛本来就会检查的 source-local 技能名称与权威类型，提前暴露同一成功条件。
2. 当原始名称精确命中标准词表且只有一个合法类型时，Python 在生成 ID 与 Schema 校验之前执行确定性权威字段校正。
3. Python 在语义校验后再次检查标准词表类型契约；模型输出的冲突类型不得进入成功结果。该过程与 JD 的权威字段校正顺序一致，不属于 MatchFeature 或归一化补救。
4. Python 归一化层只消费类型已经合法的抽取结果，按 `name + item_type` 精确查表；未命中即为 `unresolved`。
5. 禁止近似匹配、自动猜测多义类型或静默使用默认技能 ID。
6. 抽取结果和归一化结果必须分别正式导出，并通过 `document_id`、`source_item_id` 关联。
7. Python 不得依据技能后缀、动作词、熟练度词或样本词组改写技能名、项目名、熟练度或对象归属；抽取错误由同一通用标注规范驱动的有限模型修复处理。业务术语与固定词只允许进入配置或标准词表，不得在抽取脚本中枚举规避。
8. CV 成功路径与 JD 保持相同顺序：模型抽取、Evidence 确定性校正、标准词表唯一类型校正、Schema/Evidence/语义校验、有限局部修复或整体验证重试、归一化。
9. 初次请求会列出最终 section coverage gate 本来就要求引用的 source_id 与允许集合，使模型在第一次抽取时看到同一成功条件；该清单不得放宽分类边界，也不得用于虚构对象。

## 8. MatchFeature 派生约束

1. 期望岗位和历史工作岗位可以生成 `role`；项目内的负责人、小组长、项目角色不得作为岗位族 `role` 特征。
2. 同一标准技能在多个声明区、工作经历或项目中出现时保留各自 Evidence，但通过 `structured_values.aggregation_key` 声明统一聚合键；匹配覆盖度按聚合键去重，证据强度单独统计。
3. 技能、语言、奖项等任何候选等级为 `unknown` 时都不得写入 `candidate_level`；仅显式等级进入该字段。
4. 顶层复合技能的局部修复必须按 `parts` 完整拆分：原对象替换为第一项，其余项按确定性要求逐项 append；追加数量不一致时拒绝修复结果。
5. `personal_info` 是唯一允许进行单例局部修复的对象，只允许整体 replace 或 remove，不允许 append；修复后仍重新执行完整 Schema、Evidence 和语义校验。

## 9. 经历—能力验证派生层

1. 能力验证只消费已经生成的 MatchFeature，不改变抽取事实、标准技能 ID、声明熟练度或 Evidence。
2. 顶层声明技能与工作/项目 `tech_stack` 通过同一 `aggregation_key` 建立关系；每次经历出现分别保留为 `CapabilityEvidenceLink`。
3. 支持等级和置信度只由配置中声明的通用信号计算，包括直接经历出现、任务直接提及该技能、同一直接任务中的量化结果、时间、明确角色和独立经历复现。
4. `demonstrated_level` 是经历证据强度等级，不等同于候选人自述的 `declared_level`；二者必须分别保留。
5. 有经历证据时只提供非负 `evidence_bonus`；没有经历证据时状态为 `not_observed` 且加分为 0，不降分、不否定声明技能。
6. 未归一化技能可保留证据关系供审查，但状态必须为 `unresolved`，匹配加分为 0。
7. 同经历但未直接提及该技能的任务不得写入 `supporting_task_feature_ids`，也不得为该技能贡献量化结果信号；只有技术栈出现而没有直接任务支持时为 `partially_supported`。
8. `support_score` 保存单条经历证据分，`aggregate_support_score` 在不同经历之间聚合；多个直接任务或多个独立经历可以达到 `advanced/优秀`，不得通过同一项目的无关任务堆叠等级。
