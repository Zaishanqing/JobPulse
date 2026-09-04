from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache

from .models import JDExtractionResult
from .semantic_rules import compile_semantic_handbook


def build_model_output_schema() -> dict:
    schema = deepcopy(JDExtractionResult.model_json_schema())
    schema["properties"].pop("document_id", None)
    schema["properties"].pop("requirement_graph", None)
    schema["required"] = [
        name for name in schema.get("required", []) if name != "document_id"
    ]
    for definition_name in (
        "TaskRequirement",
        "SkillRequirement",
        "ToolRequirement",
        "EducationRequirement",
        "ExperienceRequirement",
        "CertificateRequirement",
        "SoftSkillRequirement",
        "OtherRequirement",
    ):
        definition = schema["$defs"][definition_name]
        definition["properties"].pop("requirement_id", None)
        definition["required"] = [
            name for name in definition.get("required", []) if name != "requirement_id"
        ]
    for definition_name in ("CompanyFact", "EmploymentFact"):
        definition = schema["$defs"][definition_name]
        definition["properties"].pop("fact_id", None)
        definition["required"] = [
            name for name in definition.get("required", []) if name != "fact_id"
        ]
    evidence = schema["$defs"]["Evidence"]
    for field_name in ("start", "end", "alignment", "occurrence_index"):
        evidence["properties"].pop(field_name, None)
        evidence["required"] = [
            name for name in evidence.get("required", []) if name != field_name
        ]
    return schema


@lru_cache(maxsize=1)
def build_system_prompt() -> str:
    schema = json.dumps(
        build_model_output_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    handbook = compile_semantic_handbook()
    return f"""你是 JD 原子事实抽取器。只负责忠实抽取原文，不负责归一化、岗位分类、统计分类或导出展示。

# 输出契约

1. 只输出一个符合 JSON Schema 的 JSON object，不输出 Markdown、解释、注释或额外字段。
2. responsibilities 只保存岗位职责；requirements 只保存候选人准入条件。
3. 每个对象必须是可独立判断的原子语义对象。
4. 每个 requirement/fact 必须给 evidence.source_id 和 evidence.quote；quote 必须逐字存在于对应 source block。
5. 不得修改 quote 的文字、空格或标点，不得跨 block，不得拼接证据。
6. 不输出 ID、字符区间、alignment、标准名称、标准 ID、岗位族、统计分类或置信度。
7. 数组字段没有内容时只能输出 []，禁止输出 null；只有 Schema 明确允许 null 的标量或对象字段才能输出 null。
8. CompanyFact 和 EmploymentFact 只能输出 Schema 声明的 kind、value、evidence，不得增加 label 等额外字段。
9. SkillItem.name 必须是单个原子技能；C/C++、TensorFlow/PyTorch、Spark/Hadoop/Hive 等必须拆成多个 SkillItem，CI/CD、TCP/IP 等固定术语除外。
10. 含开发经验、项目经验、架构经验等经历语义的内容必须进入 ExperienceRequirement，不得作为 SkillItem.name。
11. “图像处理库(OpenCV等)”这类“类别(具体示例等)”不得整体作为 SkillItem.name；只抽取原文明示的具体技术实体，例如 OpenCV。
12. “和/或”是自然语言连接结构，不是技能分隔符；不得产生以“和”结尾或以“或”开头的残缺 SkillItem.name。
13. SkillItem.name 必须是可跨 JD 复用的具名技术、方法或知识领域；“相关场景”“相关产品或框架的理解”“某某能力”“某某技术栈”等描述性短语必须改为 soft_skill、experience 或 other，不得伪造成技能实体。
14. OtherRequirement 仅用于现有结构无法忠实表达的准入条件；开发、完善、推动、交付、指导等工作动作进入 responsibilities，明确技术能力优先使用 skill、experience 或 soft_skill。

# 语义规则

{handbook}

# JSON Schema

{schema}
"""


def build_user_prompt(jd_input: dict) -> str:
    blocks = [
        {"source_id": block["source_id"], "text": block["text"]}
        for block in jd_input["source_blocks"]
    ]
    return f"""抽取以下 JD。每个 evidence.quote 必须原样复制其 source_id 对应的 text。

document_id: {jd_input["jd_id"]}
job_title_hint: {jd_input["job_title_raw"]}
company_hint: {jd_input["company"]}
source_blocks: {json.dumps(blocks, ensure_ascii=False)}
"""


def build_validation_retry_prompt(
    jd_input: dict,
    error_type: str,
    error_details: object,
    previous_invalid_output: str | None = None,
    validation_history: list[dict] | None = None,
) -> str:
    original = build_user_prompt(jd_input)
    details = json.dumps(
        error_details, ensure_ascii=False, separators=(",", ":"), default=str
    )
    history = validation_history or [
        {"error_type": error_type, "error_details": error_details}
    ]
    evidence_instruction = ""
    if any(item.get("error_type") == "SourceBindingError" for item in history):
        evidence_instruction = """
这是 Evidence 复制错误。error_details 会一次列出本轮发现的全部错误对象；每项 exact_source_text 是该对象唯一合法原文：
- quote 必须直接复制其中的连续字符，保留所有空格、英文/中文标点和句首编号；
- 立即在上一轮 JSON 中定位 source_id 与 invalid_quote 同时匹配的对象，把该对象的 quote 改为 exact_source_text；
- 同时删除该对象 value/action/domain 等语义字段中不受 exact_source_text 支持的相邻 block 内容，禁止只改 quote 而保留跨 block 拼接语义；
- 如果整个 source block 支持该对象，可以直接把 exact_source_text 完整复制为 quote；
- 如果原文不支持该对象，则不要生成该对象，禁止改写原文来保留对象。
"""
    semantic_instruction = ""
    semantic_errors = [
        detail
        for history_item in history
        if history_item.get("error_type") == "SemanticValidationError"
        for detail in (
            history_item.get("error_details")
            if isinstance(history_item.get("error_details"), list)
            else [history_item.get("error_details")]
        )
        if isinstance(detail, dict)
    ]
    if semantic_errors:
        codes = {item.get("code") for item in semantic_errors}
        issue_types = {item.get("issue_type") for item in semantic_errors}
        instructions: list[str] = []
        if "composite_skill_item" in codes:
            instructions.append(
                "composite_skill_item: 按 violations.parts 将复合名称拆成多个独立 SkillItem；"
                "例如 C/C++ 拆为 C 与 C++，TensorFlow/PyTorch 拆为 TensorFlow 与 PyTorch。"
            )
        if "experience_phrase_in_skill_item" in codes:
            instructions.append(
                "experience_phrase_in_skill_item: 含开发经验、项目经验、架构经验等经历语义的对象改为 ExperienceRequirement；"
                "仅把原文明示掌握的技术实体保留为 SkillItem。"
            )
        if "category_with_parenthetical_examples_in_skill_item" in codes:
            instructions.append(
                "category_with_parenthetical_examples_in_skill_item: 不要把“类别(示例等)”整体作为技能名；"
                "按 violations.examples 仅保留原文明示的具体技术实体，例如图像处理库(OpenCV等)应抽取 OpenCV。"
            )
        if "dangling_conjunction_skill_item" in codes:
            instructions.append(
                "dangling_conjunction_skill_item: 重新理解“和/或”等自然语言连接结构，"
                "输出语义完整的原子能力名称；禁止保留以“和”结尾或以“或”开头的残片。"
            )
        if "descriptive_skill_item" in codes:
            instructions.append(
                "descriptive_skill_item: 删除描述性的 SkillItem；若原文强调行为能力则改为 SoftSkillRequirement，"
                "若表达经历则改为 ExperienceRequirement，只有 Schema 无法忠实表达的准入条件才使用 OtherRequirement；"
                "不得从原句自行概括或虚构一个技能名。"
            )
        if "technical_requirement_in_other" in codes:
            instructions.append(
                "technical_requirement_in_other: 原文明示熟悉、掌握、精通或会使用的软件、工具、平台、"
                "框架、语言、协议、数据库、算法或模型必须改为 SkillRequirement，并拆出原文明示的技术实体；"
                "不得用 OtherRequirement 包裹明确技术能力。"
            )
        if "non_atomic_employment_fact" in codes:
            instructions.append(
                "non_atomic_employment_fact: 每一种福利分别生成一个 EmploymentFact，并选择对应 kind。"
            )
        if "base_salary_in_employment_fact" in codes:
            instructions.append(
                "base_salary_in_employment_fact: 删除基础薪资 Fact；基础薪资只由 Python salary parser 处理。"
            )
        if "candidate_constraint_in_employment_fact" in codes:
            instructions.append(
                "candidate_constraint_in_employment_fact: 将候选人准入条件放入 requirements，不放入 employment_facts。"
            )
        if "company_fact_in_employment_fact" in codes:
            instructions.append(
                "company_fact_in_employment_fact: 将公司团队或技术资源放入 company_facts。"
            )
        if "candidate_facing_company_fact" in codes:
            instructions.append(
                "candidate_facing_company_fact: “你将/你会/你可以”等面向候选人的工作关系或条件不得放入 company_facts；"
                "根据原文语义改为 requirement、employment_fact，或在 Schema 无法忠实表达时不抽取。"
            )
        if "candidate_requirement_in_responsibility" in codes:
            instructions.append(
                "candidate_requirement_in_responsibility: 以熟悉、掌握、精通、具备、会使用、了解等能力语义开头的内容"
                "属于候选人 requirement，不得因原文标题写着岗位职责就放入 responsibilities。"
            )
        if "job_title_duplicate_responsibility" in codes:
            instructions.append(
                "job_title_duplicate_responsibility: 职位标题只进入 job_title，不得把相同标题再次输出为 responsibility；"
                "删除该 responsibility。"
            )
        if "missing_explicit_responsibilities" in codes:
            instructions.append(
                "missing_explicit_responsibilities: 原文存在明确岗位职责段，必须把其中的工作任务抽取到 "
                "responsibilities，并让每条 Evidence 精确引用对应 source_block；不得把职责遗漏或改写成候选人要求。"
            )
        if "employment_kind_evidence_mismatch" in codes:
            instructions.append(
                "employment_kind_evidence_mismatch: 按 violation.expected_kind 修正 EmploymentFact.kind；"
                "以字段标准中的类别定义和原文语义为准，不按当前样本词表猜测。"
            )
        if "duplicate_soft_skill_in_requirement" in codes:
            instructions.append(
                "duplicate_soft_skill_in_requirement: SoftSkillRequirement.skills 中同一能力只保留一次。"
            )
        if "skill_item_type_mismatch" in codes:
            instructions.append(
                "skill_item_type_mismatch: 对 violations 中每个技能使用 expected_item_type；"
                "正式归一化词表是已知技能类型的权威标准，不得沿用上一轮 item_type。"
            )
        if (
            "duplicate_fact_semantics" in codes
            or "duplicate_requirement_semantics" in issue_types
        ):
            instructions.append(
                "duplicate semantics: 相同语义对象只输出一次，不因相同内容出现在多个 source block 而重复抽取。"
            )
        if (
            "conflicting_requirement_modality" in issue_types
            or "conflicting_education_degree_modality" in codes
        ):
            instructions.append(
                "conflicting modality: 同一结构条件同时出现在通用字段标签和完整正文时，"
                "带“必须、优先、加分”等显式强度词的完整正文优先于“学历:硕士”等默认字段标签；"
                "只保留显式强度版本，不重复输出同一结构条件。"
            )
        if "overlapping_requirement_evidence" in issue_types:
            instructions.append(
                "overlapping evidence: 合并共享相同技能项的重复 SkillRequirement，保留原子且不重复的技能集合。"
            )
        if "empty_structured_constraint" in issue_types:
            instructions.append(
                "empty structured constraint: EducationRequirement 必须填写原文明示的 degree/major/school 等字段；"
                "ExperienceRequirement 必须从原文填写 domain、role、year 或 duration_text，"
                "例如“大模型研究、部署经验”应把“大模型研究、部署”写入 domain；"
                "禁止输出多个结构字段全空的 experience。若 Schema 确实无法表达才改为 OtherRequirement。"
            )
        if "missing_job_title" in issue_types:
            instructions.append(
                "missing_job_title: source_blocks 的第一个 block 已通过标题结构判定；"
                "必须将该 block 的完整原文填入 job_title.value，并让 job_title.evidence 精确引用同一 block。"
            )
        if "missing_company_name" in issue_types:
            instructions.append(
                "missing_company_name: source_blocks 的第三个 block 已通过公司名结构判定；"
                "必须新增 kind=company_name 的 CompanyFact 并精确引用该 block，不能用其他公司属性代替。"
            )
        if "skill_item_other_requires_review" in issue_types:
            instructions.append(
                "skill_item_other_requires_review: 具名模型、协议、API、框架、库、工具或方法必须改为最接近的合法 item_type；"
                "英文读写、沟通、代码实现等能力短语应改为 SoftSkillRequirement 或 OtherRequirement，"
                "不能作为 item_type=other 的 SkillItem。"
            )
        if instructions:
            semantic_instruction = (
                "\n# 针对本轮语义错误的必执行修正\n- "
                + "\n- ".join(instructions)
                + "\n"
            )
    previous_output_section = ""
    if previous_invalid_output is not None:
        previous_output_section = f"""
# 上一轮被拒绝的完整 JSON

{previous_invalid_output}

上面的 JSON 仅用于定位错误，不能原样重复输出。必须修正 error_details 指向的所有对象，再输出完整结果。
"""
    history_json = json.dumps(
        history, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return f"""{original}

# 上一轮输出未通过确定性校验

error_type: {error_type}
error_details: {details}

# 本条 JD 累计校验历史

validation_history: {history_json}

本轮必须同时满足 validation_history 中历次错误的修正规则；不得在修复当前错误时重新引入此前已修正的问题。
{evidence_instruction}
{semantic_instruction}
{previous_output_section}

请从 source_blocks 重新抽取并从头输出完整 JSON object，不要局部补丁，不要沿用上一轮错误字段。
必须严格遵守 JSON Schema、精确 Evidence 和语义规则；不得解释修复过程。
"""


def build_local_repair_prompt(
    error_type: str,
    error_details: object,
    repair_targets: list[dict],
    source_blocks: list[dict],
    append_collections: list[str] | None = None,
) -> str:
    """Build a bounded correction request for already-localized validation errors."""
    details = json.dumps(
        error_details, ensure_ascii=False, separators=(",", ":"), default=str
    )
    targets = json.dumps(repair_targets, ensure_ascii=False, separators=(",", ":"))
    blocks = json.dumps(source_blocks, ensure_ascii=False, separators=(",", ":"))
    append_targets = json.dumps(
        append_collections or [], ensure_ascii=False, separators=(",", ":")
    )
    return f"""# 局部校验修复任务

上一轮 JD 抽取结果只有下列对象未通过确定性校验。不要重新抽取整条 JD，不要输出完整 JD JSON，也不要修改未列出的对象。

error_type: {error_type}
error_details: {details}

# 允许修复的现有对象

repair_targets: {targets}

# 与允许对象直接相关的原始文本

source_blocks: {blocks}

# 允许追加对象的 collection

append_collections: {append_targets}

# 唯一允许的输出格式

只输出一个合法 JSON object，且顶层只能有 operations：
{{"operations":[...]}}

operations 中每项只能是以下之一：
1. 替换一个允许对象：
{{"op":"replace","target":{{"collection":"requirements","index":0}},"value":{{...}}}}
2. 删除一个允许对象：
{{"op":"remove","target":{{"collection":"employment_facts","index":0}}}}
3. 向 append_collections 明确列出的 collection 追加对象：
{{"op":"append","target":{{"collection":"requirements"}},"value":{{...}}}}

collection 仅能是 responsibilities、requirements、company_facts、employment_facts。replace/remove 的 collection 和 index 必须严格等于 repair_targets 中的对象；append 只能写入 append_collections 明确列出的 collection。
同一个 collection/index 最多只能出现一次 replace 或 remove。若同一对象有多个字段错误，必须把全部修正合并到一个完整 replace value 中；禁止对同一 target 连续 replace，也禁止先 replace 再 remove。
若修复需要把候选人条件从 responsibilities 移到 requirements，必须用一次 remove 删除原 responsibility，再用一次 append 新增完整 requirement。
value 必须是完整的业务对象，但禁止填写 document_id、requirement_id、fact_id、evidence.start、evidence.end、evidence.alignment、evidence.occurrence_index；这些字段由 Python 重新生成。
每个 evidence.quote 必须从 source_blocks 的对应 source_id 原样连续复制。修复所有 error_details 指出的错误；不要解释，不要 Markdown，不要代码块。
"""
