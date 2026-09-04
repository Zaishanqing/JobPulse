import os

# 数据库配置 — 所有爬虫模块复用此配置，不得单独硬编码凭据
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'recruitment_app'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'recruitment_analysis'),
    'charset': 'utf8mb4',
}

# 爬虫配置
SPIDER_CONFIG = {
    'max_pages': 10,  # 最大爬取页数
    'delay_min': 2,  # 最小延迟（秒）
    'delay_max': 5,  # 最大延迟（秒）
    'timeout': 30,  # 请求超时时间（秒）
    'user_agents': [  # 用户代理列表
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    ],
}

# 城市代码映射
CITY_CODES = {
    '北京': '101010100', '上海': '101020100', '广州': '101280100',
    '深圳': '101280600', '杭州': '101210100', '天津': '101030100',
    '西安': '101110100', '苏州': '101190400', '武汉': '101200100',
    '厦门': '101230200', '长沙': '101250100', '成都': '101270100',
    '郑州': '101180100', '重庆': '101040100', '佛山': '101280800',
    '合肥': '101220100', '济南': '101120100', '青岛': '101120200',
    '南京': '101190100', '东莞': '101281600', '福州': '101230100',
}

# 常用关键词
COMMON_KEYWORDS = [
    "Java", "Python", "前端", "后端", "算法", "测试", "运维",
    "Android", "iOS", "PHP", "C++", "C#", ".NET", "Go",
    "大数据", "人工智能", "机器学习", "深度学习", "数据挖掘",
    "架构师", "全栈", "Node.js", "Vue", "React", "Angular",
    "产品经理", "UI设计", "运营", "市场营销", "销售",
]

# 图表样式配置
CHART_CONFIG = {
    'style': 'seaborn',  # 图表样式
    'font.size': 10,     # 字体大小
    'figure.figsize': (10, 6),  # 图表尺寸
}
