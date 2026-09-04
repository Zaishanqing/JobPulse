from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from .models import CVExtractionResult
from .semantic_rules import compile_semantic_handbook, repair_instruction


SPEC_PATH = Path(__file__).resolve().parents[1] / "docs" / "annotation-standard.md"


def _authoritative_spec_for_fields(
    authoritative_spec: str,
    top_level_fields: tuple[str, ...] | None,
) -> str:
    """Keep all rules for batch extraction and only relevant rules per shard."""
    if top_level_fields is None:
        return authoritative_spec
    selected = set(top_level_fields)
    section_numbers = {"1", "2", "3"}
    if selected.intersection({"skills", "work_experience", "project_experience"}):
        section_numbers.add("4")
    if selected.intersection({"certificates", "awards"}):
        section_numbers.add("5")
    if selected.intersection(
        {
            "education",
            "work_experience",
            "project_experience",
            "publications",
            "patents",
            "research_outputs",
        }
    ):
        section_numbers.add("6")
    parts = re.split(r"(?=^##\s+\d+\.)", authoritative_spec, flags=re.MULTILINE)
    retained = [parts[0]]
    for part in parts[1:]:
        match = re.match(r"^##\s+(\d+)\.", part)
        if match and match.group(1) in section_numbers:
            retained.append(part)
    return "".join(retained).strip()


def _compact_schema_for_prompt(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _compact_schema_for_prompt(child)
            for key, child in value.items()
            if key not in {"title", "default"}
        }
    if isinstance(value, list):
        return [_compact_schema_for_prompt(child) for child in value]
    return value


def _referenced_definition_names(value: object) -> set[str]:
    """Collect transitive JSON Schema definitions used by a partial schema."""
    referenced: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            referenced.add(reference.removeprefix("#/$defs/"))
        for child in value.values():
            referenced.update(_referenced_definition_names(child))
    elif isinstance(value, list):
        for child in value:
            referenced.update(_referenced_definition_names(child))
    return referenced


def _prune_unused_definitions(schema: dict) -> None:
    definitions = schema.get("$defs", {})
    pending = list(_referenced_definition_names(schema.get("properties", {})))
    retained: set[str] = set()
    while pending:
        name = pending.pop()
        if name in retained or name not in definitions:
            continue
        retained.add(name)
        pending.extend(_referenced_definition_names(definitions[name]) - retained)
    schema["$defs"] = {
        name: definition
        for name, definition in definitions.items()
        if name in retained
    }


def build_model_output_schema(
    top_level_fields: tuple[str, ...] | None = None,
) -> dict:
    schema = deepcopy(CVExtractionResult.model_json_schema())
    schema["properties"].pop("document_id", None)
    schema["required"] = [name for name in schema.get("required", []) if name != "document_id"]
    for definition_name in (
        "EducationEntry",
        "WorkEntry",
        "ProjectEntry",
        "LanguageSkill",
        "CertificateEntry",
        "AwardEntry",
        "PublicationEntry",
        "PatentEntry",
        "ResearchOutputEntry",
        "SelfEvaluation",
    ):
        definition = schema["$defs"][definition_name]
        definition["properties"].pop("entry_id", None)
        definition["required"] = [name for name in definition.get("required", []) if name != "entry_id"]
    skill_item = schema["$defs"]["SkillItem"]
    skill_item["properties"].pop("item_id", None)
    skill_item["required"] = [name for name in skill_item.get("required", []) if name != "item_id"]
    evidence = schema["$defs"]["Evidence"]
    for field_name in ("start", "end", "alignment", "occurrence_index"):
        evidence["properties"].pop(field_name, None)
        evidence["required"] = [name for name in evidence.get("required", []) if name != field_name]
    certificate_kind = schema["$defs"]["CertificateEntry"]["properties"]["kind"]
    certificate_kind["enum"] = [
        value for value in certificate_kind["enum"] if value != "competition_award"
    ]
    if top_level_fields is not None:
        unknown = set(top_level_fields) - set(schema["properties"])
        if unknown:
            raise ValueError(f"Unknown CV extraction fields: {sorted(unknown)}")
        selected = set(top_level_fields)
        schema["properties"] = {
            name: value
            for name, value in schema["properties"].items()
            if name in selected
        }
        schema["required"] = [
            name for name in schema.get("required", []) if name in selected
        ]
        _prune_unused_definitions(schema)
    return _compact_schema_for_prompt(schema)


@lru_cache(maxsize=8)
def build_system_prompt(
    top_level_fields: tuple[str, ...] | None = None,
) -> str:
    schema = json.dumps(
        build_model_output_schema(top_level_fields),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    handbook = compile_semantic_handbook(top_level_fields)
    authoritative_spec = SPEC_PATH.read_text(encoding="utf-8").strip()
    if not authoritative_spec:
        raise ValueError(f"Authoritative CV specification is empty: {SPEC_PATH}")
    authoritative_spec = _authoritative_spec_for_fields(
        authoritative_spec, top_level_fields
    )
    shard_scope = ""
    if top_level_fields is not None:
        shard_scope = (
            "\n# 分区输出范围\n\n"
            f"只输出这些顶层字段：{json.dumps(top_level_fields, ensure_ascii=False)}。"
            "不得输出其他顶层字段；仍须阅读全部 source_blocks 后再判断。\n"
        )
    return f"""你是简历原子事实抽取器。只负责忠实抽取原文信息，不负责归一化、评分或导出展示。

# 输出契约

1. 只输出一个符合 JSON Schema 的 JSON object，不输出 Markdown、解释、注释或额外字段。
2. 每个对象必须是可独立判断的原子语义对象。
3. 每个 entry、技能、职责、成果、技术栈、项目亮点和自我评价都必须给出自己的 evidence；quote 必须逐字存在于对应 source block。
4. PersonalInfo、EducationEntry、WorkEntry、ProjectEntry 各自只能使用其 JSON Schema 声明的 field_evidence 字段名；每个用于匹配且非 null、非 unknown 的标量字段必须各有且只有一个同名绑定。绑定的 evidence 必须直接支持该字段值，可以与 entry 主体 evidence 来自不同 source block。姓名、电话、邮箱等 PII 不得进入 field_evidence。
5. 不得修改 quote 的文字、空格或标点，不得跨 block，不得拼接证据。
6. 不输出 entry_id、item_id、start、end、alignment、occurrence_index；这些由 Python 填充。
7. 数组字段没有内容时只能输出 []，禁止输出 null；只有 Schema 明确允许 null 的标量或对象字段才能输出 null。
8. 没有信息的可选字段直接省略，不要输出空字符串 ""。

# 语义规则

{handbook}

# 权威标注规范

{authoritative_spec}
{shard_scope}

# JSON Schema

{schema}
"""


def build_user_prompt(
    cv_input: dict,
    taxonomy_requirements: list[dict[str, str]],
    coverage_requirements: list[dict[str, object]],
    *,
    top_level_fields: tuple[str, ...] | None = None,
) -> str:
    if top_level_fields is not None:
        selected = set(top_level_fields)
        coverage_requirements = [
            {
                **requirement,
                "expected_collections": [
                    name
                    for name in requirement.get("expected_collections", [])
                    if name in selected
                ],
            }
            for requirement in coverage_requirements
            if selected.intersection(requirement.get("expected_collections", []))
        ]
        section_collections = {
            "skills": {"skills"},
            "work": {"work_experience"},
            "project": {"project_experience"},
        }
        taxonomy_requirements = [
            requirement
            for requirement in taxonomy_requirements
            if selected.intersection(
                section_collections.get(requirement.get("section", ""), set())
            )
        ]
    blocks = [
        {"source_id": block["source_id"], "text": block["text"]}
        for block in cv_input["source_blocks"]
    ]
    requirements = json.dumps(
        taxonomy_requirements, ensure_ascii=False, separators=(",", ":")
    )
    coverage = json.dumps(
        coverage_requirements, ensure_ascii=False, separators=(",", ":")
    )
    source_json = json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))
    requested_scope = (
        "all"
        if top_level_fields is None
        else json.dumps(top_level_fields, ensure_ascii=False, separators=(",", ":"))
    )
    return f"""抽取以下简历。每个 evidence.quote 必须原样复制其 source_id 对应的 text。

document_id: {cv_input['cv_id']}
requested_top_level_fields: {requested_scope}
source_blocks: {source_json}
source_taxonomy_requirements: {requirements}
source_coverage_requirements: {coverage}

source_taxonomy_requirements 只列出本份原文在技能、工作或项目分区中精确出现、且最终确定性门槛本来就会检查的技能名称和类型，不包含 canonical ID。skills 分区的要求进入 skills；work/project 分区的要求进入引用对应 source_id 的经历 tech_stack。不得把要求添加到没有引用该 source_id 的其他经历，也不得扩写 name。
source_coverage_requirements 列出最终覆盖度门槛本来就要求引用的 source_id。每项必须至少被 expected_collections 中一个正确业务对象的 Evidence 引用；引用技术项不能代替同一原文块中的职责、成果或项目事实。不得为了满足覆盖度而虚构对象或把活动名称伪装成项目。

输出前必须逐项完成以下闭包检查：
1. 每个非 null、非 unknown 的匹配标量都有且只有一个同名 field_evidence；不得把相邻教育或经历对象的 GPA、日期、地点等字段复制到当前对象。
2. 每个技能名称或同一 canonical identity 的权威别名直接出现在其 evidence.source_id 文本中；不得从文件后缀、链接元数据、泛化描述或大小写相近的其他实体推断技能。
3. quote 直接从单个 source block 连续复制，保留句首编号、空格及中英文标点；需要证明局部名称时优先复制最短充分原文。
4. 学生会、社团、协会、中心等组织及其任职进入 work_experience；竞赛名称不是技术项目名，项目内单独的排名或准确率只进入 achievements/highlights，不得重复进入 awards。awards.name 必须同时保留原文明示的竞赛身份和奖次/提名结果，不得只输出竞赛名称或“单项奖”等泛称。
5. 论文进入 publications，专利进入 patents，科研项目、竞赛、数据集、开源软件、标准或技术报告进入 research_outputs。只抽取原文明示的成果；课程论文、普通项目名称和技能声明不得推断成科研产出。title/name、状态、作者或发明人身份、顺序和年份必须分别忠实于原文，缺失字段保持 null 或 unknown。在投论文的 status 必须为 submitted，不得标为 published。
"""


def build_local_repair_prompt(
    error_type: str,
    error_details: object,
    repair_targets: list[dict],
    source_blocks: list[dict],
    append_collections: list[str] | None = None,
    required_append_counts: dict[str, int] | None = None,
) -> str:
    """Build a bounded correction request for already-localized validation errors."""
    details = json.dumps(error_details, ensure_ascii=False, separators=(",", ":"), default=str)
    targets = json.dumps(repair_targets, ensure_ascii=False, separators=(",", ":"))
    blocks = json.dumps(source_blocks, ensure_ascii=False, separators=(",", ":"))
    append_targets = json.dumps(append_collections or [], ensure_ascii=False, separators=(",", ":"))
    append_counts = json.dumps(required_append_counts or {}, ensure_ascii=False, separators=(",", ":"))
    required_field_rule = _required_field_evidence_rule(error_details)
    semantic_repair_rule = _local_semantic_repair_rule(error_details)
    return f"""# 局部校验修复任务

上一轮简历抽取结果只有下列对象未通过确定性校验。不要重新抽取整份简历，不要输出完整 JSON，也不要修改未列出的对象。

error_type: {error_type}
error_details: {details}

# 允许修复的现有对象

repair_targets: {targets}

# 与允许对象直接相关的原始文本

source_blocks: {blocks}

# 允许追加对象的 collection

append_collections: {append_targets}
required_append_counts: {append_counts}

# 唯一允许的输出格式

只输出一个合法 JSON object，且顶层只能有 operations：
{{"operations":[...]}}

operations 中每项只能是以下之一：
1. 替换一个允许对象：
{{"op":"replace","target":{{"collection":"education","index":0}},"value":{{...}}}}
2. 删除一个允许对象：
{{"op":"remove","target":{{"collection":"work_experience","index":0}}}}
3. 向 append_collections 明确列出的 collection 追加对象：
{{"op":"append","target":{{"collection":"education"}},"value":{{...}}}}
4. 替换或删除 personal_info 单例：
{{"op":"replace","target":{{"singleton":"personal_info"}},"value":{{...}}}}
{{"op":"remove","target":{{"singleton":"personal_info"}}}}

collection 仅能是 education、work_experience、project_experience、skills、languages、certificates、awards、publications、patents、research_outputs、self_evaluation。replace/remove 的 collection 和 index 必须严格等于 repair_targets 中的对象；append 只能写入 append_collections 明确列出的 collection。
singleton 只能是 repair_targets 明确授权的 personal_info；单例只允许整体 replace 或 remove，不允许 append。
如果 required_append_counts 声明了数量，必须为对应 collection 输出数量完全一致的 append 操作。复合顶层技能拆分时，用 replace 将原对象替换为第一个原子技能，再按 parts 顺序 append 其余原子技能；不得遗漏或保留复合名称。
同一个 collection/index 最多只能出现一次 replace 或 remove。若同一对象有多个字段错误，必须把全部修正合并到一个完整 replace value 中；禁止对同一 target 连续 replace，也禁止先 replace 再 remove。
value 必须是完整的业务对象，但禁止填写 document_id、entry_id、item_id、evidence.start、evidence.end、evidence.alignment、evidence.occurrence_index；这些字段由 Python 重新生成。
对象内部每个 evidence.quote 都必须从 source_blocks 的对应 source_id 原样连续复制。修复所有 error_details 指出的错误；不要解释，不要 Markdown，不要代码块。
{required_field_rule}
{semantic_repair_rule}
"""


def _local_semantic_repair_rule(error_details: object) -> str:
    details = _flatten_candidate_error_details(error_details)
    codes = {
        detail.get("code") for detail in details
        if isinstance(detail, dict) and isinstance(detail.get("code"), str)
    }
    instructions = [instruction for code in sorted(codes) if (instruction := repair_instruction(code))]
    if not instructions:
        return ""
    return "\n# 本轮局部语义修复规则\n- " + "\n- ".join(instructions) + "\n"


def _required_field_evidence_rule(error_details: object) -> str:
    details = _flatten_candidate_error_details(error_details)
    required_by_type = {
        "education": {"school", "major"},
        "work_experience": {"company"},
        "project_experience": {"name"},
    }
    affected: set[str] = set()
    for detail in details:
        if not isinstance(detail, dict) or detail.get("code") != "invalid_match_field_evidence":
            continue
        object_type = detail.get("type")
        required = required_by_type.get(object_type, set())
        invalid_fields = set(detail.get("missing_fields", [])) | set(
            detail.get("unsupported_fields", [])
        )
        for field in sorted(required & invalid_fields):
            affected.add(f"{object_type}.{field}")
    has_field_evidence_error = any(
        isinstance(detail, dict)
        and detail.get("code") == "invalid_match_field_evidence"
        for detail in details
    )
    if not has_field_evidence_error:
        return ""
    affected_text = (
        f"以下字段属于必填识别字段：{', '.join(sorted(affected))}。"
        if affected
        else ""
    )
    return (
        "\n# 匹配字段与证据的闭包修复\n"
        + affected_text
        + "replace value 是完整对象：修改任何匹配字段后，必须重新核对该对象全部匹配字段。每个非 null、"
        "非 unknown 字段必须恰有一个同名 field_evidence；null 或 unknown 字段不得保留同名绑定。"
        "不得只删除其 field_evidence 后保留原值，"
        "也不得为字段值添加原文中不存在的类别前缀（例如‘课程项目 -’）。必须从给定 source_blocks 选择"
        "能够识别该对象的连续原文短语，同时把字段值改成该原文短语，并保留且仅保留一个包含该值的"
        "精确 field_evidence；项目动作或成果有原文支持但名称不合法时，必须修正项目名称，不得删除"
        "整个项目；只有原文根本不支持该对象时才删除整个对象。\n"
    )


def _flatten_candidate_error_details(error_details: object) -> list[object]:
    details = error_details if isinstance(error_details, list) else [error_details]
    flattened: list[object] = []
    for detail in details:
        if isinstance(detail, dict) and "error_type" in detail and "error_details" in detail:
            nested = detail["error_details"]
            flattened.extend(nested if isinstance(nested, list) else [nested])
        else:
            flattened.append(detail)
    return flattened


def build_validation_retry_prompt(
    cv_input: dict,
    taxonomy_requirements: list[dict[str, str]],
    coverage_requirements: list[dict[str, object]],
    error_type: str,
    error_details: object,
    previous_invalid_output: str | None = None,
    validation_history: list[dict] | None = None,
) -> str:
    original = build_user_prompt(
        cv_input, taxonomy_requirements, coverage_requirements
    )
    details = json.dumps(error_details, ensure_ascii=False, separators=(",", ":"), default=str)
    history = validation_history or [{"error_type": error_type, "error_details": error_details}]
    evidence_instruction = ""
    if any(
        item.get("error_type") == "SourceBindingError"
        or (
            item.get("error_type") == "CandidateValidationError"
            and any(
                isinstance(issue, dict) and issue.get("error_type") == "SourceBindingError"
                for issue in (item.get("error_details") or [])
            )
        )
        for item in history
    ):
        evidence_instruction = """
这是 Evidence 复制错误。error_details 会一次列出本轮发现的全部错误对象；每项 exact_source_text 是该对象唯一合法原文：
- quote 必须直接复制其中的连续字符，保留所有空格、英文/中文标点和句首编号；
- 立即在上一轮 JSON 中定位 source_id 与 invalid_quote 同时匹配的对象，把该对象的 quote 改为 exact_source_text；
- 如果原文不支持该对象，则不要生成该对象，禁止改写原文来保留对象。
"""
    semantic_instruction = ""
    semantic_errors = [
        detail
        for history_item in history
        for issue in (
            history_item.get("error_details", [])
            if history_item.get("error_type") == "CandidateValidationError"
            else [
                {
                    "error_type": history_item.get("error_type"),
                    "error_details": history_item.get("error_details"),
                }
            ]
        )
        if isinstance(issue, dict) and issue.get("error_type") == "SemanticValidationError"
        for detail in _flatten_candidate_error_details(issue.get("error_details"))
        if isinstance(detail, dict)
    ]
    if semantic_errors:
        codes = {item.get("code") for item in semantic_errors}
        instructions = [instruction for code in sorted(codes) if (instruction := repair_instruction(code))]
        if "invalid_match_field_evidence" in codes:
            instructions.append(
                "invalid_match_field_evidence: 按 missing_fields 为每个非空匹配字段补充且只补充一个 "
                "field_evidence 绑定；删除 duplicate_fields 的重复绑定和 unexpected_fields 的无效绑定；"
                "unsupported_fields 必须改绑到包含该字段原始文字的精确引文，原文不支持时删除可选字段或修正字段值。"
            )
            required_rule = _required_field_evidence_rule(semantic_errors).strip()
            if required_rule:
                instructions.append(required_rule)
        if instructions:
            semantic_instruction = "\n# 针对本轮语义错误的必执行修正\n- " + "\n- ".join(instructions) + "\n"
    previous_output_section = ""
    if previous_invalid_output is not None:
        previous_output_section = f"""
# 上一轮被拒绝的完整 JSON

{previous_invalid_output}

上面的 JSON 仅用于定位错误，不能原样重复输出。必须修正 error_details 指向的所有对象，再输出完整结果。
"""
    history_json = json.dumps(history, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"""{original}

# 上一轮输出未通过确定性校验

error_type: {error_type}
error_details: {details}

# 本条简历累计校验历史

validation_history: {history_json}

本轮必须同时满足 validation_history 中历次错误的修正规则；不得在修复当前错误时重新引入此前已修正的问题。
{evidence_instruction}
{semantic_instruction}
{previous_output_section}

请从 source_blocks 重新抽取并从头输出完整 JSON object，不要局部补丁，不要沿用上一轮错误字段。
必须严格遵守 JSON Schema、精确 Evidence 和语义规则；不得解释修复过程。
"""
