# 接口清单

> 文档类型：接口参考（API reference）
> 维护状态：生效（active）
> 适用范围：项目级
> 责任人：后端维护者
> 最后复核：2026-08-20（路由命名空间与物理路径）

## 0. 接口设计约定

### 0.1 基础路径

```text id="fset6m"
/api/v1
```

### 0.2 角色约定

```text id="vwc66j"
personal_user      个人用户
enterprise_user    企业用户
admin              管理员
reviewer           审核员
developer          开发团队 / 系统维护者
```

公开网站只暴露个人入口与企业入口。
管理审核后台通过隐藏路径或独立后台访问，例如：

```text id="kd3t67"
/admin
```

### 0.3 通用返回格式

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "trace_id": "req_xxx"
}
```

### 0.4 通用分页参数

```json
{
  "page": 1,
  "page_size": 20,
  "keyword": "",
  "sort_by": "created_at",
  "order": "desc"
}
```

### 0.5 优先级说明

| 优先级 | 含义 |
|---|---|
| P0 | 最小闭环必须实现 |
| P1 | 推荐实现，支撑创新点 |
| P2 | 先保留接口，后续逐步实现 |

系统以“多源数据采集→新岗位发现与定义和既有岗位能力更新→能力图谱动态→简历解析→精准匹配与差距分析”的完整闭环为目标，因此 P0 接口围绕这条链路设计。

---

# 1. 用户与权限接口

## 1.1 登录注册

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 用户注册 | POST | `/auth/register` | 个人 / 企业 | P0 | 是 |
| 用户登录 | POST | `/auth/login` | 个人 / 企业 / 管理员 | P0 | 是 |
| 退出登录 | POST | `/auth/logout` | 全部用户 | P0 | 是 |
| 退出所有登录 | POST | `/auth/logout-all` | 全部用户 | P0 | 是 |
| 续期 Access Session | POST | `/auth/refresh` | 全部用户 | P0 | 是 |
| 获取当前用户信息 | GET | `/auth/me` | 全部用户 | P0 | 是 |
| 修改密码 | PUT | `/auth/password` | 全部用户 | P1 | 是 |
| 重置密码 | POST | `/auth/password/reset` | 全部用户 | P2 | 否 |

`/auth/refresh` 只使用当前有效的 access JWT 续期 access session；当前实现不含
refresh-token rotation、reuse detection 或独立 session revocation，不属于完整的双 Token 系统。
`/auth/logout` 仅确认当前客户端退出；`/auth/logout-all` 通过递增账号 `token_version`
使该账号之前签发的所有 access JWT 在 API 鉴权时失效。

### POST `/auth/register`

请求：

```json
{
  "role": "personal_user",
  "username": "user001",
  "password": "******",
  "email": "user@example.com",
  "phone": "13800000000"
}
```

返回：

```json
{
  "user_id": "u_001",
  "role": "personal_user",
  "username": "user001"
}
```

---

## 1.2 角色与权限

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取角色列表 | GET | `/roles` | 管理员 | P1 | 是 |
| 获取权限列表 | GET | `/permissions` | 管理员 | P1 | 是 |
| 给用户分配角色 | PUT | `/users/{user_id}/role` | 管理员 | P1 | 是 |
| 禁用用户 | PUT | `/users/{user_id}/disable` | 管理员 | P1 | 是 |
| 启用用户 | PUT | `/users/{user_id}/enable` | 管理员 | P1 | 是 |

---

# 2. 企业信息接口

## 2.1 企业资料

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建企业资料 | POST | `/enterprises` | 企业用户 | P0 | 是 |
| 获取企业资料 | GET | `/enterprises/{enterprise_id}` | 企业用户 / 管理员 | P0 | 是 |
| 修改企业资料 | PUT | `/enterprises/{enterprise_id}` | 企业用户 | P1 | 是 |
| 获取我的企业信息 | GET | `/enterprises/me` | 企业用户 | P0 | 是 |
| 企业认证提交 | POST | `/enterprises/{enterprise_id}/verification` | 企业用户 | P2 | 否 |
| 企业认证审核 | PUT | `/enterprises/{enterprise_id}/verification` | 管理员 | P2 | 否 |

### POST `/enterprises`

请求：

```json
{
  "enterprise_name": "示例科技有限公司",
  "industry": "人工智能",
  "scale": "100-500人",
  "location": "武汉",
  "description": "专注大模型应用开发"
}
```

返回：

```json
{
  "enterprise_id": "ent_001",
  "enterprise_name": "示例科技有限公司",
  "status": "active"
}
```

---

# 3. 企业招聘岗位管理接口

该模块对应企业端“岗位发布、岗位需求调整、招聘人数调整、暂停/恢复/撤销招聘”等能力。相关接口先保留，部分在一期中不要求完整实现。

## 3.1 招聘岗位基础管理

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建招聘岗位 | POST | `/enterprise-jobs` | 企业用户 | P0 | 是 |
| 获取招聘岗位列表 | GET | `/enterprise-jobs` | 企业用户 / 管理员 | P0 | 是 |
| 获取招聘岗位详情 | GET | `/enterprise-jobs/{job_id}` | 企业用户 / 管理员 | P0 | 是 |
| 编辑招聘岗位 | PUT | `/enterprise-jobs/{job_id}` | 企业用户 | P0 | 是 |
| 删除招聘岗位草稿 | DELETE | `/enterprise-jobs/{job_id}` | 企业用户 | P1 | 是 |
| 复制招聘岗位 | POST | `/enterprise-jobs/{job_id}/copy` | 企业用户 | P2 | 否 |

### POST `/enterprise-jobs`

请求：

```json
{
  "enterprise_id": "ent_001",
  "title": "大模型应用开发工程师",
  "standard_position_id": "pos_001",
  "jd_text": "岗位职责：负责 RAG 应用开发...",
  "headcount": 3,
  "location": "武汉",
  "employment_type": "full_time",
  "salary_min": 15000,
  "salary_max": 25000,
  "status": "draft"
}
```

返回：

```json
{
  "enterprise_job_id": "ejob_001",
  "title": "大模型应用开发工程师",
  "status": "draft",
  "created_at": "2026-07-09T10:00:00+08:00"
}
```

---

## 3.2 招聘岗位状态管理

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 发布招聘岗位 | PUT | `/enterprise-jobs/{job_id}/publish` | 企业用户 | P1 | 是 |
| 暂停招聘岗位 | PUT | `/enterprise-jobs/{job_id}/pause` | 企业用户 | P2 | 接口保留 |
| 恢复招聘岗位 | PUT | `/enterprise-jobs/{job_id}/resume` | 企业用户 | P2 | 接口保留 |
| 撤销招聘岗位 | PUT | `/enterprise-jobs/{job_id}/cancel` | 企业用户 | P2 | 接口保留 |
| 修改招聘人数 | PUT | `/enterprise-jobs/{job_id}/headcount` | 企业用户 | P2 | 接口保留 |
| 获取岗位状态变更记录 | GET | `/enterprise-jobs/{job_id}/status-logs` | 企业用户 / 管理员 | P2 | 否 |

### PUT `/enterprise-jobs/{job_id}/headcount`

请求：

```json
{
  "headcount": 5,
  "reason": "业务扩张，需要增加招聘名额"
}
```

返回：

```json
{
  "enterprise_job_id": "ejob_001",
  "old_headcount": 3,
  "new_headcount": 5,
  "status": "published"
}
```

---

## 3.3 企业岗位权重配置

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取岗位默认技能权重 | GET | `/enterprise-jobs/{job_id}/skill-weights` | 企业用户 | P0 | 是 |
| 修改岗位技能权重 | PUT | `/enterprise-jobs/{job_id}/skill-weights` | 企业用户 | P1 | 是 |
| 重置为标准岗位权重 | POST | `/enterprise-jobs/{job_id}/skill-weights/reset` | 企业用户 | P1 | 是 |
| 设置必备技能 | PUT | `/enterprise-jobs/{job_id}/required-skills` | 企业用户 | P1 | 是 |
| 设置加分技能 | PUT | `/enterprise-jobs/{job_id}/bonus-skills` | 企业用户 | P1 | 是 |
| 获取权重修改历史 | GET | `/enterprise-jobs/{job_id}/weight-logs` | 企业用户 / 管理员 | P2 | 否 |

### PUT `/enterprise-jobs/{job_id}/skill-weights`

请求：

```json
{
  "weights": [
    {
      "skill_id": "skill_python",
      "weight": 0.25,
      "is_required": true
    },
    {
      "skill_id": "skill_rag",
      "weight": 0.35,
      "is_required": true
    },
    {
      "skill_id": "skill_docker",
      "weight": 0.10,
      "is_required": false
    }
  ]
}
```

返回：

```json
{
  "enterprise_job_id": "ejob_001",
  "updated_count": 3
}
```

---

# 4. JD 数据接口

## 4.1 JD 上传与导入

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 上传单条 JD 文本 | POST | `/jds/text` | 企业用户 / 管理员 | P0 | 是 |
| 上传 JD 文件 | POST | `/jds/file` | 企业用户 / 管理员 | P0 | 是 |
| 批量导入 JD | POST | `/jds/batch` | 管理员 | P0 | 是 |
| 上传 JD 图片 | POST | `/jds/image` | 企业用户 / 管理员 | P1 | 是 |
| 获取 JD 列表 | GET | `/jds` | 企业用户 / 管理员 | P0 | 是 |
| 获取 JD 详情 | GET | `/jds/{jd_id}` | 企业用户 / 管理员 | P0 | 是 |
| 编辑 JD 原文 | PUT | `/jds/{jd_id}/raw` | 企业用户 / 管理员 | P0 | 是 |
| 删除 JD | DELETE | `/jds/{jd_id}` | 管理员 | P1 | 是 |
| 弃用 JD | POST | `/jds/{jd_id}/deprecate` | 管理员 | P1 | 是 | 标记 JD 已弃用，解析结果作废并关闭其审核任务 |
| 导入不可变来源 JD | POST | `/source-jds/import` | 已登录调用方 | P0 | 是 |
| 获取来源 JD | GET | `/source-jds/{source_jd_id}` | 已登录调用方 | P0 | 是 |
| 获取来源 JD 版本历史 | GET | `/source-jds/{source_jd_id}/versions` | 已登录调用方 | P0 | 是 |
| 获取来源 JD 版本 | GET | `/source-jd-versions/{version_id}` | 已登录调用方 | P0 | 是 |
| 创建 JD 抽取任务 | POST | `/source-jd-versions/{version_id}/extraction-tasks` | 已登录调用方 | P0 | 是 |
| 执行 JD 抽取任务 | POST | `/extraction-tasks/{task_id}/run` | 已登录调用方 | P0 | 是 |
| 重试 JD 抽取任务 | POST | `/extraction-tasks/{task_id}/retry` | 已登录调用方 | P0 | 是 |
| 获取 JD 抽取任务 | GET | `/extraction-tasks/{task_id}` | 已登录调用方 | P0 | 是 |
| 导入抽取结果为 JD 草稿 | POST | `/extraction-tasks/{task_id}/import-draft` | 已登录调用方 | P0 | 是 |
| 获取 Task 对应 JD 草稿 | GET | `/extraction-tasks/{task_id}/draft` | 已登录调用方 | P0 | 是 |
| 获取来源版本的 JD 草稿 | GET | `/source-jd-versions/{version_id}/drafts` | 已登录调用方 | P0 | 是 |
| 查询 JD 抽取任务 | GET | `/extraction-tasks` | 已登录调用方 | P0 | 是 |
| 手动触发 pending 任务 | POST | `/extraction-tasks/run-pending` | 管理员 / 开发者 | P0 | 是 |

### POST `/jds/text`

请求：

```json
{
  "source_type": "enterprise_upload",
  "source_name": "企业上传",
  "enterprise_id": "ent_001",
  "title": "Java 开发工程师",
  "raw_text": "岗位职责：负责后端服务开发...",
  "cleaned_text": "岗位职责:负责后端服务开发...",
  "publish_date": "2026-07-01",
  "url": ""
}
```

`cleaned_text` 可选：由 Extraction 正式清洗阶段产出，入库后审核中心优先展示该字段；
未提供时前端按确定性清洗规则现算展示。

JD 相关接口返回的 `raw_text` 字段统一为清洗后文本（`cleaned_text` 优先，缺失时按
确定性清洗规则现算）；原始文本仍保存在 `job_descriptions.raw_text`，用于内容哈希、
血缘核对和审计展示。

返回：

```json
{
  "jd_id": "jd_001",
  "parse_status": "pending",
  "created_at": "2026-07-09T10:00:00+08:00"
}
```

---

## 4.2 JD 解析

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 启动 JD 解析 | POST | `/jds/{jd_id}/parse` | 企业用户 / 管理员 | P0 | 是 |
| 批量启动 JD 解析 | POST | `/jds/parse-batch` | 管理员 | P0 | 是 |
| 获取 JD 解析结果 | GET | `/jds/{jd_id}/parse-result` | 企业用户 / 管理员 | P0 | 是 |
| 编辑 JD 解析结果 | PUT | `/jds/{jd_id}/parse-result` | 企业用户 / 管理员 | P0 | 是 |
| 确认 JD 解析结果 | POST | `/jds/{jd_id}/parse-result/confirm` | 企业用户 / 管理员 | P0 | 是 |
| 获取解析任务状态 | GET | `/jds/parse-tasks/{task_id}` | 企业用户 / 管理员 | P0 | 是 |

### POST `/jds/{jd_id}/parse`

请求：

```json
{
  "model": "default",
  "use_skill_dictionary": true,
  "auto_normalize_skill": true
}
```

返回：

```json
{
  "task_id": "task_parse_001",
  "jd_id": "jd_001",
  "status": "running"
}
```

### GET `/jds/{jd_id}/parse-result`

返回：

```json
{
  "jd_id": "jd_001",
  "position_title": "Java 开发工程师",
  "responsibilities": [
    "负责后端服务设计与开发",
    "参与系统性能优化"
  ],
  "required_skills": [
    {
      "raw_skill": "Java",
      "normalized_skill_id": "skill_java",
      "confidence": 0.98
    },
    {
      "raw_skill": "Spring Boot",
      "normalized_skill_id": "skill_spring_boot",
      "confidence": 0.95
    }
  ],
  "bonus_skills": [
    {
      "raw_skill": "Docker",
      "normalized_skill_id": "skill_docker",
      "confidence": 0.86
    }
  ],
  "education": "本科",
  "experience": "3-5年",
  "industry": "互联网",
  "tools": ["Git", "Maven", "Docker"],
  "business_scenarios": [],
  "parse_confidence": 0.91,
  "need_review": false,
  "execution": {
    "parse_quality": {
      "score": 0.91,
      "level": "high",
      "components": {
        "required_field_coverage": 1.0,
        "exact_evidence_ratio": 0.8,
        "unresolved_quality": 0.8,
        "normalization_coverage": 1.0,
        "schema_provider_validation": 1.0
      }
    }
  }
}
```

`parse_confidence` 是兼容字段，当前承载 deterministic parse quality score，不表示
模型校准 probability。分数由 required field coverage、Exact Evidence ratio、
unresolved ratio、normalization coverage 与 schema/provider validation 组合：
`score >= 0.85` 为 high，默认不审核；`0.60 <= score < 0.85` 进入 normal
review；`score < 0.60` 进入 high-priority review。provider 自身明确要求审核时，
即使质量为 high 也保留 normal review。

主系统不再根据 raw text 中的“后端”等关键词生成 `business_scenarios`。该兼容
字段默认空；只有 versioned normalization 的
`job_classification.industry_context_codes` 同时具有 Evidence refs 时才投影保留。

---

## 4.3 JD 去重与抄袭检测

JD 去重需要处理抄袭问题，通过文本级重复检测和能力级异常检测处理模板传播与能力通胀。

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 单条 JD 重复检测 | POST | `/jds/{jd_id}/duplicate-check` | 管理员 | P1 | 是 |
| 批量 JD 重复检测 | POST | `/jds/duplicate-check-batch` | 管理员 | P1 | 是 |
| 获取相似 JD 列表 | GET | `/jds/{jd_id}/similar` | 管理员 | P1 | 是 |
| 获取模板抄袭风险 | GET | `/jds/{jd_id}/copy-risk` | 管理员 | P1 | 是 |
| 设置 JD 降权 | PUT | `/jds/{jd_id}/downweight` | 管理员 | P1 | 是 |

### GET `/jds/{jd_id}/copy-risk`

返回：

```json
{
  "jd_id": "jd_001",
  "copy_risk_score": 0.87,
  "similar_jds": [
    {
      "jd_id": "jd_101",
      "similarity": 0.91,
      "source_name": "猎聘",
      "text_overlap": 0.92,
      "skill_overlap": 0.85,
      "length_similarity": 0.95
    }
  ],
  "recommended_action": "downweight",
  "reason": "基于字符 shingles 文本重合度、技能重合度和长度相似度的确定性重复评分"
}
```

重复评分先做 NFKC、lowercase、标点/空白归一化，再组合
`0.65 * text_overlap + 0.20 * skill_overlap + 0.15 * length_similarity`。
`text_overlap` 使用 character 3-gram/4-gram shingles Jaccard，是主导信号。
没有 candidate 时 `copy_risk_score=0.0`；模板样式本身不会产生重复高分。
该分数用于 copy-risk 治理，不是法律意义的抄袭认定。

---

## 4.4 能力通胀检测

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 检测单条 JD 能力通胀 | POST | `/jds/{jd_id}/inflation-check` | 管理员 | P1 | 是 |
| 批量检测能力通胀 | POST | `/jds/inflation-check-batch` | 管理员 | P1 | 是 |
| 获取能力通胀报告 | GET | `/jds/{jd_id}/inflation-report` | 管理员 | P1 | 是 |
| 标记异常技能项 | PUT | `/jds/{jd_id}/skills/{skill_id}/mark-abnormal` | 管理员 | P1 | 是 |

返回：

```json
{
  "jd_id": "jd_001",
  "inflation_score": 0.90,
  "abnormal_skills": [],
  "mismatch_reasons": [
    "seniority_mismatch: 低职级与整体架构或技术选型职责不一致",
    "experience_mismatch: 低经验要求与高阶责任范围不一致",
    "ownership_mismatch: 低资历要求与主导或端到端负责不一致",
    "leadership_mismatch: 低资历要求与团队管理责任不一致"
  ],
  "recommended_action": "manual_review"
}
```

Inflation 检测关注 career level / 最低经验年限与 architecture、ownership、
leadership 责任范围是否不一致；required skill breadth 仅为最高 `0.10` 的弱信号，
不会单独触发高风险。命中的可解释原因由 `mismatch_reasons` 返回。

上述接口是单文档结构一致性检查。正式岗位画像另有
[`requirement-strength-calibration.v1`](../../../docs/technical/requirement-strength-calibration.md)
市场一致性校准：在同岗位去重 JD 上计算 required prevalence、required purity、跨 JD、
跨企业/来源及 leave-one-out 支持，将 required 技能分为 `market_supported`、
`enterprise_specific` 或 `inflation_risk`。原始 requirement 与 Evidence 保留，单点异常要求
不会被放大为标准岗位的普遍必备能力。

---

# 5. 简历接口

## 5.1 简历上传

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 上传 CV 文件并调度抽取 | POST | `/source-cvs/upload-and-extract` | 个人用户 | P0 | 是 |
| 上传简历文件 | POST | `/resumes/file` | 个人用户 | P0 | 是 |
| 上传简历图片 | POST | `/resumes/image` | 个人用户 | P1 | 是 |
| 粘贴简历文本 | POST | `/resumes/text` | 个人用户 | P0 | 是 |
| 获取我的简历列表 | GET | `/resumes/me` | 个人用户 | P0 | 是 |
| 获取简历详情 | GET | `/resumes/{resume_id}` | 个人用户 / 管理员 | P0 | 是 |
| 删除简历 | DELETE | `/resumes/{resume_id}` | 个人用户 | P1 | 是 |
| 内部 raw-text CV 导入 | POST | `/internal/source-cvs/import-and-extract` | 内部工具 / 测试 | P2 | 是 |
| 获取 CV 抽取审核 | GET | `/cv-extraction-tasks/{task_id}/review` | 个人用户 | P0 | 是 |
| 确认 CV 快照 | POST | `/cv-extraction-tasks/{task_id}/confirm` | 个人用户 | P0 | 是 |
| 获取 CV 快照 | GET | `/validated-cv-snapshots/{snapshot_id}` | 个人用户 | P0 | 是 |
| 创建 CV 快照修订 | POST | `/validated-cv-snapshots/{snapshot_id}/revisions` | 个人用户 | P0 | 是 |

### CV 响应契约

上传、任务查询、Evidence 审核、Snapshot 确认/查询均声明严格 Pydantic
`response_model`，统一外层为 `{ code, message, data, trace_id }`。

- 上传返回固定字段：`source_cv_id`、`source_cv_version_id`、
  `cv_extraction_task_id`、`created_source`、`created_version`、
  `created_task`、`task_status`。
- 任务状态固定为 `pending | running | succeeded | failed`；
  `confirmation_status` 固定为 `pending | confirmed`；
  `validation_conclusion` 固定为 `pass | warn | block`。
- 身份字段使用显式 ID：任务携带 `request_id`、`execution_id`、`review_id`、
  `confirmation_idempotency_id`；Snapshot 携带 `snapshot_revision` 与
  `supersedes_snapshot_id`，不依赖业务哈希或指纹。
- 失败响应在 `data` 内返回稳定错误字段
  `{ "error_code": "...", "message": "..." }`，例如
  `CV_EXTRACTION_TASK_NOT_FOUND`、`CV_SNAPSHOT_NOT_FOUND`、
  `CV_EXTRACTION_CONFLICT`、`CV_EXTRACTION_BLOCKED`、
  `CV_REVIEW_CONFLICT`、`CV_PERSONAL_ROLE_REQUIRED`、
  `CV_REQUEST_INVALID` 及文件输入 `CV_FILE_*` 错误码。
- 确认/修订幂等规则：同一 `idempotency_key` 且身份一致时返回同一
  Snapshot/Resume 结果；`expected_review_id` 过期或载荷身份不同返回
  409 `CV_REVIEW_CONFLICT`。
- Review 端点 `GET /cv-extraction-tasks/{task_id}/review` 返回固定结构：
  - 血缘：`task_id`、`source_cv_id`、`source_cv_version_id`、`status`、
    `confirmation_status`、`review_id`、`review_revision`；
  - 原文：`source_text` 提供可回跳的原始简历文本；
  - 可审核字段 `reviewable_fields`：每项包含 `field_id`、`field_type`、
    `section`、`original_value`、`suggested_value`、`evidence` 与
    `flag_codes`；`evidence` 固定为
    `{ source_document_id, source_id, quote, start, end, alignment,
    occurrence_index }`；
  - Review Flags `review_flags`：每项固定为 `code`、`severity`、
    `rule_scope`、`message`、`suggested_action`、`item_id`；
  - Validation 摘要 `validation`：固定为 `conclusion`、`policy_version`、
    `validation_task_id`、`validation_report_id`、`blocking_reasons`。

---

## 5.2 简历解析

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 启动简历解析 | POST | `/resumes/{resume_id}/parse` | 个人用户 | P0 | 是 |
| 获取简历解析结果 | GET | `/resumes/{resume_id}/parse-result` | 个人用户 | P0 | 是 |
| 编辑简历解析结果 | PUT | `/resumes/{resume_id}/parse-result` | 个人用户 | P0 | 是 |
| 确认简历解析结果 | POST | `/resumes/{resume_id}/parse-result/confirm` | 个人用户 | P0 | 是 |
| 生成技能画像 | POST | `/resumes/{resume_id}/skill-profile` | 个人用户 | P0 | 是 |
| 获取技能画像 | GET | `/resumes/{resume_id}/skill-profile` | 个人用户 | P0 | 是 |

### GET `/resumes/{resume_id}/parse-result`

返回：

```json
{
  "resume_id": "res_001",
  "education": [
    {
      "school": "华中科技大学",
      "major": "软件工程",
      "degree": "本科",
      "start_date": "2025-09",
      "end_date": "2029-06"
    }
  ],
  "projects": [
    {
      "project_name": "岗位能力图谱系统",
      "description": "负责 JD 抽取与图谱构建模块",
      "skills": ["Python", "FastAPI", "Neo4j"]
    }
  ],
  "skills": [
    {
      "raw_skill": "Python",
      "normalized_skill_id": "skill_python",
      "confidence": 0.97,
      "evidence": "项目经历"
    }
  ],
  "parse_confidence": 0.89,
  "need_review": true
}
```

---

# 6. 技能体系与归一化接口

## 6.1 技能词表

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建技能 | POST | `/skills` | 管理员 | P0 | 是 |
| 获取技能列表 | GET | `/skills` | 全部用户 | P0 | 是 |
| 获取技能详情 | GET | `/skills/{skill_id}` | 全部用户 | P0 | 是 |
| 编辑技能 | PUT | `/skills/{skill_id}` | 管理员 | P0 | 是 |
| 删除技能 | DELETE | `/skills/{skill_id}` | 管理员 | P1 | 是 |
| 获取技能类别树 | GET | `/skill-categories/tree` | 全部用户 | P0 | 是 |

### POST `/skills`

请求：

```json
{
  "skill_name": "RAG",
  "category": "大模型应用",
  "description": "检索增强生成技术",
  "parent_skill_id": "skill_llm_app",
  "aliases": ["检索增强生成", "Retrieval-Augmented Generation"]
}
```

---

## 6.2 技能别名

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 添加技能别名 | POST | `/skills/{skill_id}/aliases` | 管理员 | P0 | 是 |
| 获取技能别名 | GET | `/skills/{skill_id}/aliases` | 全部用户 | P0 | 是 |
| 删除技能别名 | DELETE | `/skills/{skill_id}/aliases/{alias_id}` | 管理员 | P1 | 是 |
| 预览技能合并影响 | POST | `/skills/merge/preview` | 管理员 | P1 | 是 |
| 合并技能 | POST | `/skills/merge` | 管理员 | P1 | 是 |
| 拆分技能 | POST | `/skills/{skill_id}/split` | 管理员 | P2 | 否 |

---

## 6.3 技能多维分类

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建分类节点 | POST | `/skill-taxonomy/nodes` | 管理员 | P0 | 是 |
| 获取分类节点 | GET | `/skill-taxonomy/nodes` | 全部用户 | P0 | 是 |
| 编辑分类节点 | PUT | `/skill-taxonomy/nodes/{node_id}` | 管理员 | P0 | 是 |
| 添加技能分类 | POST | `/skills/{skill_id}/classifications` | 管理员 | P0 | 是 |
| 获取技能分类 | GET | `/skills/{skill_id}/classifications` | 全部用户 | P0 | 是 |
| 删除技能分类 | DELETE | `/skills/{skill_id}/classifications/{classification_id}` | 管理员 | P0 | 是 |

分类使用 `concept_class`、`technology_kind` 和 `domain` 三个轴；结构和约束见
[标准技能多维分类](skill-taxonomy.md)。旧 `skills.category` 只保留兼容，不再作为
新分类事实源。

---

## 6.4 技能归一化

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 单个技能归一化 | POST | `/skills/normalize` | 系统 / 管理员 | P0 | 是 |
| 批量技能归一化 | POST | `/skills/normalize-batch` | 系统 / 管理员 | P0 | 是 |
| 获取归一化候选 | GET | `/skills/normalize-candidates` | 管理员 | P0 | 是 |
| 预览技能目录草稿 | GET | `/skills/catalog/draft` | 管理员 | P0 | 是 |
| 发布技能目录版本 | POST | `/skills/catalog/publish` | 管理员 | P0 | 是 |
| 获取最新已发布技能目录 | GET | `/skills/catalog/versions/latest` | 全部用户 | P0 | 是 |
| 获取指定技能目录版本 | GET | `/skills/catalog/versions/{catalog_version}` | 全部用户 | P0 | 是 |
| 重新归一化候选 | POST | `/skills/normalize-candidates/re-normalize` | 管理员 | P0 | 是 |
| 获取下游技能投影 | GET | `/skills/catalog/downstream` | 全部用户 | P0 | 是 |
| 映射到已有技能 | POST | `/skills/normalize-candidates/{candidate_id}/map-existing` | 管理员 | P0 | 是 |
| 创建新标准技能 | POST | `/skills/normalize-candidates/{candidate_id}/create-new` | 管理员 | P0 | 是 |
| 排除非技能内容 | POST | `/skills/normalize-candidates/{candidate_id}/exclude-non-skill` | 管理员 | P0 | 是 |
| 延后审核候选 | POST | `/skills/normalize-candidates/{candidate_id}/defer` | 管理员 | P0 | 是 |

### POST `/skills/normalize`

请求：

```json
{
  "raw_skill": "python开发",
  "context": "熟悉 python开发，了解 FastAPI"
}
```

返回：

```json
{
  "raw_skill": "python开发",
  "candidates": [
    {
      "skill_id": "skill_python",
      "skill_name": "Python",
      "confidence": 0.96
    }
  ],
  "need_review": false
}
```

---

# 7. 标准岗位与岗位能力图谱接口

产品需要展示新一代信息技术岗位全景图谱，颗粒度到技能点级别，并支持按技术栈和级别切换视图。

## 7.1 标准岗位管理

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建标准岗位 | POST | `/positions` | 管理员 | P0 | 是 |
| 获取标准岗位列表 | GET | `/positions` | 全部用户 | P0 | 是 |
| 获取标准岗位详情 | GET | `/positions/{position_id}` | 全部用户 | P0 | 是 |
| 编辑标准岗位 | PUT | `/positions/{position_id}` | 管理员 | P0 | 是 |
| 删除标准岗位 | DELETE | `/positions/{position_id}` | 管理员 | P1 | 是 |
| 获取岗位分类树 | GET | `/position-categories/tree` | 全部用户 | P0 | 是 |

---

## 7.2 岗位能力图谱

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取已发布岗位列表 | GET | `/portal/positions` | 全部用户 | P0 | 是 |
| 获取已发布岗位详情 | GET | `/portal/positions/{position_id}` | 全部用户 | P0 | 是 |
| 获取已发布岗位图谱 | GET | `/portal/positions/{position_id}/graph` | 全部用户 | P0 | 是 |
| 获取图谱关系证据 | GET | `/portal/evidence/relations/{relation_id}` | 全部用户 | P1 | 是 |
| 获取图谱聚合证据 | GET | `/portal/evidence/{kind}/{aggregate_id}` | 全部用户 | P1 | 是 |
| 获取岗位证据源 | GET | `/positions/{position_id}/evidence` | 全部用户 | P0 | 是 |

### GET `/portal/positions/{position_id}/graph`

返回 KG Published Graph Snapshot，包含岗位、版本、技能关系、Evidence 和样本统计；
`graph_version` 固定为 KG Published `graph_version_id` 的字符串形式，与 Matching、Trend 引用一致。

---

## 7.3 图谱版本管理

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取图谱版本列表 | GET | `/portal/admin/knowledge-graph/positions/{position_id}/versions` | 管理员 | P1 | 是 |
| 获取图谱版本详情 | GET | `/portal/admin/knowledge-graph/positions/{position_id}/versions/{version_id}` | 管理员 | P1 | 是 |
| 对比图谱版本 | GET | `/portal/admin/knowledge-graph/positions/{position_id}/versions/diff` | 管理员 | P1 | 是 |
| 回滚图谱版本 | POST | `/portal/admin/knowledge-graph/positions/{position_id}/versions/{version_id}/rollback` | 管理员 | P2 | 是 |

---

# 8. 既有岗位趋势分析接口

你指出：针对特定岗位应输出完整能力图谱和趋势分析报告，而不是只展示能力变化。

## 8.1 趋势分析任务

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建趋势分析任务 | POST | `/positions/{position_id}/trend-analysis/tasks` | 管理员 | P0 | 是 |
| 获取趋势分析任务状态 | GET | `/trend-analysis/tasks/{task_id}` | 管理员 | P0 | 是 |
| 获取趋势分析报告列表 | GET | `/positions/{position_id}/trend-reports` | 全部用户 | P0 | 是 |
| 获取趋势分析报告详情 | GET | `/trend-reports/{report_id}` | 全部用户 | P0 | 是 |
| 编辑趋势分析报告 | PUT | `/trend-reports/{report_id}` | 管理员 | P1 | 是 |
| 发布趋势分析报告 | POST | `/trend-reports/{report_id}/publish` | 管理员 | P1 | 是 |
| 导出趋势分析报告 | GET | `/trend-reports/{report_id}/export` | 全部用户 | P1 | 是 |

### POST `/positions/{position_id}/trend-analysis/tasks`

请求：

```json
{
  "time_window_start": "2026-01-01",
  "time_window_end": "2026-07-01",
  "compare_with_previous_window": true,
  "source_types": ["jd", "enterprise_website"],
  "generate_llm_summary": true
}
```

返回：

```json
{
  "task_id": "trend_task_001",
  "position_id": "pos_java",
  "status": "running"
}
```

---

## 8.2 趋势报告内容接口

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取当前完整能力图谱 | GET | `/trend-reports/{report_id}/current-graph` | 全部用户 | P0 | 是 |
| 获取技能权重分布 | GET | `/trend-reports/{report_id}/skill-weight-distribution` | 全部用户 | P0 | 是 |
| 获取新增技能趋势 | GET | `/trend-reports/{report_id}/new-skills` | 全部用户 | P0 | 是 |
| 获取上升技能趋势 | GET | `/trend-reports/{report_id}/rising-skills` | 全部用户 | P0 | 是 |
| 获取下降技能趋势 | GET | `/trend-reports/{report_id}/declining-skills` | 全部用户 | P0 | 是 |
| 获取被替代技能 | GET | `/trend-reports/{report_id}/replaced-skills` | 全部用户 | P1 | 是 |
| 获取技能组合迁移 | GET | `/trend-reports/{report_id}/skill-combo-shifts` | 全部用户 | P1 | 是 |
| 获取风险提示 | GET | `/trend-reports/{report_id}/risks` | 全部用户 | P1 | 是 |
| 获取趋势解释 | GET | `/trend-reports/{report_id}/summary` | 全部用户 | P0 | 是 |

---

# 9. 岗位聚类接口

岗位簇必须由算法自动形成，不能人工选定。

## 9.1 聚类任务

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建岗位聚类任务 | POST | `/position-clusters/tasks` | 管理员 | P0 | 是 |
| 获取聚类任务状态 | GET | `/position-clusters/tasks/{task_id}` | 管理员 | P0 | 是 |
| 获取岗位簇列表 | GET | `/position-clusters` | 管理员 | P0 | 是 |
| 获取岗位簇详情 | GET | `/position-clusters/{cluster_id}` | 管理员 | P0 | 是 |
| 获取簇内 JD 样本 | GET | `/position-clusters/{cluster_id}/jds` | 管理员 | P0 | 是 |
| 获取簇核心技能 | GET | `/position-clusters/{cluster_id}/core-skills` | 管理员 | P0 | 是 |
| 获取簇代表文本 | GET | `/position-clusters/{cluster_id}/representatives` | 管理员 | P1 | 是 |
| 删除异常岗位簇 | DELETE | `/position-clusters/{cluster_id}` | 管理员 | P1 | 是 |

### POST `/position-clusters/tasks`

请求：

```json
{
  "time_window_start": "2026-01-01",
  "time_window_end": "2026-07-01",
  "source_types": ["jd"],
  "embedding_fields": ["title", "responsibilities", "skills"],
  "cluster_algorithm": "hdbscan",
  "min_cluster_size": 10
}
```

返回：

```json
{
  "task_id": "cluster_task_001",
  "status": "running"
}
```

---

# 10. 新兴岗位发现接口

产品需要识别市场上正在萌芽但尚未被标准化或逐渐兴起的新岗位，并生成岗位定义，包括岗位名称、核心职责、必备技能、加分技能、典型行业应用场景。

## 10.1 新兴岗位候选

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 从岗位簇生成新兴岗位候选 | POST | `/emerging-positions/from-cluster/{cluster_id}` | 管理员 | P0 | 是 |
| 获取新兴岗位候选列表 | GET | `/emerging-positions` | 管理员 / 企业 / 个人 | P0 | 是 |
| 获取新兴岗位候选详情 | GET | `/emerging-positions/{emerging_id}` | 管理员 / 企业 / 个人 | P0 | 是 |
| 编辑新兴岗位定义 | PUT | `/emerging-positions/{emerging_id}` | 管理员 | P0 | 是 |
| 删除新兴岗位候选 | DELETE | `/emerging-positions/{emerging_id}` | 管理员 | P1 | 是 |
| 发布为新兴岗位 | POST | `/emerging-positions/{emerging_id}/publish` | 管理员 | P0 | 是 |
| 转入标准岗位图谱 | POST | `/emerging-positions/{emerging_id}/promote-to-position` | 管理员 | P1 | 是 |

---

## 10.2 旧版兼容评分接口

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 计算旧版兼容评分 | POST | `/emerging-positions/{emerging_id}/germination-score` | 管理员 | P0 | 是 |
| 获取旧版兼容评分详情 | GET | `/emerging-positions/{emerging_id}/germination-score` | 全部用户 | P0 | 是 |
| 编辑旧版兼容评分参数 | PUT | `/emerging-positions/score-config` | 管理员 | P1 | 是 |
| 获取旧版兼容评分参数 | GET | `/emerging-positions/score-config` | 管理员 | P1 | 是 |

返回：

```json
{
  "emerging_id": "emg_001",
  "germination_score": 0.82,
  "dimensions": {
    "cluster_growth_rate": 0.86,
    "skill_combo_novelty": 0.91,
    "source_diversity": 0.72,
    "industry_spread": 0.66,
    "distance_from_existing_positions": 0.84,
    "sample_size_penalty": -0.08,
    "single_platform_noise_penalty": -0.05
  },
  "level": "high_potential"
}
```

---

## 10.3 LLM 生成岗位定义

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 生成新兴岗位定义 | POST | `/emerging-positions/{emerging_id}/generate-definition` | 管理员 | P0 | 是 |
| 获取生成版本列表 | GET | `/emerging-positions/{emerging_id}/definition-versions` | 管理员 | P1 | 是 |
| 选择生成版本 | POST | `/emerging-positions/{emerging_id}/definition-versions/{version_id}/select` | 管理员 | P1 | 是 |

请求：

```json
{
  "use_evidence_only": true,
  "include_required_skills": true,
  "include_bonus_skills": true,
  "include_industry_scenarios": true
}
```

返回：

```json
{
  "position_name": "AI Agent 应用开发工程师",
  "core_responsibilities": [
    "负责基于大模型的 Agent 应用开发",
    "设计工具调用、任务规划与记忆模块"
  ],
  "required_skills": ["Python", "LLM API", "RAG", "Agent Framework"],
  "bonus_skills": ["LangGraph", "向量数据库", "多智能体协作"],
  "industry_scenarios": ["智能客服", "企业知识库", "自动化办公"],
  "evidence_ids": ["jd_001", "jd_020", "jd_088"]
}
```

---

## 10.4 候选生命周期（Candidate Lifecycle）

Emerging Discovery 的跨窗口候选（`candidate_id` 稳定，可跨 Discovery Run / 时间窗口追踪）
通过主系统 BFF 只读代理暴露，与 `EmergingPosition` 治理对象分离。前端只访问主系统 BFF。

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 候选生命周期列表 | GET | `/portal/admin/discovery-candidates` | 管理员 | P0 | 是 |
| 候选生命周期详情 | GET | `/portal/admin/discovery-candidates/{candidate_id}` | 管理员 | P0 | 是 |
| 候选跨窗口轨迹 | GET | `/portal/admin/discovery-candidates/{candidate_id}/trajectory` | 管理员 | P0 | 是 |
| 候选进入治理门禁 | POST | `/portal/admin/discovery-candidates/{candidate_id}/enter-governance` | 管理员 | P0 | 是 |

列表过滤参数：`status`（`weak_signal` / `incubating` / `emerging_candidate` /
`stable_emerging_role` / `dead` / `noise`）、`candidate_id`、`window_id`。

列表响应：

```json
{
  "candidates": [
    {
      "candidate_id": "cand_001",
      "status": "stable_emerging_role",
      "first_seen_window_id": "2026-01",
      "last_seen_window_id": "2026-06",
      "age": 6,
      "current_cluster_id": "cluster_004",
      "previous_cluster_ids": ["cluster_001", "cluster_002", "cluster_003"],
      "canonical_title": "AI Agent Developer",
      "display_title": "Agent Engineer",
      "identity_profile": {
        "titles": ["AI Agent Developer", "Agent Engineer"],
        "skills": ["Python", "RAG", "Agent"],
        "responsibilities": ["构建智能体应用"],
        "member_jd_ids": ["jd_001", "jd_002", "jd_003"],
        "observed_window_ids": ["2026-01", "2026-02", "2026-03", "2026-06"]
      },
      "emergence_score": 0.78,
      "evidence": {}
    }
  ],
  "filters": {"status": null, "candidate_id": null, "window_id": null}
}
```

轨迹响应 `data.trajectory[]` 每个节点表达：`window_id`、`run_id`（Discovery Run）、
`cluster_id`/`cluster_name`、`title`、`status`、`emergence_score`、`support_count`、
`company_count`、身份/技能/职责/标题相似度、`evidence` 与 `match_evidence`。

Contract 校验：候选 / 观测必填字段损坏、状态非法、detail / trajectory 的
`candidate_id` 与路径不一致均返回 502 `emerging_discovery_contract_error`；
错误转换：上游 404 → 主系统 404；超时 → 503；5xx → 原状态透传；非法 JSON → 502。

治理门禁（`enter-governance`）：仅 `stable_emerging_role` 且当前 Cluster 已投影主系统岗位簇
的候选可创建 EmergingPosition（409 `candidate_lifecycle_gate_rejected` /
`candidate_lifecycle_cluster_missing` / `candidate_lifecycle_cluster_not_projected`）；
内部复用 `POST /emerging-positions/from-cluster/{cluster_id}` 的幂等语义，不做自动
review / publish / promote。

---

# 11. 预测岗位分析接口

预测岗位与新兴岗位分开。预测岗位主要来自政策、报告、论文、网页信息等多源趋势信号，并关注国家战略需求、政府文件报告、新兴媒体报告、最新论文和网页信息。

## 11.1 趋势源接入

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 上传政策文件 | POST | `/trend-sources/policy` | 管理员 | P2 | 接口保留 |
| 上传行业报告 | POST | `/trend-sources/report` | 管理员 | P2 | 接口保留 |
| 上传论文摘要 | POST | `/trend-sources/paper` | 管理员 | P2 | 接口保留 |
| 上传网页材料 | POST | `/trend-sources/webpage` | 管理员 | P2 | 接口保留 |
| 获取趋势源列表 | GET | `/trend-sources` | 管理员 | P2 | 接口保留 |
| 获取趋势源详情 | GET | `/trend-sources/{source_id}` | 管理员 | P2 | 接口保留 |
| 编辑趋势源 | PUT | `/trend-sources/{source_id}` | 管理员 | P2 | 接口保留 |
| 删除趋势源 | DELETE | `/trend-sources/{source_id}` | 管理员 | P2 | 接口保留 |

---

## 11.2 预测岗位生成

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建预测岗位分析任务 | POST | `/predicted-positions/tasks` | 管理员 | P2 | 接口保留 |
| 获取预测任务状态 | GET | `/predicted-positions/tasks/{task_id}` | 管理员 | P2 | 接口保留 |
| 获取预测岗位列表 | GET | `/predicted-positions` | 管理员 / 企业 / 个人 | P2 | 接口保留 |
| 获取预测岗位详情 | GET | `/predicted-positions/{predicted_id}` | 管理员 / 企业 / 个人 | P2 | 接口保留 |
| 编辑预测岗位定义 | PUT | `/predicted-positions/{predicted_id}` | 管理员 | P2 | 接口保留 |
| 计算预测可信度 | POST | `/predicted-positions/{predicted_id}/confidence-score` | 管理员 | P2 | 接口保留 |
| 获取预测可信度 | GET | `/predicted-positions/{predicted_id}/confidence-score` | 全部用户 | P2 | 接口保留 |
| 发布到预测图谱 | POST | `/predicted-positions/{predicted_id}/publish` | 管理员 | P2 | 接口保留 |

---

# 12. 证据源与 Evidence 接口

Evidence 与引用用于控制 LLM 幻觉，提升能力图谱构建科学性。

## 12.1 证据源管理

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建证据源 | POST | `/evidence-sources` | 系统 / 管理员 | P0 | 是 |
| 获取证据源列表 | GET | `/evidence-sources` | 管理员 | P0 | 是 |
| 获取证据源详情 | GET | `/evidence-sources/{evidence_id}` | 管理员 | P0 | 是 |
| 编辑证据源 | PUT | `/evidence-sources/{evidence_id}` | 管理员 | P1 | 是 |
| 删除证据源 | DELETE | `/evidence-sources/{evidence_id}` | 管理员 | P1 | 是 |
| 获取某技能证据源 | GET | `/skills/{skill_id}/evidence` | 全部用户 | P0 | 是 |
| 获取某岗位证据源 | GET | `/positions/{position_id}/evidence` | 全部用户 | P0 | 是 |
| 获取某关系证据源 | GET | `/relations/{relation_id}/evidence` | 全部用户 | P1 | 是 |

---

## 12.2 Evidence 检索

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 证据检索 | POST | `/evidence/retrieve` | 系统 / 管理员 | P1 | 是 |
| 生成带证据回答 | POST | `/evidence/generate` | 系统 / 管理员 | P1 | 是 |
| 校验生成内容证据覆盖 | POST | `/evidence/validate` | 系统 / 管理员 | P1 | 是 |
| 获取低证据结果 | GET | `/evidence/low-evidence-results` | 管理员 | P1 | 是 |

### POST `/evidence/validate`

请求：

```json
{
  "generated_text": "AI Agent 工程师需要掌握 LangGraph 和 RAG",
  "evidence_ids": ["jd_001", "report_003"],
  "strict_mode": true
}
```

返回：

```json
{
  "valid": true,
  "unsupported_claims": [],
  "coverage_score": 0.92
}
```

---

## 12.3 Evidence RAG BFF

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| Evidence RAG 查询 | POST | `/rag/evidence` | 登录用户 | P0 | 是 |
| Evidence RAG 索引 | POST | `/rag/evidence/index` | 管理员 / 开发者 | P1 | 是 |
| Evidence RAG 失效 | POST | `/rag/evidence/invalidate` | 管理员 / 开发者 | P1 | 是 |
| Evidence RAG 删除 | DELETE | `/rag/evidence` | 管理员 / 开发者 | P1 | 是 |

### POST `/rag/evidence`

BFF 端点在服务端装配租户、权限范围和 `assembled_by`，不信任浏览器传入的
`permission` / `assembled_by`。返回结构遵循 `evidence-rag-response.v1`：

```json
{
  "status": "answered",
  "answer": "候选人具备 RAG 项目经验。",
  "references": [
    {
      "evidence_id": "evidence-1",
      "source_object_type": "validated_cv_snapshot",
      "source_object_id": "snapshot-1",
      "source_document_id": "document-1",
      "quote": "负责 RAG 应用开发",
      "location_start": 0,
      "location_end": 9,
      "alignment": "exact",
      "graph_version_id": 7,
      "source_version": "v1",
      "tenant_ref": "jobgraph-platform-public",
      "permission_scope": "platform:public"
    }
  ],
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "model_version": "deepseek-evidence-rag-answer.v1",
  "permission": {
    "user_id": "account-1",
    "tenant_ref": "jobgraph-platform-public",
    "permission_scope": "platform:public",
    "assembled_by": "main-system-bff"
  }
}
```

`status` 固定为 `answered` / `insufficient_evidence` / `failed`。无有效 Evidence
时返回 `insufficient_evidence`，Provider 或检索不可用时返回 `failed`，两者都带
稳定 `error.code`。详细契约见 `docs/evidence-rag-bff.md`。

---

# 13. 向量与 embedding 接口

权重不放向量数据库作为主存储。权重、置信度、趋势分数应放结构化数据库；向量库主要存 embedding，用于相似度检索、聚类和证据检索。

## 13.1 embedding 生成

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 生成 JD embedding | POST | `/embeddings/jds/{jd_id}` | 系统 / 管理员 | P0 | 是 |
| 批量生成 JD embedding | POST | `/embeddings/jds/batch` | 管理员 | P0 | 是 |
| 生成简历 embedding | POST | `/embeddings/resumes/{resume_id}` | 系统 | P0 | 是 |
| 生成技能 embedding | POST | `/embeddings/skills/{skill_id}` | 系统 / 管理员 | P0 | 是 |
| 生成岗位 embedding | POST | `/embeddings/positions/{position_id}` | 系统 / 管理员 | P0 | 是 |
| 生成证据 embedding | POST | `/embeddings/evidence/{evidence_id}` | 系统 / 管理员 | P1 | 是 |
| 生成关系 embedding | POST | `/embeddings/relations/{relation_id}` | 系统 / 管理员 | P1 | 是 |

---

## 13.2 向量检索

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 相似 JD 检索 | POST | `/vectors/search/jds` | 系统 / 管理员 | P0 | 是 |
| 相似岗位检索 | POST | `/vectors/search/positions` | 系统 / 管理员 | P0 | 是 |
| 相似技能检索 | POST | `/vectors/search/skills` | 系统 / 管理员 | P0 | 是 |
| 相似简历检索 | POST | `/vectors/search/resumes` | 企业用户 / 管理员 | P1 | 是 |
| 证据文本检索 | POST | `/vectors/search/evidence` | 系统 / 管理员 | P1 | 是 |
| 技能组合相似度计算 | POST | `/vectors/similarity/skill-combo` | 系统 / 管理员 | P0 | 是 |
| 岗位关系相似度计算 | POST | `/vectors/similarity/position-relation` | 系统 / 管理员 | P1 | 是 |

---

# 14. 人岗匹配接口

产品需要支持多维度匹配分析，提供针对性改进建议和岗位学习路径规划。

## 14.1 匹配任务

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建人岗匹配任务 | POST | `/matches/tasks` | 个人 / 企业 | P0 | 是 |
| 获取匹配任务状态 | GET | `/matches/tasks/{task_id}` | 个人 / 企业 | P0 | 是 |
| 获取匹配报告列表 | GET | `/matches/reports` | 个人 / 企业 | P0 | 是 |
| 获取匹配报告详情 | GET | `/matches/reports/{evaluation_id}` | 个人 / 企业 | P0 | 是 |
| 导出匹配报告 | GET | `/matches/reports/{evaluation_id}/export` | 个人 / 企业 | P1 | 是 |

### POST `/matches/tasks`

请求：

```json
{
  "resume_id": "res_001",
  "target_type": "standard_position",
  "target_id": "pos_java",
  "use_enterprise_weights": false,
  "generate_learning_path": true
}
```

返回：

```json
{
  "task_id": "match_task_001",
  "status": "running"
}
```

---

## 14.2 匹配报告内容

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取统一 Evaluation | GET | `/matches/reports/{evaluation_id}` | 个人 / 企业 | P0 | 是 |
| 导出统一 Evaluation | GET | `/matches/reports/{evaluation_id}/export` | 个人 / 企业 | P1 | 是 |

### GET `/matches/reports/{evaluation_id}`

返回：

```json
{
  "evaluation_id": "evaluation_001",
  "status": "current",
  "evaluation": {
    "evaluation_status": "completed",
    "algorithm_version": "deterministic-matching.v5",
    "hard_constraint_results": [],
    "skill_results": [],
    "final_match_result": {
      "overall_score": 78.0,
      "dimension_scores": [],
      "score_contributions": [],
      "position_graph_version": "graph-42"
    }
  },
  "gap_analysis": {
    "generation_status": "completed",
    "learning_path": [],
    "counterfactual_suggestions": []
  }
}
```

匹配报告、Gap 分析、Learning Path 的主系统 BFF 接口均声明严格响应模型
（`extra=forbid`，禁止任意 `dict` 透传）。任务状态固定为
`pending | running | succeeded | failed | cancelled`；报告状态固定为
`pending | running | succeeded | failed | cancelled | current | stale`；
结果状态 `result_status` 固定为
`completed | empty | failed | cancelled | insufficient_data`。

`evaluation` 与 `gap_analysis` 内的每条 Evidence 固定返回：

```json
{
  "source_object_type": "validated_cv_snapshot | position_profile | skill_relation | matching_evidence",
  "source_object_id": "snapshot-1",
  "source_document_id": "source_cv_version_id | position_source_version | graph_version | evaluation_id",
  "source_fragment_id": "snapshot-skill:0",
  "quote": "熟练使用 Python",
  "start": 0,
  "end": 11,
  "alignment": "exact",
  "occurrence_index": 0,
  "version": {
    "validated_cv_snapshot_id": "snapshot-1",
    "source_cv_version_id": "version-1",
    "resume_id": "resume-1",
    "position_id": "position-1",
    "graph_version": "graph-42",
    "source_jd_version_id": "position-source-version",
    "evaluation_id": "evaluation-1"
  },
  "result_reference": "validated_cv_snapshot:snapshot-1#evidence:snapshot-skill:0:0-11"
}
```

`result_reference` 固定为
`{source_object_type}:{source_object_id}#evidence:{source_fragment_id}[:start-end]`。
血缘使用显式身份字段：`cv_profile_id / cv_profile_version`、
`position_profile_id / position_profile_version`、`source_evaluation_id`、
`profile_version`，不携带业务哈希或指纹。空结果、失败、取消和数据不足由
`result_status` 与 `error_code / error_message` 结构化表示。

---

## 14.3 企业候选人匹配

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 浏览已发布企业岗位 | GET | `/published-enterprise-jobs` | 个人用户 | P0 | 是 |
| 查看已发布企业岗位 | GET | `/published-enterprise-jobs/{job_id}` | 个人用户 | P0 | 是 |
| 获取本人可投递简历与投递状态 | GET | `/enterprise-jobs/{job_id}/candidate-submission-options` | 个人用户 | P0 | 是 |
| 投递本人简历 | POST | `/enterprise-jobs/{job_id}/candidate-submissions` | 个人用户 | P0 | 是 |
| 撤销本人投递 | PUT | `/enterprise-jobs/{job_id}/candidate-submissions/{resume_id}/revoke` | 个人用户 | P0 | 是 |
| 获取候选人投递池 | GET | `/enterprise-jobs/{job_id}/candidate-submissions` | 企业用户 | P1 | 是 |
| 企业岗位匹配候选人 | POST | `/enterprise-jobs/{job_id}/match-submissions` | 企业用户 | P1 | 是 |
| 获取候选人匹配列表 | GET | `/enterprise-jobs/{job_id}/match-reports` | 企业用户 | P1 | 是 |
| 获取候选人匹配详情 | GET | `/enterprise-jobs/{job_id}/match-reports/{evaluation_id}` | 企业用户 | P1 | 是 |
| 获取候选决策板 | GET | `/enterprise-jobs/{job_id}/candidate-decision-board` | 企业用户 | P1 | 是 |
| 获取招聘决策分歧审计 | GET | `/enterprise-jobs/{job_id}/decision-audit` | 企业用户 | P2 | 是 |
| 回放单个审计案例 | GET | `/enterprise-jobs/{job_id}/decision-audit/cases/{evaluation_id}` | 企业用户 | P2 | 是 |
| 标记候选人合适 | POST | `/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-fit` | 企业用户 | P2 | 接口保留 |
| 标记候选人不合适 | POST | `/enterprise-jobs/{job_id}/candidates/{resume_id}/mark-unfit` | 企业用户 | P2 | 接口保留 |

公开岗位 Contract 只返回 `published`；draft 对个人用户不可见。submission options 只枚举当前
个人用户自己的简历，并显式给出 validated snapshot 前置条件。企业角色不能代个人创建
submission；revoke 后该授权不再允许 matching，Board 中也不参与排名。Board 和招聘工作台
必须区分真实空数据、403 与 503，并保留稳定错误码和 `trace_id`。

---

# 15. 学习路径接口

## 15.1 学习路径生成

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建学习路径 | POST | `/learning-paths` | 个人用户 | P0 | 是 |
| 获取学习路径列表 | GET | `/learning-paths` | 个人用户 | P0 | 是 |
| 获取学习路径详情 | GET | `/learning-paths/{path_id}` | 个人用户 | P0 | 是 |
| 编辑学习路径 | PUT | `/learning-paths/{path_id}` | 个人用户 | P1 | 否 |
| 删除学习路径 | DELETE | `/learning-paths/{path_id}` | 个人用户 | P1 | 否 |
| 导出学习路径 | GET | `/learning-paths/{path_id}/export` | 个人用户 | P1 | 是 |

### POST `/learning-paths`

请求：

```json
{
  "evaluation_id": "evaluation_001",
  "target_position_id": "pos_java",
  "time_budget_hours": 20
}
```

返回：

```json
{
  "path_id": "learning-path:lp_001",
  "evaluation_id": "evaluation_001",
  "target_position_id": "pos_java",
  "time_budget_hours": 20,
  "stages": [
    {
      "stage": 1,
      "title": "补齐 Spring Boot 基础",
      "skills": ["Spring Boot", "REST API"],
      "estimated_weeks": 2
    }
  ]
}
```

列表、详情和导出均从主系统持久化记录恢复，并使用相同 ownership：普通用户只能读取自己的
Path；不可访问 Path 返回 404。每条记录保留来源 Evaluation、target position、预算、
algorithm/data versions 与 CV/Position lineage。相同 Evaluation 使用不同预算重新规划时产生
不同 `path_id`，不会覆盖历史记录；stale 或本地版本漂移的 Evaluation 返回冲突且不写入记录。

---

# 16. 人工审核接口

该模块覆盖“所有输入保留编辑接口”和“人工优化与动态更新”两项要求。

## 16.1 审核任务

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 创建审核任务 | POST | `/review-tasks` | 系统 / 管理员 | P0 | 是 |
| 获取待审核任务列表 | GET | `/review-tasks` | 管理员 / 审核员 | P0 | 是 |
| 获取审核任务详情 | GET | `/review-tasks/{task_id}` | 管理员 / 审核员 | P0 | 是 |
| 领取审核任务 | POST | `/review-tasks/{task_id}/claim` | 审核员 | P1 | 是 |
| 提交审核通过 | POST | `/review-tasks/{task_id}/approve` | 审核员 | P0 | 是 |
| 提交审核驳回 | POST | `/review-tasks/{task_id}/reject` | 审核员 | P0 | 是 |
| 提交审核修改 | PUT | `/review-tasks/{task_id}/modify` | 审核员 | P0 | 是 |
| 获取审核历史 | GET | `/review-tasks/{task_id}/history` | 管理员 | P1 | 是 |

图谱结论审核（Knowledge Graph）代理路由：

| 接口名称 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 获取 KG 审核任务列表 | GET | `/portal/admin/knowledge-graph/review-tasks` | 代理 KG `/api/v1/review-tasks`，支持 `page`/`page_size`/`status`/`task_kind`/`risk_level` 参数，默认 `page_size=100` |
| 执行 KG 审核操作 | POST | `/portal/admin/knowledge-graph/review-tasks/{task_id}/{action}` | `action` ∈ `claim`/`approve`/`reject`/`modify`，请求体 `{reason, payload}` |

---

## 16.2 审核对象类型

```text id="qav9re"
jd_parse_result
resume_parse_result
skill_normalization
position_skill_relation
emerging_position
predicted_position
trend_report
match_report_feedback
graph_version
```

### POST `/review-tasks`

请求：

```json
{
  "object_type": "emerging_position",
  "object_id": "emg_001",
  "priority": "high",
  "reason": "新兴岗位候选需要人工审核后入库"
}
```

返回：

```json
{
  "review_task_id": "review_001",
  "status": "pending"
}
```

---

# 17. 反馈接口

## 17.1 个人反馈

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 反馈简历解析错误 | POST | `/feedback/resume-parse` | 个人用户 | P1 | 是 |
| 反馈匹配结果 | POST | `/feedback/match-report` | 个人用户 | P1 | 是 |
| 反馈学习路径 | POST | `/feedback/learning-path` | 个人用户 | P2 | 否 |

---

## 17.2 企业反馈

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 反馈 JD 解析错误 | POST | `/feedback/jd-parse` | 企业用户 | P1 | 是 |
| 反馈岗位技能权重 | POST | `/feedback/skill-weight` | 企业用户 | P1 | 是 |
| 反馈候选人匹配结果 | POST | `/feedback/candidate-match` | 企业用户 | P2 | 接口保留 |
| 反馈岗位需求变化 | POST | `/feedback/job-requirement-change` | 企业用户 | P2 | 接口保留 |

---

# 18. 文件与 OCR 接口

## 18.1 文件上传

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 上传通用文件 | POST | `/files/upload` | 全部用户 | P0 | 是 |
| 获取文件详情 | GET | `/files/{file_id}` | 文件拥有者 / 管理员 | P0 | 是 |
| 删除文件 | DELETE | `/files/{file_id}` | 文件拥有者 / 管理员 | P1 | 是 |
| 文件预览 | GET | `/files/{file_id}/preview` | 文件拥有者 / 管理员 | P1 | 是 |

---

## 18.2 OCR

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 图片 OCR | POST | `/ocr/image` | 系统 / 用户 | P1 | 是 |
| PDF OCR | POST | `/ocr/pdf` | 系统 / 用户 | P1 | 是 |
| 获取 OCR 结果 | GET | `/ocr/tasks/{task_id}` | 系统 / 用户 | P1 | 是 |
| 编辑 OCR 结果 | PUT | `/ocr/results/{result_id}` | 用户 / 管理员 | P1 | 是 |

---

# 20. 系统配置接口

## 20.1 算法配置

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取算法配置 | GET | `/system/config/algorithms` | 管理员 | P1 | 是 |
| 修改算法配置 | PUT | `/system/config/algorithms` | 管理员 | P1 | 是 |
| 获取 LLM 配置 | GET | `/system/config/llm` | 管理员 | P1 | 是 |
| 修改 LLM 配置 | PUT | `/system/config/llm` | 管理员 | P1 | 是 |
| 获取 embedding 配置 | GET | `/system/config/embedding` | 管理员 | P1 | 是 |
| 修改 embedding 配置 | PUT | `/system/config/embedding` | 管理员 | P1 | 是 |
| 获取聚类配置 | GET | `/system/config/clustering` | 管理员 | P1 | 是 |
| 修改聚类配置 | PUT | `/system/config/clustering` | 管理员 | P1 | 是 |

---

## 20.2 权重配置

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取匹配权重配置 | GET | `/system/config/match-weights` | 管理员 | P1 | 是 |
| 修改匹配权重配置 | PUT | `/system/config/match-weights` | 管理员 | P1 | 是 |
| 获取旧版兼容评分权重 | GET | `/system/config/germination-score` | 管理员 | P1 | 是 |
| 修改旧版兼容评分权重 | PUT | `/system/config/germination-score` | 管理员 | P1 | 是 |
| 获取趋势分析权重 | GET | `/system/config/trend-analysis` | 管理员 | P2 | 接口保留 |
| 修改趋势分析权重 | PUT | `/system/config/trend-analysis` | 管理员 | P2 | 接口保留 |

---

# 21. 日志与运行状态接口

## 21.1 任务日志

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 获取任务列表 | GET | `/tasks` | 管理员 | P0 | 是 |
| 获取任务详情 | GET | `/tasks/{task_id}` | 管理员 | P0 | 是 |
| 取消任务 | POST | `/tasks/{task_id}/cancel` | 管理员 | P1 | 是 |
| 重试任务 | POST | `/tasks/{task_id}/retry` | 管理员 | P1 | 是 |
| 获取任务日志 | GET | `/tasks/{task_id}/logs` | 管理员 | P1 | 是 |
| 获取演示任务统一投影 | GET | `/portal/admin/demo-tasks` | `integration.status.view` | P1 | 是 |

### GET `/portal/admin/demo-tasks`

默认仅返回当前演示任务链的 `jd_extraction`、`cv_extraction`、`trend`、`discovery`、
`matching` 任务；可按 `task_type`、`status`、`object_id` 过滤。每项字段固定为
`task_id`、`task_type`、`object_type`、`object_id`、`service`、`status`、`progress`、
`error`、`result_reference`、`created_at`、`updated_at`，状态限定为
`pending/running/succeeded/failed/cancelled`。

---

## 21.2 系统状态

| 接口名称 | 方法 | 路径 | 调用方 | 优先级 | 一期实现 |
|---|---|---|---|---|---|
| 健康检查 | GET | `/health` | 系统 | P0 | 是 |
| 获取系统状态 | GET | `/system/status` | 管理员 | P1 | 是 |
| 获取数据库状态 | GET | `/system/status/databases` | 管理员 | P1 | 是 |
| 获取向量库状态 | GET | `/system/status/vector-db` | 管理员 | P1 | 是 |
| 获取模型服务状态 | GET | `/system/status/model-services` | 管理员 | P1 | 是 |

---

# 22. 前端页面对应接口映射

## 22.1 个人端页面

| 页面 | 主要接口 |
|---|---|
| 登录页 | `/auth/login` |
| 简历上传页 | `/source-cvs/upload-and-extract`, `/resumes/file`, `/resumes/text`, `/resumes/image` |
| 简历解析页 | `/resumes/{resume_id}/parse-result` |
| 技能画像页 | `/resumes/{resume_id}/skill-profile` |
| 目标岗位选择页 | `/positions`, `/emerging-positions`, `/enterprise-jobs` |
| 匹配报告页 | `/matches/reports/{evaluation_id}` |
| 六维雷达图 | `/matches/reports/{evaluation_id}/radar` |
| 匹配报告与学习路径详情 | `/matches/reports/{evaluation_id}?pathId={path_id}`；使用 `/learning-paths`、`/learning-paths/{path_id}`、`/learning-paths/{path_id}/export` |

---

## 22.2 企业端页面

| 页面 | 主要接口 |
|---|---|
| 企业登录页 | `/auth/login` |
| 企业资料页 | `/enterprises/me` |
| 岗位管理页 | `/enterprise-jobs` |
| 岗位编辑页 | `/enterprise-jobs/{job_id}` |
| 招聘人数调整 | `/enterprise-jobs/{job_id}/headcount` |
| 暂停/恢复/撤销 | `/enterprise-jobs/{job_id}/pause`, `/resume`, `/cancel` |
| 技能权重配置页 | `/enterprise-jobs/{job_id}/skill-weights` |
| 岗位图谱页 | `/portal/positions/{position_id}/graph` |
| 候选人匹配页 | `/enterprise-jobs/{job_id}/match-reports` |

---

## 22.3 管理审核端页面

| 页面 | 主要接口 |
|---|---|
| 数据源管理 | `/jds`, `/trend-sources`, `/evidence-sources` |
| JD 审核 | `/jds/{jd_id}/parse-result`, `/review-tasks` |
| 简历审核 | `/resumes/{resume_id}/parse-result`, `/review-tasks` |
| 技能词表管理 | `/skills`, `/skills/normalize-candidates` |
| 岗位图谱管理 | `/portal/admin/knowledge-graph/positions/{position_id}/versions` |
| 趋势报告管理 | `/trend-reports/{report_id}` |
| 岗位聚类管理 | `/position-clusters` |
| 新兴岗位审核 | `/emerging-positions` |
| 预测岗位审核 | `/predicted-positions` |
| 系统配置 | `/system/config/*` |
| 演示任务状态 | `/portal/admin/demo-tasks` |

---

# 23. 一期最小闭环必须实现接口集合

## 23.1 P0 必须完成

```text id="dikd5x"
认证：
POST /auth/login
POST /auth/register
GET  /auth/me

企业：
POST /enterprises
GET  /enterprises/me
POST /enterprise-jobs
GET  /enterprise-jobs
GET  /enterprise-jobs/{job_id}
PUT  /enterprise-jobs/{job_id}

JD：
POST /jds/text
POST /jds/file
POST /jds/batch
GET  /jds
GET  /jds/{jd_id}
POST /jds/{jd_id}/parse
GET  /jds/{jd_id}/parse-result
PUT  /jds/{jd_id}/parse-result

简历：
POST /resumes/file
POST /resumes/text
GET  /resumes/me
GET  /resumes/{resume_id}
POST /resumes/{resume_id}/parse
GET  /resumes/{resume_id}/parse-result
PUT  /resumes/{resume_id}/parse-result
POST /resumes/{resume_id}/skill-profile
GET  /resumes/{resume_id}/skill-profile

技能：
GET  /skills
POST /skills
POST /skills/normalize
POST /skills/normalize-batch

岗位图谱（Portal Published）：
GET  /positions
POST /positions
GET  /positions/{position_id}
GET  /portal/positions
GET  /portal/positions/{position_id}
GET  /portal/positions/{position_id}/graph
GET  /portal/admin/demo-tasks

趋势分析：
POST /positions/{position_id}/trend-analysis/tasks
GET  /trend-analysis/tasks/{task_id}
GET  /trend-reports/{report_id}

岗位聚类：
POST /position-clusters/tasks
GET  /position-clusters/tasks/{task_id}
GET  /position-clusters
GET  /position-clusters/{cluster_id}

新兴岗位：
POST /emerging-positions/from-cluster/{cluster_id}
GET  /emerging-positions
GET  /emerging-positions/{emerging_id}
POST /emerging-positions/{emerging_id}/germination-score
GET  /emerging-positions/{emerging_id}/germination-score
POST /emerging-positions/{emerging_id}/generate-definition

候选生命周期（BFF）：
GET  /portal/admin/discovery-candidates
GET  /portal/admin/discovery-candidates/{candidate_id}
GET  /portal/admin/discovery-candidates/{candidate_id}/trajectory
POST /portal/admin/discovery-candidates/{candidate_id}/enter-governance

人岗匹配：
POST /matches/tasks
GET  /matches/tasks/{task_id}
GET  /matches/reports/{evaluation_id}
GET  /matches/reports/{evaluation_id}/radar
GET  /matches/reports/{evaluation_id}/missing-skills

学习路径：
POST /learning-paths
GET  /learning-paths/{path_id}

审核：
GET  /review-tasks
GET  /review-tasks/{task_id}
POST /review-tasks/{task_id}/approve
POST /review-tasks/{task_id}/reject
PUT  /review-tasks/{task_id}/modify

# 24. P2 先保留接口集合

```text id="z4t7uz"
企业招聘动态调整：
PUT /enterprise-jobs/{job_id}/pause
PUT /enterprise-jobs/{job_id}/resume
PUT /enterprise-jobs/{job_id}/cancel
PUT /enterprise-jobs/{job_id}/headcount

预测岗位：
POST /trend-sources/policy
POST /trend-sources/report
POST /trend-sources/paper
POST /trend-sources/webpage
POST /predicted-positions/tasks
GET  /predicted-positions
GET  /predicted-positions/{predicted_id}
POST /predicted-positions/{predicted_id}/confidence-score
POST /predicted-positions/{predicted_id}/publish

图谱高级能力：
GET  /portal/admin/knowledge-graph/positions/{position_id}/versions
GET  /portal/admin/knowledge-graph/positions/{position_id}/versions/diff
POST /portal/admin/knowledge-graph/positions/{position_id}/versions/{version_id}/rollback
GET  /trend-reports/{report_id}/skill-combo-shifts
GET  /trend-reports/{report_id}/replaced-skills

企业反馈闭环：
POST /feedback/candidate-match
POST /feedback/job-requirement-change
```

---

# 25. 后端模块拆分建议

```text id="pszzo5"
auth_service
enterprise_service
enterprise_job_service
jd_service
resume_service
skill_service
position_graph_service
trend_analysis_service
cluster_service
emerging_position_service
predicted_position_service
evidence_service
rag_service
embedding_service
match_service
learning_path_service
review_service
evaluation_service
file_service
system_config_service
task_service
```

---

# 26. 数据流对应关系

## 26.1 JD 到岗位图谱

```text id="s07tmo"
POST /jds/text
→ POST /jds/{jd_id}/parse
→ GET /jds/{jd_id}/parse-result
→ POST /skills/normalize-batch
→ POST /jds/{jd_id}/duplicate-check
→ POST /jds/{jd_id}/inflation-check
→ POST /portal/admin/knowledge-graph/positions/{position_id}/build
→ GET /portal/positions/{position_id}/graph
```

---

## 26.2 既有岗位趋势分析

```text id="nm8d9a"
GET /portal/positions/{position_id}/graph
→ POST /positions/{position_id}/trend-analysis/tasks
→ GET /trend-analysis/tasks/{task_id}
→ GET /trend-reports/{report_id}
→ GET /trend-reports/{report_id}/current-graph
→ GET /trend-reports/{report_id}/summary
```

---

## 26.3 新兴岗位发现

```text id="xwk3av"
POST /position-clusters/tasks
→ GET /position-clusters
→ GET /position-clusters/{cluster_id}
→ GET /portal/admin/discovery-candidates
→ GET /portal/admin/discovery-candidates/{candidate_id}
→ GET /portal/admin/discovery-candidates/{candidate_id}/trajectory
→ POST /portal/admin/discovery-candidates/{candidate_id}/enter-governance
→ POST /emerging-positions/from-cluster/{cluster_id}
→ POST /emerging-positions/{emerging_id}/germination-score
→ POST /emerging-positions/{emerging_id}/generate-definition
→ POST /review-tasks
→ POST /review-tasks/{task_id}/approve
→ POST /emerging-positions/{emerging_id}/publish
```

---

## 26.4 简历到人岗匹配

```text id="lmylh7"
POST /resumes/file
→ POST /resumes/{resume_id}/parse
→ GET /resumes/{resume_id}/parse-result
→ PUT /resumes/{resume_id}/parse-result
→ POST /resumes/{resume_id}/skill-profile
→ POST /matches/tasks
→ GET /matches/reports/{evaluation_id}
→ GET /matches/reports/{evaluation_id}/radar
→ POST /learning-paths
```

---

# 27. 接口实现顺序建议

## JD 草稿审核与发布

| 能力 | 方法 | 路径 | 角色 | 语义 |
|---|---|---|---|---|
| 审核通过 | POST | `/review-tasks/{task_id}/approve` | reviewer/admin/developer | pending → approved；JD 草稿同步进入 reviewed |
| 审核拒绝 | POST | `/review-tasks/{task_id}/reject` | reviewer/admin/developer | 必须提交原因；JD 草稿保持不可发布 |
| 技能人工映射 | POST | `/jd-parse-results/{parse_result_id}/skill-catalog-mappings` | reviewer/admin/developer | 将待处理规范化技能绑定到现有 Catalog Skill，并关闭对应阻塞 flag |
| 原子发布 | POST | `/jd-parse-results/{parse_result_id}/publish` | admin/developer 或有权企业用户 | reviewed → published，并原子创建不可变快照与 pending Outbox |
| 发布查询 | GET | `/jd-parse-results/{parse_result_id}/publication` | 有权读取 JD 的用户 | 返回快照、fingerprint、来源版本及 Outbox 标识 |
| Outbox 正式重新投递 | POST | `/outbox-events/{event_id}/requeue` | admin/developer | 将 retryable/dead_letter 或 lease 已过期的 claimed 事件恢复为 pending；保留 attempts 和 last_error |

发布接口不执行 Extraction、规则重抽取或 KG HTTP。重复发布按 parse result、来源版本、Extraction Task、schema version 与内容 fingerprint 幂等。

发布后的 KG 同步由 Compose 中的 `kg-outbox-worker` 完成。Worker 与手工
`/integrations/knowledge-graph/jds/{document_id}/sync` 共用同一
`JDPublication`、Outbox 事件、mapper 和幂等身份；手工接口不会创建第二份发布事实或重复
Outbox。Worker 的并发、轮询、lease、最大次数以及 KG URL、认证和超时均由环境变量配置。
KG 401 认证失败按 retryable 处理并受最大尝试次数限制；403 权限不足和契约/身份错误直接进入
dead_letter。达到最大次数的事件只能通过严格鉴权的 requeue 接口重新排队，delivered 或仍持有
有效 lease 的事件不能 requeue。

Bundle 与旧手工解析结果在进入审核前都按 Catalog ID、管理员维护的精确别名和
Catalog 快照重新解析。无法唯一解析或 ID/名称冲突时保留原始规范化字段与 Evidence，
并生成 `skill_catalog_unresolved`、`skill_catalog_conflict` 或
`skill_catalog_snapshot_missing` blocking ReviewFlag；发布前会再次执行相同门禁。

## 第一批：系统能跑起来

```text id="z8zaee"
auth
enterprise
enterprise_job
jd upload / parse
resume upload / parse
skill normalize
position graph
match report
```

## 第二批：系统有创新点

```text id="72wjl2"
jd duplicate check
inflation check
position clustering
emerging position
germination score
trend report
review task
```

## 第三批：系统更完整

```text id="zf6c4c"
predicted position
trend sources
Evidence validation
graph version
enterprise feedback
evaluation dashboard
```

---

# 关键契约与执行模式

## 现有契约

- Portal Demo Task：`/api/v1/portal/admin/demo-tasks`，字段与枚举由
  `app/schemas/task.py` 和 `tests/test_portal_demo_tasks.py` 固定；
- KG Published Position Profile：`position-profile.v3`
  （`jobgraph_contracts/position_profile.py`），必须携带 `graph_version_id`、
  `position_code`、岗位目录版本、分类状态和样本支持状态；
- GraphVersion：KG 唯一 `(position_id, version_name)` 版本语义，Published Profile、
  Matching 与 Trend 共用同一 GraphVersion 身份；
- ValidatedCVSnapshot / CV Profile：主系统 `ValidatedCVSnapshotRecord` 与
  `cv-match-profile.v1`；
- Matching Evaluation：`matching-evaluation-result.v1`，保留确定性正式评分与
  `semantic_shadow_status` 只读语义；
- Evidence：JD/CV Extraction Evidence 保留 `source_id / quote / start / end /
  alignment / occurrence_index` 必填语义。

## Semantic Shadow 只读语义

Matching Evaluation 的 `semantic_shadow_status / score / evidence` 为只读语义：
语义候选由匹配服务语义检索链路（BGE-M3 + Qdrant）生成，契约与模型版本固定于
`config/semantic-demo-contract.env`；`matching-evaluation-result.v1` 只回传
字段，不参与正式评分改写。业务场景维度不作为 Matching/KG 正式链路的评分维度，
任务上下文由 responsibilities / project 片段承载，主系统不从 raw text 关键词
猜测行业场景。
## Evidence RAG 契约

定义文件：`jobgraph_contracts/rag.py`

查询（`evidence-rag-query.v1`）：

- `business_object`：当前业务对象；
- `business_objects`：多对象查询时的对象列表，每项携带自己的 `object_version`；
- `query_text`：查询文本；
- `evidence_types`：Evidence 类型范围；
- `version_scope`：`single_object` 或 `multi_object`；
- `graph_version_id` / `graph_version` / `business_version`：GraphVersion 或业务版本；
- `permission`：`user_id`、`tenant_ref`、`permission_scope`。

响应（`evidence-rag-response.v1`）：

- `status`：`answered` / `insufficient_evidence` / `failed`；
- `answer` 与 `references`：每条引用包含 `evidence_id`、来源对象、原文片段或位置、
  版本信息、`tenant_ref` 与 `permission_scope`；
- `provider`、`model`、`model_version`、`trace_id`；
- `error`：明确的错误码与消息；
- `permission`：实际使用的 `PermissionContext`。

版本范围：

- 单对象 Query、Response、Reference 使用 `graph_version_id` / `graph_version` /
  `business_version` 三种版本身份中的一种；
- 多对象 Query 使用 `version_scope=multi_object` 和逐对象 `business_objects` 版本，
  不声明唯一的顶层 GraphVersion；多对象 Response 同样不声明唯一顶层版本；
- 多对象 Response 的每条 Reference 保留 `business_object_id`、版本身份和
  `source_version`，由应用层校验其版本必须等于该对象在本次 Query 中请求的版本；
- Reference 支持 `business_version`，`source_cv` 等业务版本查询可以形成合法回答；
- 单对象 Response 使用任一版本身份时，所有引用必须与该身份一致；多对象仅拒绝
  对象外引用或对象与版本不匹配的引用。

权限范围：

- `PermissionContext` 必须由主系统 BFF 根据认证用户装配
  （`assembled_by=main-system-bff`），不能信任浏览器自行声明权限；
- Response 保留实际使用的 `PermissionContext`，而不是只返回 `tenant_ref`；
- Evidence Reference 至少携带 `tenant_ref` 与 `permission_scope`；引用不得跨
  `tenant_ref`，有 `permission_scope` 时引用必须处于响应允许的范围；
- `user_id` 只作为执行主体与审计标识，不代表 Evidence 所有者；当前 Contract 不阻止
  跨用户，也不声称已阻止跨用户。

强制语义：

- 无有效 Evidence 时拒答，`insufficient_evidence` 不允许携带回答或引用；
- 不允许跨租户、跨 permission_scope 或跨 GraphVersion/business_version 串用证据；
- RAG 不生成新的岗位、技能、学历、项目或工作经历事实；
- 浏览器只通过主系统 BFF 使用 RAG；
- 旧 Evidence Composer 输出缺少 `status / references / trace_id`，不能仅改名冒充
  真实 RAG。

## 执行模式标识

定义文件：`jobgraph_contracts/execution_modes.py`

固定标识：`rule`、`llm`、`human_confirmed`、`demo`、`semantic_shadow`、
`rag_explanation`。`mock` 不是执行模式。`llm`、`semantic_shadow`、
`rag_explanation` 属于 `model_required=True` 模式，结果必须保持
`requested_mode == result_mode`：禁止 `rag_explanation -> rule/demo/human_confirmed`
成功、`semantic_shadow -> rule/demo` 成功、`llm -> rule/demo/shadow/human_confirmed`
成功等伪成功；`failed` 必须携带 `error_code`，`succeeded` / `available` 不得携带
错误字段。
