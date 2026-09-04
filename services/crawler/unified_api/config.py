import os
import secrets

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'recruitment_app'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'recruitment_analysis'),
    'charset': 'utf8mb4',
}

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
_UNSAFE_JWT_SECRETS = frozenset({
    "",
    "unified-scraper-secret-2026",
    "change-me",
    "secret",
    "default",
    "123456",
})


def _resolve_jwt_secret() -> str:
    raw = os.getenv("JWT_SECRET", "").strip()
    if raw:
        lowered = raw.casefold()
        if lowered in _UNSAFE_JWT_SECRETS:
            raise ValueError("JWT_SECRET is an unsafe placeholder value")
        if len(raw) < 32 and ENVIRONMENT == "production":
            raise ValueError("JWT_SECRET must contain at least 32 characters in production")
        return raw
    if ENVIRONMENT == "production":
        raise ValueError("JWT_SECRET must be explicitly configured in production")
    return secrets.token_urlsafe(48)


JWT_SECRET = _resolve_jwt_secret()
JWT_ALGORITHM = 'HS256'
JWT_EXPIRES_DAYS = 7

# Internal service-to-service token for Main Backend -> Crawler calls.
# Main Backend must present this as a Bearer token on /internal/v1 endpoints.
INTERNAL_SERVICE_TOKEN = os.getenv(
    "INTERNAL_SERVICE_TOKEN",
    "local-crawler-internal-token-0123456789abcdef",
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_CORS_ORIGINS_RAW = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
if _CORS_ORIGINS_RAW:
    CORS_ALLOWED_ORIGINS = [origin.strip() for origin in _CORS_ORIGINS_RAW.split(",") if origin.strip()]
else:
    CORS_ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

_CORS_CREDENTIALS_RAW = os.getenv("CORS_ALLOW_CREDENTIALS", "false").strip().lower()
CORS_ALLOW_CREDENTIALS = _CORS_CREDENTIALS_RAW == "true"


# ---------------------------------------------------------------------------
# Spider settings
# ---------------------------------------------------------------------------
SPIDER_CONFIG = {
    'max_pages': 10,
    'delay_min': 2,
    'delay_max': 5,
    'timeout': 30,
}

CITY_CODES = {
    '北京': '101010100', '上海': '101020100', '广州': '101280100',
    '深圳': '101280600', '杭州': '101210100', '天津': '101030100',
    '西安': '101110100', '苏州': '101190400', '武汉': '101200100',
    '厦门': '101230200', '长沙': '101250100', '成都': '101270100',
    '郑州': '101180100', '重庆': '101040100', '佛山': '101280800',
    '合肥': '101220100', '济南': '101120100', '青岛': '101120200',
    '南京': '101190100', '东莞': '101281600', '福州': '101230100',
}

COMMON_KEYWORDS = [
    "Java", "Python", "前端", "后端", "算法", "测试", "运维",
    "Android", "iOS", "PHP", "C++", "C#", ".NET", "Go",
    "大数据", "人工智能", "机器学习", "深度学习", "数据挖掘",
    "架构师", "全栈", "Node.js", "Vue", "React", "Angular",
    "产品经理", "UI设计", "运营", "市场营销", "销售",
]


# ---------------------------------------------------------------------------
# Security validation — call at startup / database init boundary
# ---------------------------------------------------------------------------
_UNSAFE_DB_PASSWORDS = frozenset({
    "", "123456", "password", "root123", "admin", "root",
})


def validate_security_settings() -> None:
    """Raise ValueError if the security configuration is unsafe for the
    current environment.  Call this at application startup or database
    initialisation before any connection is established."""

    db_password = str(DB_CONFIG.get("password", "")).strip()
    db_user = str(DB_CONFIG.get("user", "")).strip().lower()

    if db_password.casefold() in _UNSAFE_DB_PASSWORDS:
        raise ValueError("DB_PASSWORD is empty or an unsafe placeholder value")
    if ENVIRONMENT == "production" and db_user in ("root", "admin"):
        raise ValueError("DB_USER must not be 'root' or 'admin' in production")

    # JWT_SECRET is validated at import time in _resolve_jwt_secret().
    # Re-validate here to keep the startup gate explicit.
    _resolve_jwt_secret()

    if CORS_ALLOW_CREDENTIALS and any(
        origin.strip() == "*" for origin in CORS_ALLOWED_ORIGINS
    ):
        raise ValueError(
            "CORS_ALLOW_CREDENTIALS cannot be true when origins contain '*'"
        )
    if ENVIRONMENT == "production" and not _CORS_ORIGINS_RAW:
        raise ValueError("CORS_ALLOWED_ORIGINS must be explicitly configured in production")

    internal_token = INTERNAL_SERVICE_TOKEN.strip()
    if ENVIRONMENT == "production" and (
        len(internal_token) < 32 or internal_token.casefold() in _UNSAFE_JWT_SECRETS
    ):
        raise ValueError(
            "INTERNAL_SERVICE_TOKEN must contain at least 32 characters in production"
        )
