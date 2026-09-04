# 爬虫运行与部署

> 文档类型：运维
> 维护状态：active
> 适用范围：`JobPulse/services/crawler/`
> 事实源：爬虫代码、配置与测试
> 责任角色：模块维护者
> 最后复核：2026-08-21

## 快速开始

### 1. 环境要求

- Python 3.11+
- MySQL 5.7+（需预先安装并运行）
- Chrome 浏览器（Boss直聘/猎聘爬虫需要）
- Playwright 浏览器驱动（多公司爬虫需要）

### 2. 安装依赖

```bash
pip install -e ../../packages/contracts
pip install -e .

# 真实浏览器采集另安装浏览器 extra 和 Playwright 驱动
pip install -e '.[browser]'
playwright install chromium
```

### 3. 配置数据库

编辑 `unified_api/config.py` 修改数据库连接信息（或通过环境变量设置）：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DB_HOST` | 127.0.0.1 | MySQL 主机 |
| `DB_PORT` | 3306 | MySQL 端口 |
| `DB_USER` | recruitment_app | 数据库用户 |
| `DB_PASSWORD` | (必须自行设置) | 数据库密码 |
| `DB_NAME` | recruitment_analysis | 数据库名 |
| `JWT_SECRET` | (开发环境自动生成) | JWT 签名密钥（生产或正式演示环境建议显式设置至少32字符） |
| `INTERNAL_SERVICE_TOKEN` | 本地开发值 | `/internal/v1` 服务间 Bearer Token；生产必须显式设置至少 32 字符 |
| `CORS_ALLOWED_ORIGINS` | 本地 3000/5173 地址 | 生产必须显式设置的逗号分隔 Origin |
| `OFFLINE_BUNDLE_DIR` | `bundles` | 内部导出接口写入 Bundle 的目录 |

Windows PowerShell 设置环境变量示例：

```powershell
$env:DB_HOST="127.0.0.1"
$env:DB_PORT="3306"
$env:DB_USER="recruitment_app"
$env:DB_PASSWORD="你的本地数据库密码"
$env:DB_NAME="recruitment_analysis"
$env:JWT_SECRET="至少32字符的随机字符串"
```

初始化数据库：

```bash
python init_database.py
```

### 4. 启动服务

```bash
python run.py
```

服务启动后访问：
- API 文档：http://localhost:8800/docs
- 健康检查：http://localhost:8800/api/health

## 生产环境部署

### 使用 uvicorn 直接启动

```bash
uvicorn unified_api.main:app --host 0.0.0.0 --port 8800 --workers 4
```

### 环境变量配置

生产环境务必设置以下环境变量：

```bash
export DB_HOST=your-mysql-host
export DB_PASSWORD=your-secure-password
export JWT_SECRET=your-random-secret-string
```
