from dataclasses import dataclass, field


@dataclass
class CompanyConfig:
    name: str
    platform: str  # moka, feishu, baidu, tencent, netease, zhiye, playwright
    base_url: str
    enabled: bool = True
    # Playwright scrapers use these selectors
    selectors: dict = field(default_factory=dict)
    # API scrapers use these
    api_config: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "CompanyConfig":
        return cls(
            name=data["name"],
            platform=data["platform"],
            base_url=data["base_url"],
            enabled=data.get("enabled", True),
            selectors=data.get("selectors", {}),
            api_config=data.get("api_config", {}),
        )
