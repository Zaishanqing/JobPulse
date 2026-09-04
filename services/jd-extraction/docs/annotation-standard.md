# JD 标注规范 V2

> 文档类型：标准
> 维护状态：active
> 适用范围：`Extraction/jdextraction/`
> 事实源：JD Schema、语义约束、配置与测试
> 责任角色：JD Extraction 维护者
> 最后复核：2026-07-27

## 1. 标注目标

本规范用于将岗位 JD 转换为可审计、可复核的结构化数据，为岗位画像、候选人匹配、能力统计、归一化和抽取质量评测提供统一口径。

V2 采用“原子事实 + 精确 Evidence”的抽取结构，并将数据划分为三个互不混写的层级：

| 层级 | 负责内容 | 不负责内容 |
|---|---|---|
| 抽取层 | 岗位职责、候选人要求、公司事实、雇佣事实、原文 Evidence | 标准技能 ID、岗位族、统计细类、Excel 展平字段 |
| 归一化层 | 标准技能名称与 ID、岗位分类、薪资解析、未归一项 | 改写抽取事实、补充原文没有的信息 |
| 导出层 | JSON、Excel 和数据库宽表 | 反向改变抽取 Schema |

V2 不沿用 V1 的统一 `requirement.items[]` 大宽表。不同语义对象使用不同结构：

| 对象 | 保存内容 |
|---|---|
| `job_title` | 原文明确出现的岗位名称 |
| `responsibilities[]` | 任职者需要执行、交付或负责的原子任务 |
| `requirements[]` | 候选人需要满足的技能、教育、经验、证书、软技能或其他准入条件 |
| `company_facts[]` | 公司自身的行业、规模、业务、产品、团队、资源与资质 |
| `employment_facts[]` | 地点、雇佣方式、办公条件、工作时间、薪酬福利与工作条件 |

## 2. 总体标注原则

### 2.1 以原文为唯一事实来源

所有抽取对象都必须有原文直接支持。不得依靠常识、岗位惯例、公司背景或技能共现关系补充事实。

| 原文 | 正确处理 | 错误处理 |
|---|---|---|
| 熟悉 RAG 系统搭建 | 抽取 `RAG` 及原文明确表达的相关要求 | 自动补充 LangChain、向量数据库、Embedding |
| 有推荐系统经验 | 抽取推荐系统经验 | 自动补充 Python、PyTorch |
| 公司提供五险一金 | 抽取社会保障事实 | 推断住房补贴、商业保险 |
| 熟悉主流数据库 | 保留“主流数据库”这一原文技能表达 | 猜测 MySQL、PostgreSQL |

抽取层允许把原文约束拆成结构化字段，但不得把推断结果伪装成原文事实。

### 2.2 每个对象必须原子化

“原子”是指一个对象只表达一个可独立判断的任务、准入条件或事实。删除该对象时，不应连带删除另一个独立事实。

| 原文 | 正确拆分 |
|---|---|
| 本科及以上，计算机相关专业，3 年以上 Python 开发经验 | education：本科 + 计算机相关专业；experience：3 年 Python 开发；原文未独立表达技能熟练度，不额外派生 skill |
| 负责模型训练、部署和性能优化 | 三个 task：模型训练；模型部署；性能优化 |
| 熟悉 Python、Java 和 Go | 一个 skill requirement，包含三个共享同一强度和熟练度的 SkillItem |
| 熟悉 Python，Java 经验优先 | 两个 skill requirement，因为 modality 不同 |

以下情况必须拆成不同 requirement：

1. `kind` 不同，例如学历与经验、经验与技能。
2. `modality` 不同，例如必须与优先、必须与加分。
3. 熟练度不同，例如“精通 Python，了解 Go”。
4. 年限、方向或岗位约束属于不同作用域；同一年限直接修饰的方向或岗位必须保持绑定。
5. 同一句中包含多个独立职责动作。

同一证据可以支持不同 `kind` 的原子对象，这不等于重复抽取。

### 2.3 职责与候选人经历严格分离

职责描述任职后需要做什么；经验描述候选人在入职前已经具备什么经历。

| 原文 | 对象 | 原因 |
|---|---|---|
| 负责大模型应用开发 | `responsibilities.task` | 任职后的工作动作 |
| 有大模型应用开发经验 | `requirements.experience` | 候选人既有经历 |
| 能够独立完成模型部署 | 通常为 `requirements.skill` 或 `other` | 表达候选人准入能力，不是直接陈述岗位职责 |
| 入职后负责模型部署 | `responsibilities.task` | 明确是工作内容 |

不得因为职责中出现“开发、部署、优化”等动作，就推断候选人已经有对应经验。

### 2.4 Evidence 必须精确、连续、同源

每个 requirement、fact 和有值的 `job_title` 都必须带 `evidence`。

模型负责：

| 字段 | 规则 |
|---|---|
| `source_id` | 必须是输入中真实存在的 source block ID |
| `quote` | 必须逐字存在于该 source block 的 `text` 中 |

Python 负责：

| 字段 | 规则 |
|---|---|
| `start`、`end` | 根据 quote 的精确位置确定 |
| `alignment` | 正式结果必须为 `exact` |
| `occurrence_index` | 同一 quote 多次出现时确定具体出现位置 |

禁止以下操作：

- 改写原文、纠正错别字或替换同义词；
- 改动空格、大小写、数字或标点；
- 跨 source block 拼接 Evidence；
- 用省略号删除 quote 中间文字；
- 为了缩短证据而拼接首尾片段；
- Evidence 无法对齐时改用模糊匹配。

Evidence 无法精确对齐时，整条 JD 进入硬失败，不进入正式结果。

抽取前流水线会先执行正式清洗（`src/text_cleaning.py`）：Unicode NFKC 归一化并移除来源平台插入的“来自BOSS直聘”“kanzhun”及中文词内 `boss`/`直聘` 残片，同时保留合法的 `BOSS直聘`、`BOSS主页` 品牌词。模型只看到清洗后的 source blocks，`evidence.quote` 必须逐字存在于清洗后文本的对应 block。抽取后校验仍保留：若模型输出仍把平台插入词写入 `job_title.value`、`action`、Fact `value`、`SkillItem.name` 或其他语义字段，验证器返回确定性语义硬错误。职位标题只进入 `job_title`，不得以相同 source、quote 和文字再次建立 responsibility。

流水线可将 Schema、Evidence 或确定性语义门禁错误反馈给模型，要求从 source blocks 完整重抽，默认最多重试两次。Evidence 校验必须在一轮内收集并返回全部错误对象，避免逐个暴露错误耗尽重试次数；后续每次重抽必须同时携带本条 JD 的累计校验历史，防止修复新错误时重新引入已经修正的问题。重试不是后处理修补：任何一次输出仍必须独立通过全部校验，失败结果不得进入正式数据。

### 2.5 Modality 与 Proficiency 分开判断

`modality` 表示筛选强度；`proficiency` 只表示技能熟练程度，二者不能互相替代。

`modality` 合法取值：

| 取值 | 判定口径 | 常见原文 |
|---|---|---|
| `required` | 明确必须，或位于任职要求中的默认准入条件 | 必须、要求、应具备、需要、熟悉 Python |
| `preferred` | 满足更佳或优先考虑，但不是明确加分项 | 优先、有经验者优先、具备更佳 |
| `bonus` | 原文明示为加分项或加分能力 | 加分项、可加分、额外加分 |
| `unknown` | 缺少上下文，无法稳定判断强度 | 只有孤立技能标签或不完整短语 |

判定优先级：

1. 原文明示“加分项”时为 `bonus`。
2. 原文明示“优先、具备更佳”时为 `preferred`。
3. “技能要求”“任职要求”“学历”“经验要求”等明确准入字段标题下的默认条件为 `required`；不得因为句子省略“必须”而标为 `unknown`。
4. 无法判断时才使用 `unknown`，不得为减少 review flag 而猜测。

来源冲突规则：同一结构条件同时出现在通用字段标签和完整正文中，且 modality 不一致时，带“必须、优先、加分”等显式强度词的完整正文高于默认字段标签。例如“学历:硕士”与“硕士优先”同时出现时，只保留正文支持的 `preferred`，不得输出 required/preferred 两份冲突对象。

`proficiency` 仅适用于 `SkillRequirement`：

| 取值 | 常见原文 |
|---|---|
| `know` | 了解、接触过、知晓 |
| `familiar` | 熟悉、掌握、能够使用 |
| `proficient` | 熟练、熟练掌握、熟练使用 |
| `expert` | 精通、深入理解、专家级 |
| `unknown` | 原文存在熟练度表达，但无法稳定映射到其他四档 |
| `null` | 原文完全没有熟练度表达 |

### 2.6 缺省值和空值必须统一

V2 不使用“未提及”字符串填充抽取 Schema。

数组字段没有内容时只能输出 `[]`，禁止输出 `null`。

| 字段类型 | 没有内容时的处理 |
|---|---|
| 数组 | 必须为 `[]`，禁止 `null` |
| Schema 允许为空的标量或对象 | 使用 `null` 或省略可选字段 |
| 必填字符串 | 必须是非空原文值，禁止空字符串 |
| 没有对应事实 | 不生成该 requirement 或 fact |

例如：

- 没有专业限制：`majors=[]`。
- 没有院校限制：`school_constraints=[]`。
- 没有岗位标题原文：`job_title=null`。
- 没有证书要求：不生成 certificate requirement，不能输出 `certificates=[]`。

### 2.7 抽取层不得承担归一化

抽取模型不得输出：

- `normalized`、`canonical_name`；
- 标准技能 ID；
- `job_family`；
- `category_code`、`subcategory_code`；
- Excel sheet 或宽表字段；
- 模型自造的 requirement ID、fact ID、字符区间和 alignment。

技能原文名称无法命中词表时，由归一化层标记 `unresolved`，不得由模型猜测标准名称。

## 3. 输出结构

### 3.1 抽取层完整结构

正式抽取结果遵循以下结构。模型输出时不包含由 Python 生成的 ID 和 Evidence 位置字段。

```json
{
  "document_id": "jd_000001",
  "job_title": {
    "value": "大模型算法工程师",
    "evidence": {
      "source_id": "src_0001",
      "quote": "大模型算法工程师",
      "start": 0,
      "end": 9,
      "alignment": "exact",
      "occurrence_index": 0
    }
  },
  "responsibilities": [],
  "requirements": [],
  "company_facts": [],
  "employment_facts": []
}
```

### 3.2 字段责任

| 内容 | 模型 | Python 后处理 | 归一化层 |
|---|---:|---:|---:|
| 原子 kind、modality、value、结构化约束 | 是 | 校验 | 否 |
| Evidence source_id、quote | 是 | 精确校验 | 否 |
| document_id、requirement_id、fact_id | 否 | 是 | 否 |
| start、end、alignment、occurrence_index | 否 | 是 | 否 |
| 标准名称、技能 ID、岗位分类 | 否 | 否 | 是 |
| 薪资结构化解析 | 否 | 否 | 是 |

## 4. JD 级字段

### 4.1 document_id

由 Python 根据输入 `jd_id` 确定。模型不得生成或修改。

### 4.2 job_title

当前版本仅当岗位名称出现在输入 `source_blocks` 中时抽取。独立岗位标题列只作为定位 hint；如果该标题没有进入 source block，就不能作为事实来源。

| 字段 | 含义 |
|---|---|
| `value` | 原文岗位名称，不做归一化 |
| `evidence` | 岗位名称对应的精确原文证据 |

不得根据职责或技能列表推断岗位名称。输入 hint 只能帮助定位，不能替代原文 Evidence；无法为标题提供合法 `source_id + quote` 时必须输出 `job_title=null`。

## 5. Responsibilities 标注规则

每个 `TaskRequirement` 表示一个岗位职责动作。

| 字段 | 类型 | 规则 |
|---|---|---|
| `requirement_id` | string | Python 生成 |
| `kind` | `task` | 固定值 |
| `modality` | enum | 通常为 `required`；原文明示优先或加分时按原文 |
| `action` | string | 原子任务动作，忠实于原文，不补充执行方式或目的 |
| `evidence` | Evidence | 支持该任务的连续原文 |

常见职责触发词：负责、参与、承担、完成、设计、开发、构建、维护、部署、优化、研究、分析、推进、交付。

| 原文 | 正确标注 |
|---|---|
| 负责模型训练与部署 | task：模型训练；task：模型部署 |
| 参与数据清洗和标注体系建设 | task：数据清洗；task：标注体系建设 |
| 有模型部署经验 | 不进入 responsibilities，标为 experience |
| 熟悉模型部署流程 | 不进入 responsibilities，标为 skill 或 other |

即使原文把内容误放在“岗位职责”标题下，只要语义以“熟悉、掌握、精通、具备、会使用、了解”等候选人能力要求开头，仍应进入 requirements，不得机械按标题标成 task。

`action` 可以对原文做最小结构化摘取，但不得改变动作主体、目标或强度。

## 6. Candidate Requirements 标注规则

候选人要求共同字段：

| 字段 | 规则 |
|---|---|
| `requirement_id` | Python 生成，同一 JD 内唯一 |
| `kind` | 从 skill、education、experience、certificate、soft_skill、other 中选择 |
| `modality` | required、preferred、bonus、unknown |
| `evidence` | 精确连续原文 |

### 6.1 SkillRequirement

用于表达候选人需要掌握、了解或使用的技术实体、方法与领域知识。

| 字段 | 类型 | 规则 |
|---|---|---|
| `items` | `SkillItem[]` | 至少一个；共享同一 modality 和 proficiency 的并列技能可合并 |
| `proficiency` | enum 或 null | 只按原文熟练度填写 |

#### SkillItem.item_type

| item_type | 定义 | 示例 |
|---|---|---|
| `programming_language` | 编程、脚本或查询语言 | Python、Java、C++、SQL、Shell |
| `framework` | 提供应用骨架、运行范式或完整开发框架 | Django、Spring、React、PyTorch |
| `library` | 被程序调用的库、SDK 或较轻量代码依赖 | NumPy、Pandas、Transformers SDK |
| `database` | 数据库、向量库、缓存、搜索或消息存储系统 | MySQL、Redis、Milvus、Elasticsearch |
| `tool` | 具体开发、部署、协作、标注或 AI 辅助工具 | Git、Docker、Postman、Cursor |
| `platform` | 云平台、业务平台、操作系统或运行环境 | AWS、阿里云、Linux、Hugging Face |
| `methodology` | 算法、模型方法、训练方法、分析方法、工程策略 | RAG、LoRA、量化、A/B 测试 |
| `domain_knowledge` | 理论、技术领域、行业或业务知识 | 机器学习、NLP、金融风控 |
| `other` | 明确是技能实体但无法稳定归入以上类型 | 主流大模型工具 |

边界规则：

1. SQL 是 `programming_language`，MySQL 是 `database`。
2. PyTorch 按当前 Schema 标为 `framework`。
3. RAG、微调、量化是 `methodology`，不是 tool。
4. Linux 是 `platform`，Shell 是 `programming_language`。
5. “模型部署”作为能力短语时不能把“部署”伪造成具体工具；原文出现 Docker 时再单独抽取 tool。
6. 不得从“熟悉深度学习”派生 PyTorch、TensorFlow。
7. `C/C++`、`TensorFlow/PyTorch`、`Spark/Hadoop/Hive` 等并列技术必须拆成多个 SkillItem；`CI/CD`、`TCP/IP` 等不可拆的固定术语除外。
8. 名称包含“开发经验、项目经验、架构经验”等经历语义时，应建立 ExperienceRequirement，不得作为 SkillItem。
9. “图像处理库(OpenCV 等)”这类“类别（具体示例等）”表达，只抽取原文明示的具体技术实体 `OpenCV`；不得把整段类别说明作为一个 SkillItem，也不得补出未出现的其他库。
10. “和/或”属于自然语言选择连接结构，不按 `/` 机械拆分；拆分后的每个 SkillItem 必须语义完整，不得出现“用英文和”“或繁体中文写提示词”等悬空连词残片。

### 6.2 EducationRequirement

用于学历、专业、院校、招生类型、毕业年份和学生届次。

| 字段 | 类型 | 规则 |
|---|---|---|
| `minimum_degree` | enum 或 null | high_school、associate、bachelor、master、doctor、unknown |
| `majors` | string[] | 原文明确专业；无专业限制时 `[]` |
| `school_constraints` | string[] | 985、211、双一流、海外名校等原文限制；无则 `[]` |
| `admission_type` | enum 或 null | unified、non_unified、unknown |
| `graduation_year` | integer 或 null | 原文明确毕业年份 |
| `student_cohort` | string 或 null | 应届生、2026 届、在校生等原文表达 |

拆分原则：

- 同一 Evidence、同一 modality 下共同构成一个筛选条件的学历、专业和院校约束，合并在一个 education requirement 中，例如“本科及以上，计算机相关专业”。
- modality 不同、Evidence 不同，或原文将条件明确列为相互独立要求时，拆成不同 education requirement。
- 同一原文不得在“合并”和“拆分”之间任意选择；先按上述 Evidence、modality 和条件作用域规则确定唯一结果。
- “学历不限”使用 `minimum_degree=unknown` 不能表达“不限”，应根据当前 Schema 使用 `other` 明确保留“学历不限”，不得伪造学历下限。
- “本科或硕士”不是最低学历表达时，不得擅自等价为本科及以上。

### 6.3 ExperienceRequirement

用于候选人已经具备的工作、项目、实习、科研、管理或特定方向经历及年限。

| 字段 | 类型 | 规则 |
|---|---|---|
| `minimum_years` | number 或 null | 最低年限 |
| `maximum_years` | number 或 null | 最高年限 |
| `domain` | string 或 null | 经验方向或领域，如大模型应用、金融风控 |
| `role` | string 或 null | 经历中的岗位或角色，如算法工程师、团队负责人 |
| `duration_text` | string 或 null | 需要保留的原文时长表达 |
| `experience_unlimited` | boolean | 原文明示经验不限时为 true |

年限规则：

| 原文 | minimum_years | maximum_years | duration_text |
|---|---:|---:|---|
| 3 年以上开发经验 | 3 | null | 3 年以上 |
| 3—5 年开发经验 | 3 | 5 | 3—5 年 |
| 不超过 5 年 | null | 5 | 不超过 5 年 |
| 半年以上实习经验 | 0.5 | null | 半年以上 |
| 经验不限 | null | null | 可保留“不限” |

规则：

1. `minimum_years` 不能大于 `maximum_years`。
2. `experience_unlimited=true` 时不能同时填写年限上下界。
3. “熟悉 Python”不是 Python 经验；只有原文明示“经验、经历、做过、参与过”才抽取 experience。
4. “负责推荐系统开发”是 task，不是候选人项目经验。
5. 论文、开源项目、竞赛经历是否属于 experience 或 certificate，按第 9 章边界判断。
6. 同一年限直接修饰的 `domain` 或 `role` 必须保存在同一个 ExperienceRequirement 中，例如“3 年以上大模型开发经验”不能拆成无方向的“3 年经验”和无年限的“大模型经验”。
7. 总工作年限与方向年限不同必须拆分，例如“3 年工作经验，其中 1 年大模型经验”拆成两个 ExperienceRequirement。
8. 多个方向共享同一个明确年限且属于同一并列条件时，应按原文作用域分别建立 requirement，并保留相同年限；不得生成一个失去方向绑定的总括年限对象。
9. 没有明确年限时，只要原文存在经历方向，仍必须写入 `domain` 或 `role`；不得生成仅有 Evidence、其余经验结构字段全空的 ExperienceRequirement。

### 6.4 CertificateRequirement

用于证书、执照、考试认证、职业资质以及明确作为资质要求的奖项。

| 字段 | 类型 | 规则 |
|---|---|---|
| `certificates` | string[] | 至少一个，保留原文证书或资质名称 |

| 原文 | 标注 |
|---|---|
| 通过英语六级 | certificate：英语六级 |
| 持有 PMP 证书 | certificate：PMP |
| AWS 认证优先 | certificate，modality=preferred |
| 有竞赛经验 | 通常为 experience |
| 获 ACM 金牌 | certificate；如果强调参赛经历，可另建 experience |

不得把“英语读写能力”标成证书；这属于 skill 或 other。

### 6.5 SoftSkillRequirement

用于沟通、协作、学习、逻辑、责任心、抗压、表达等行为特质。

| 字段 | 类型 | 规则 |
|---|---|---|
| `skills` | string[] | 至少一个；共享同一 modality 的并列软技能可合并 |

| 原文 | 正确处理 |
|---|---|
| 具备良好的沟通和团队协作能力 | soft_skill：沟通能力、团队协作能力 |
| 能独立解决复杂技术问题 | 如果强调行为能力可标 soft_skill；若强调具体技术任务，优先 task 或 other |
| 熟悉团队协作工具 | skill.tool，不是 soft_skill |

### 6.6 OtherRequirement

OtherRequirement 只用于现有结构确实无法忠实表达的候选人准入条件。原文明示熟悉、掌握、精通或会使用的软件、工具、平台、框架、语言、协议、数据库、算法或模型时，必须建立 SkillRequirement 并保留原文技术实体，不得用 OtherRequirement 包裹明确技术能力。

仅用于原文明确属于候选人准入条件，但现有 kind 无法忠实表达的情况。

| 字段 | 类型 | 规则 |
|---|---|---|
| `label` | string | 稳定描述该条件的原文类别，不得自造新枚举 |
| `value` | string 或 null | 原文中的具体值或说明 |

适用例子：

- 可接受夜班；
- 能接受出差；
- 学历不限；
- 需要提供作品集；
- 原文明确的年龄、语言读写或其他无法进入现有结构的条件。

不得为了省事把可稳定归入 skill、education、experience、certificate 或 soft_skill 的内容放入 other。

## 7. CompanyFact 标注规则

CompanyFact 只描述公司自身，不描述岗位要求或员工福利。“你将”“你会”“你可以”等面向候选人的工作关系、机会或条件不是公司自身事实，不得放入 CompanyFact；应按语义进入 requirement、EmploymentFact，或在当前 Schema 无法忠实表达时不抽取。

发展通道、晋升通道、职业发展机会同样属于候选人的雇佣条件，不是 CompanyFact。

共同字段：`fact_id` 由 Python 生成；模型只输出 `kind`、`value`、`evidence`。不得额外输出 `label`。

| kind | 定义 | 示例 |
|---|---|---|
| `company_name` | 公司名称 | 某某科技有限公司 |
| `industry` | 所属行业 | 金融科技、医疗健康 |
| `development_stage` | 融资或发展阶段 | A 轮、上市公司、创业阶段 |
| `company_size` | 人员或组织规模 | 500 人以上 |
| `business_domain` | 公司业务领域 | 企业服务、智能驾驶 |
| `product_service` | 明确产品或服务 | 风控平台、SaaS 产品 |
| `target_market` | 客户、用户或市场 | 海外市场、金融机构客户 |
| `team` | 团队构成或团队背景 | 核心团队来自头部互联网公司 |
| `technical_resource` | 公司拥有的技术、数据、算力或研发资源 | 自研大模型、GPU 集群 |
| `qualification` | 公司资质、荣誉、认证 | 国家高新技术企业 |
| `other` | 明确的其他公司事实 | 无法稳定归入以上 kind 的公司属性 |

边界：

- “提供培训”是 employment `training`，不是 company `team`。
- “拥有专业培训团队”如果描述公司组织，可为 company `team`；如果表达员工福利，则为 employment `training`。
- “公司位于上海”可以作为 company 事实理解，但岗位工作地点应进入 employment `location`；当前以招聘条件为目标时优先后者。

## 8. EmploymentFact 标注规则

EmploymentFact 描述岗位的雇佣、办公、时间、薪酬福利与工作条件。

| kind | 定义 | 示例 |
|---|---|---|
| `location` | 工作地点 | 上海市浦东新区 |
| `employment_type` | 全职、兼职、实习、劳务等 | 全职、实习 |
| `work_mode` | 现场、远程、混合办公 | 可远程、居家办公 |
| `work_schedule` | 班次、工时、工作日 | 周末双休、弹性工作制 |
| `social_security` | 社保、公积金等 | 五险一金、六险二金 |
| `allowance` | 通用补贴 | 交通补贴、通讯补贴 |
| `meal` | 餐饮福利 | 免费午餐、餐补 |
| `accommodation` | 住宿福利 | 提供宿舍、住房补贴 |
| `health_check` | 体检或健康福利 | 年度体检 |
| `team_activity` | 团建与员工活动 | 定期团建、旅游 |
| `leave` | 休假制度 | 带薪年假、生日假 |
| `bonus` | 奖金 | 年终奖、项目奖金 |
| `commission` | 提成 | 销售提成、绩效提成 |
| `equity` | 股权或期权 | 股票期权 |
| `training` | 员工培训与培养 | 导师制、专业培训 |
| `equipment` | 办公设备 | 配备 MacBook、电脑补贴 |
| `other` | 其他明确工作条件或福利 | 无法归入以上 kind 的雇佣事实 |

规则：

1. 每个 fact 只表达一个原子福利或条件。
2. “五险一金、年度体检、带薪年假”拆成三个 EmploymentFact。
3. 普通基础薪资范围不生成 EmploymentFact，只由归一化层的 salary parser 解析；年终奖、项目奖金、提成和股权分别使用 `bonus`、`commission`、`equity`。
4. “技术氛围好、发展空间大”等宣传性空泛描述，不能稳定映射时不要强行生成多个事实。

## 9. 易混边界规则

### 9.1 Skill、Task 与 Experience

| 判断问题 | 是 | 否 |
|---|---|---|
| 原文是否描述入职后要完成的动作？ | task | 继续判断 |
| 原文是否要求候选人已有经历？ | experience | 继续判断 |
| 原文是否要求掌握技术、方法、工具或知识？ | skill | other 或其他 kind |

示例：

- “负责构建 RAG 系统” → 只建立 task；仅出现技术名不构成独立候选人技能要求。
- “有 RAG 系统构建经验” → 只建立 experience；不能从经历要求派生独立 skill。
- “熟悉 RAG 原理” → skill(methodology/domain_knowledge)，不建 task 或 experience。

只有原文同时明确表达“熟悉、掌握、精通、能够使用”等技能准入语义时，才建立 SkillRequirement。技术名出现在职责、项目经历或公司介绍中，不足以单独证明候选人技能要求。

### 9.2 Framework、Library、Tool 与 Platform

| 类型 | 核心判断 |
|---|---|
| framework | 提供应用骨架、运行范式或完整开发框架 |
| library | 被代码调用的依赖、库或 SDK |
| tool | 主要作为具体软件或工程工具使用 |
| platform | 提供运行环境、托管能力或业务平台 |

同一产品在不同上下文可能角色不同，应按 JD 表达的使用方式标注，不按品牌永久固定。

### 9.3 Methodology 与 Domain Knowledge

- 能够作为具体算法、训练、评测或工程方法执行：优先 `methodology`。
- 表达对理论、领域或业务的理解：优先 `domain_knowledge`。
- “深度学习”缺少动作上下文时通常为 domain knowledge；“使用深度学习方法进行分类”可同时存在 methodology 和 task，但不得凭空拆出具体模型。

### 9.4 Education 与 Experience

- 学历、专业、院校、届次进入 education。
- 科研、实习、工作、项目经历进入 experience。
- “计算机专业背景”如果指所学专业进入 education；如果泛指知识背景且没有学历语义，可为 skill(domain_knowledge) 或 other。

### 9.5 Certificate 与 Experience

- 证书、认证、执照、明确奖项进入 certificate。
- 参加竞赛、发表论文、参与开源项目的过程进入 experience。
- “获得竞赛一等奖”可以生成 certificate；若原文同时强调竞赛项目经验，可另建 experience，共享 Evidence 是允许的。

### 9.6 CompanyFact 与 EmploymentFact

- 描述公司是谁、做什么、拥有什么：CompanyFact。
- 描述员工在哪里工作、如何雇佣、获得什么条件：EmploymentFact。
- “公司有健身房”如果只是公司设施，可为 company other；如果作为员工福利宣传，优先 employment other。

### 9.7 Preferred 与 Bonus

- “优先、有经验者优先、具备更佳”通常为 `preferred`。
- “加分项、额外加分、可加分”才使用 `bonus`。
- 不得把所有“优先”统一改为 bonus。

## 10. 重复、冲突与人工复核

以下规则先生成结构化 review flag。`unknown_modality` 始终保留为软复核；只有能够通过重新抽取客观修正的结构问题才提升为重试门禁。系统不自动删除跨 requirement 内容：

| issue_type | 触发条件 |
|---|---|
| `duplicate_skill_in_requirement` | 同一 skill requirement 内 name 与 item_type 完全重复 |
| `unknown_modality` | modality 为 unknown |
| `empty_structured_constraint` | education 或 experience 没有任何有效结构字段 |
| `duplicate_requirement_semantics` | kind、结构化语义和 modality 相同 |
| `conflicting_requirement_modality` | 结构化语义相同但 modality 不同 |
| `overlapping_requirement_evidence` | 同一 source block 的 skill requirements 共享技能项，且 Evidence 存在包含关系 |

语义重复使用确定性指纹判断：忽略 ID 和 Evidence，规范化 Unicode、大小写、空白；SkillItem 顺序不影响指纹。

`unknown_modality` 保持软复核，因为原文本身可能确实缺少强度上下文；重复、冲突、空结构和 Evidence 重叠不得作为“成功但待人工处理”的结果直接输送下游。

## 11. 完整示例

原文：

> 大模型算法工程师。负责 RAG 系统开发和模型部署。要求本科及以上学历，熟练使用 Python，有 3 年以上大模型应用经验；熟悉 LangChain 者优先。公司专注企业智能服务，提供五险一金和年度体检。

关键拆分：

| 对象 | kind | modality | 内容 |
|---|---|---|---|
| responsibility | task | required | RAG 系统开发 |
| responsibility | task | required | 模型部署 |
| requirement | education | required | minimum_degree=bachelor |
| requirement | skill | required | Python，proficiency=proficient |
| requirement | experience | required | minimum_years=3，domain=大模型应用 |
| requirement | skill | preferred | LangChain |
| company fact | business_domain | — | 企业智能服务 |
| employment fact | social_security | — | 五险一金 |
| employment fact | health_check | — | 年度体检 |

不得额外生成 PyTorch、向量数据库、Docker、岗位族或标准技能 ID。

## 12. AI 预标注校验清单

### 12.1 结构检查

- [ ] 只输出一个合法 JSON object。
- [ ] 没有 Schema 外字段。
- [ ] responsibilities 中只有 task。
- [ ] requirements 中没有 task。
- [ ] 数组无内容时使用 `[]`，没有输出 `null`。
- [ ] 必填字符串非空。
- [ ] certificate、soft_skill、skill 的必填数组至少包含一项。

### 12.2 Evidence 检查

- [ ] 每个 requirement、fact 和非空 job_title 都有 Evidence。
- [ ] source_id 真实存在。
- [ ] quote 是对应 source block 的精确连续子串。
- [ ] 没有改词、改标点、跨 block、拼接或省略。
- [ ] 模型没有输出 ID、start、end、alignment、occurrence_index。

### 12.3 语义检查

- [ ] 每个对象能够独立判断。
- [ ] 不同 kind、modality 或 proficiency 已正确拆分。
- [ ] 职责没有被标成候选人已有经验。
- [ ] 经验没有被标成入职后职责。
- [ ] skill item_type 使用受控枚举。
- [ ] preferred 与 bonus 没有混用。
- [ ] other 仅用于现有 kind 无法忠实表达的明确准入条件。
- [ ] CompanyFact 与 EmploymentFact 边界正确。

### 12.4 非推断检查

- [ ] 没有补充原文未出现的技能、工具、证书或福利。
- [ ] 没有生成 normalized、标准技能 ID、job_family 或统计细类。
- [ ] 模糊表达保持原文粒度，没有擅自具体化。
- [ ] Evidence 无法精确对齐时已硬失败，没有使用模糊兜底。

## 13. 仲裁原则

出现难例时按以下顺序处理：

1. 先判断内容属于职责、候选人要求、公司事实还是雇佣事实。
2. 再判断是否能拆成多个独立原子对象。
3. 候选人要求按最具体且能被当前 Schema 忠实表达的 kind 标注。
4. 只依据原文判断 modality 和 proficiency。
5. 无法精确对齐 Evidence 时硬失败。
6. Schema 无法表达但原文明确的准入条件才使用 other。
7. 无法稳定判断强度时使用 unknown 并进入人工复核。
8. 不新增字段、不扩枚举、不通过后处理改写模型违约结果；通用新需求先更新 Schema 和规则，再重新运行。

代码权威来源为 `src/models.py`；语义配置权威来源为 `config/model_semantic_rules.yaml`。当本文与代码不一致时，应先停止标注并修正规范或 Schema，不得由标注人员自行选择口径。
