# 定时调度补丁（patches）

为爬虫项目添加定时自动启动爬虫任务的能力。基于 APScheduler 的 BackgroundScheduler，在后台线程中按 cron 表达式自动触发 Boss 直聘、多公司爬虫、猎聘三类任务。

## 安装依赖

```bash
pip install apscheduler pyyaml
```

## 配置文件（schedule_config.yaml）

```yaml
schedules:
  - name: "Boss直聘-每日Java北京"
    enabled: true          # 设为 false 可临时禁用
    task_type: boss        # boss / company / liepin
    cron: "0 8 * * *"      # 分 时 日 月 周（5 字段）
    user_id: 1
    params:                # 直接透传给对应爬虫服务
      keyword: "Java"
      city: "北京"
      pages: 5
```

### 支持的 task_type

| task_type | 对应服务 | 必填 params |
|---|---|---|
| `boss` | `unified_api.services.boss_service` | `keyword`, `city` |
| `company` | `unified_api.services.company_service` | `company_name`（传 `"all"` 抓全部） |
| `liepin` | `unified_api.services.liepin_service` | `keywords`, `cities` |

## 集成方式

### 方式一：嵌入 FastAPI（推荐）

在 `unified_api/main.py` 的 startup 事件中调用：

```python
from patches.scheduler import start_scheduler

@app.on_event("startup")
def startup():
    start_scheduler()
```

同时注册路由以获得状态查询接口：

```python
from patches.scheduler_router import router as scheduler_router
app.include_router(scheduler_router)
```

启动后可通过以下接口查看运行状态：

| 接口 | 说明 |
|---|---|
| `GET /api/scheduler/status` | 调度器运行状态、已注册任务及下次执行时间 |
| `GET /api/scheduler/jobs` | 列出所有已注册的定时任务 |

### 方式二：独立运行

```bash
python -m patches.scheduler
```

此模式会阻塞当前终端，按 Ctrl-C 退出。

## 公开 API

```python
from patches.scheduler import start_scheduler, stop_scheduler, get_status, list_jobs

# 启动（默认读取本目录下的 schedule_config.yaml）
scheduler = start_scheduler()

# 指定配置文件
scheduler = start_scheduler(config_path="/path/to/custom_config.yaml")

# 查看状态
status = get_status()
print(status)  # {"running": True, "job_count": 3, "jobs": [...], ...}

# 查任务列表
jobs = list_jobs()

# 停止
stop_scheduler()
```

## 注意事项

- 调度器以 daemon 线程运行，主进程退出时自动终止。
- 三个爬虫错开 3 分钟触发，避免同时争抢浏览器资源。
- 如果 APScheduler 未安装，调用 `start_scheduler()` 会抛出 `ImportError` 提示安装。
- 配置文件不存在时不会报错，仅以空配置运行（无任务）。
