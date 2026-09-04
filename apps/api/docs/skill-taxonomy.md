# 标准技能多维分类

> 文档类型：领域设计与实现说明
> 维护状态：active
> 适用范围：主系统标准技能目录
> 事实源：`skills`、`skill_taxonomy_nodes`、`skill_classifications`
> 责任角色：主系统技能目录维护者
> 最后复核：2026-07-28

完整类别定义、旧类别迁移规则和 Extraction 重处理顺序见
跨运行时契约位于 `packages/contracts/jobgraph_contracts/skill_taxonomy.py`；JD/CV Extraction
各自消费版本化资源快照。本文只说明主系统的所有权和实现。

## 所有权与边界

- `skills` 保存标准技能身份；主系统是标准技能 ID 和分类关系的唯一写入方。
- `skill_taxonomy_nodes` 保存受控分类节点，不允许调用方自由填写类别名称。
- `skill_classifications` 保存技能与多个分类轴的关系。
- Extraction 负责识别、抽取和归一化输入，不创建权威分类。
- Knowledge Graph 消费目录分类并叠加岗位语境、Evidence、权重和图关系，不反向创建主系统权威类别。

本设计不使用分类版本表。`skills.category` 仅为旧接口兼容字段，不是多维分类的事实源，
也不会被自动转换成新关系。

## 三个分类轴

| facet | 基数 | 作用 |
| --- | --- | --- |
| `concept_class` | 每个技能最多一个 | 区分技术实体、知识概念、方法实践和通用能力 |
| `technology_kind` | 技术实体最多一个 | 区分语言、框架、库、存储、平台、协议、模型、硬件等形态 |
| `domain` | 可多个、最多一个主领域 | 表达技能覆盖的软件、AI、云、安全、通信、芯片等领域 |

`technology_kind` 只有在技能已关联 `concept_class=technology` 时才能建立。将概念性质从
`technology` 删除前必须先移除技术形态。每个 facet 最多一条 `is_primary=true` 关系。

## 最小治理含义

这里的“可治理”不是版本化、审批平台或复杂流程，只表示数据不会重新退化成任意字符串：

- 分类代码在同一 facet 内唯一；
- 父节点必须存在、处于 active 状态并与子节点属于同一 facet；
- inactive 节点保留已有引用，但不能建立新关系；
- 数据库约束和应用规则共同拒绝重复关系与非法跨轴组合；
- 未知类别保持未分类，不写 `other` 或默认类别。

## 接口

分类节点：

- `POST /api/v1/skill-taxonomy/nodes`
- `GET /api/v1/skill-taxonomy/nodes?facet=domain`
- `PUT /api/v1/skill-taxonomy/nodes/{node_id}`

技能分类关系：

- `POST /api/v1/skills/{skill_id}/classifications`
- `GET /api/v1/skills/{skill_id}/classifications`
- `DELETE /api/v1/skills/{skill_id}/classifications/{classification_id}`

写接口沿用主系统目录管理权限；读接口用于目录展示和下游消费。

## 数据迁移边界

迁移 `20260728_35` 创建分类结构，`20260728_36` 为标准技能增加稳定
`catalog_code`。审核目录位于 `config/skill_taxonomy_catalog.v1.json`，使用下列命令在
一个事务内同步节点、技能代码和分类关系：

```powershell
python scripts/sync_skill_taxonomy_catalog.py --check
python scripts/sync_skill_taxonomy_catalog.py
```

主后端 Docker 启动会在迁移和 bootstrap 账号后自动执行正式同步，并且仅在同步成功后
启动 API。上述命令仍用于本地检查或显式维护，不再是容器部署的必需人工步骤。

同步遇到名称、代码或节点冲突会明确失败，不从旧 `skills.category` 猜测。taxonomy
SHA-256 只覆盖稳定的 catalog code、canonical name 和多维分类关系，节点展示名与技能别名不改变语义指纹。
主系统对 JD/CV v2 projection 逐项校验版本、覆盖、唯一性、canonical name 和全部分类关系；
任何差异均阻断下游快照。`capability-skill-snapshot.v2` 向 KG 发布分类关系与 taxonomy SHA-256；
v1 仅保留历史兼容。完成调整后，应重新执行 Extraction 归一化及
下游目录快照/KG 构建；
只有原抽取遗漏或错误合并概念时才重跑原始抽取。
