"""共享工具：数据库连接、公司关键词加载、输出路径。"""
import os
import pymysql
import yaml
from unified_api.config import DB_CONFIG

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
COMPANIES_YAML = os.path.join(
    os.path.dirname(__file__), "..", "multi_company_scraper", "config", "companies.yaml"
)

# 聚焦新一代信息技术方向的关键词
TARGET_KEYWORDS = [
    "人工智能", "AI", "机器学习", "深度学习", "大模型", "NLP", "CV", "算法",
    "大数据", "数据仓库", "数据分析", "数据挖掘", "数据工程", "ETL", "数据架构",
    "智能系统", "推荐系统", "知识图谱", "智能决策",
    "物联网", "IoT", "边缘计算", "嵌入式",
    "云计算", "云原生", "K8S", "Docker",
]


def get_db_connection() -> pymysql.Connection:
    return pymysql.connect(**DB_CONFIG)


def load_companies() -> list[dict]:
    """加载 companies.yaml，返回仅 enabled=True 的公司列表。"""
    with open(COMPANIES_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [c for c in data.get("companies", []) if c.get("enabled", False)]


def ensure_output_dir() -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR
