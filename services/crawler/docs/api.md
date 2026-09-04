# 爬虫 HTTP API

> 文档类型：API
> 维护状态：active
> 适用范围：`JobPulse/services/crawler/`
> 事实源：爬虫代码、配置与测试
> 责任角色：模块维护者
> 最后复核：2026-08-21

## API 概览

### 认证（无需登录）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录，返回 JWT Token（7天有效） |
| GET | `/api/auth/profile` | 获取当前用户资料 |

健康检查为 `GET /api/health`。

### Boss直聘（需登录）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/boss/crawl` | 启动爬虫 → 返回 task_id |
| GET | `/api/boss/jobs` | 分页查询岗位 |
| GET | `/api/boss/stats` | 统计概览 |
| GET | `/api/boss/keywords` | 关键词列表 |
| GET | `/api/boss/cities` | 城市列表 |
| GET | `/api/boss/login/status` | Boss 登录状态 |
| GET | `/api/boss/jobs/{job_id}/raw` | 单条岗位原始 Envelope |
| POST | `/api/boss/jobs/export-envelopes` | 批量导出岗位 Envelope |

**启动爬虫示例：**

```bash
# 设置测试账号（请替换为实际注册的账号密码）
ADMIN_USER="你的测试用户名"
ADMIN_PASSWORD="你的测试密码"

# 登录获取 token
TOKEN=$(curl -s -X POST http://localhost:8800/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASSWORD\"}" | jq -r '.access_token')

# 启动 Boss直聘爬虫：搜索"Java" + "北京"，爬 5 页
curl -X POST http://localhost:8800/api/boss/crawl \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"keyword":"Java","city":"北京","pages":5}'

# 查询任务进度
curl http://localhost:8800/api/task/{task_id} \
  -H "Authorization: Bearer $TOKEN"

# 查询爬取结果
curl "http://localhost:8800/api/boss/jobs?keyword=Java&city=北京&page=1&page_size=20" \
  -H "Authorization: Bearer $TOKEN"
```

### 多公司爬虫（需登录）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/company/crawl` | 启动爬虫（支持按公司/平台筛选） |
| GET | `/api/company/jobs` | 分页查询岗位 |
| GET | `/api/company/companies` | 已配置的公司列表 |
| GET | `/api/company/stats` | 按公司/平台统计 |
| GET | `/api/company/jobs/{job_id}/raw` | 单条公司岗位原始 Envelope |
| POST | `/api/company/jobs/export-envelopes` | 批量导出公司岗位 Envelope |

**启动爬虫示例：**

```bash
# 爬取单家公司
curl -X POST http://localhost:8800/api/company/crawl \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"company_name":"字节跳动"}'

# 爬取所有公司
curl -X POST http://localhost:8800/api/company/crawl \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"company_name":"all"}'

# 按平台筛选
curl -X POST http://localhost:8800/api/company/crawl \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"company_name":"all","platform":"playwright"}'
```

### 猎聘（需登录）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/liepin/crawl` | 启动猎聘爬虫 |

> **注意：** 猎聘有较强的反爬机制，需要：
> 1. 首次启动猎聘爬虫时，可以直接在浏览器中扫码登录。登录成功后，Cookie 默认保存到 `multi_company_scraper/config/liepin_cookies.local.json`（该文件已被 Git 忽略，不得提交）。也可以通过环境变量 `LIEPIN_COOKIES_FILE` 指定其他本地路径
> 2. 爬虫不使用无头模式（`HEADLESS = False`）
> 3. 爬取耗时较长（多关键词 × 多城市组合），建议设置较大的轮询间隔

### 任务管理（需登录）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/task/{task_id}` | 查询单个任务状态与进度 |
| GET | `/api/tasks` | 列出当前用户的所有任务 |

**任务状态说明：**

| 状态 | 说明 |
|---|---|
| `running` | 正在执行 |
| `completed` | 已完成 |
| `failed` | 执行失败（查看 error_message） |

### 定时调度

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/scheduler/status` | 调度器状态与已注册任务 |
| GET | `/api/scheduler/jobs` | 调度任务列表 |

### 主后端内部接口

`/internal/v1` 不是浏览器接口，统一使用 `INTERNAL_SERVICE_TOKEN` 的 Bearer Token。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/internal/v1/sources` | Boss、猎聘、飞书来源可用/登录状态 |
| GET | `/internal/v1/boss/login/status` | Boss 登录状态 |
| GET | `/internal/v1/liepin/login/status` | 猎聘登录状态 |
| POST | `/internal/v1/boss/cookies` | 写入并验证 Boss Cookie |
| POST | `/internal/v1/liepin/cookies` | 写入并验证猎聘 Cookie |
| POST | `/internal/v1/crawl` | 按 `boss`、`liepin` 或 `feishu` 创建采集任务 |
| GET | `/internal/v1/tasks/{task_id}` | 查询内部采集任务 |
| POST | `/internal/v1/export` | 为已完成且来源匹配的任务导出 full Offline Bundle |
