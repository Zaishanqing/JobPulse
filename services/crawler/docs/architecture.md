# 爬虫目录与架构

> 文档类型：架构
> 维护状态：active
> 适用范围：`JobPulse/services/crawler/`
> 事实源：爬虫代码、配置与测试
> 责任角色：模块维护者
> 最后复核：2026-08-21

## 项目结构

```
├── run.py                          # 启动入口
├── requirements.txt                # Python 依赖
├── init_database.py                # 数据库初始化脚本
├── config.py                       # 爬虫配置（城市代码、关键词等）
│
├── unified_api/                    # 统一 API 后端
│   ├── main.py                     # FastAPI 入口，CORS，路由注册
│   ├── config.py                   # 数据库 / JWT / 爬虫参数配置
│   ├── database.py                 # MySQL 连接池（DBUtils）
│   ├── auth.py                     # JWT 认证工具 + 依赖注入
│   ├── routers/                    # API 路由
│   │   ├── auth_router.py          # 注册 / 登录 / 个人信息
│   │   ├── boss_router.py          # Boss直聘爬虫
│   │   ├── company_router.py       # 多公司爬虫
│   │   ├── liepin_router.py        # 猎聘爬虫
│   │   ├── internal_router.py      # 主后端服务间采集/导出接口
│   │   └── task_router.py          # 任务状态查询
│   ├── offline_export/             # 离线 Bundle 导出、校验与清单
│   ├── schemas/                    # Pydantic 请求/响应模型
│   │   ├── auth.py
│   │   ├── task.py
│   │   └── job.py
│   └── services/                   # 业务逻辑层
│       ├── boss_service.py         # 包装 crawler/simple_spider.py
│       ├── company_service.py      # 包装 multi_company_scraper/
│       ├── liepin_service.py       # 包装 liepin_scraper
│       └── task_manager.py         # 后台线程任务调度
├── patches/                        # 定时调度器及 `/api/scheduler` 路由
│
├── crawler/                        # Boss直聘爬虫模块
│   ├── simple_spider.py            # 无头爬虫（API 拦截）
│   ├── visual_spider.py            # 带 GUI 的爬虫
│   └── data_viewer.py              # 数据查看器
│
└── multi_company_scraper/          # 多公司爬虫模块
    ├── main.py                     # CLI 入口
    ├── collector.py                # 数据收集器
    ├── normalizer.py               # 薪资/经验/学历标准化
    ├── excel_writer.py             # Excel 输出
    ├── config/
    │   ├── companies.yaml          # 50 家公司配置
    │   ├── liepin_search_params.yaml
    │   ├── liepin_cookies.example.json # Cookie 结构示例
    │   └── liepin_cookies.local.json   # 本地登录 Cookie（运行时生成、Git 忽略）
    ├── models/
    │   ├── company_config.py
    │   └── job_data.py
    ├── scrapers/
    │   ├── base.py                 # 爬虫基类
    │   ├── dispatcher.py           # 爬虫调度器
    │   ├── playwright_scraper.py   # Playwright 通用爬虫（主力）
    │   ├── liepin_scraper.py       # 猎聘爬虫
    │   ├── moka_scraper.py         # Moka ATS
    │   ├── feishu_scraper.py       # 飞书招聘
    │   ├── baidu_scraper.py        # 百度招聘
    │   ├── tencent_scraper.py      # 腾讯招聘
    │   ├── netease_scraper.py      # 网易招聘
    │   └── zhiye_scraper.py        # 智联招聘
    └── tests/                      # 多公司采集器模块测试
```

## 技术栈

- **Web 框架**：FastAPI
- **数据库**：MySQL + PyMySQL + DBUtils 连接池
- **认证**：JWT (PyJWT + passlib/bcrypt)
- **爬虫引擎**：DrissionPage（Boss直聘）/ Playwright（多公司 + 猎聘）
- **任务调度**：后台线程 + 内存状态 + MySQL 持久化
- **数据验证**：Pydantic v2
