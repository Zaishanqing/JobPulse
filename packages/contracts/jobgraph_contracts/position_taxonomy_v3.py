from typing import Literal

from pydantic import Field

from jobgraph_contracts.base import StrictContract


CareerLevel = Literal[
    "intern",
    "junior",
    "mid",
    "senior",
    "expert",
    "unspecified",
]
LeadershipScope = Literal[
    "none",
    "technical_lead",
    "team",
    "department",
    "organization",
    "executive",
]
TechnologyFocusCode = Literal[
    "ARTIFICIAL_INTELLIGENCE",
    "LLM",
    "AI_AGENT",
    "RAG",
    "NLP",
    "COMPUTER_VISION",
    "MULTIMODAL",
    "RECOMMENDATION",
    "SEARCH",
    "SPEECH",
    "BIG_DATA",
    "CLOUD_NATIVE",
    "CYBERSECURITY",
    "IOT",
    "EDGE_COMPUTING",
    "INTELLIGENT_HARDWARE",
    "ROBOTICS",
    "AUTONOMOUS_DRIVING",
    "BLOCKCHAIN",
    "GAME",
    "DIGITAL_TWIN",
    "XR",
    "GIS",
]
IndustryContextCode = Literal[
    "FINANCE",
    "HEALTHCARE",
    "EDUCATION",
    "ECOMMERCE",
    "MANUFACTURING",
    "GOVERNMENT",
    "ENTERPRISE_SERVICES",
    "TELECOMMUNICATIONS",
    "TRANSPORTATION",
    "ENERGY",
    "MEDIA",
    "GAMING",
]
ObservedSkillDomainCode = Literal[
    "ai_intelligent_systems",
    "blockchain_web3",
    "cloud_distributed",
    "computing_hardware",
    "cybersecurity_privacy",
    "data_engineering",
    "digital_governance",
    "embedded_iot_edge",
    "hci_graphics_xr",
    "network_communications",
    "quantum_computing",
    "robotics_autonomy",
    "software_engineering",
]
OBSERVED_SKILL_DOMAIN_CODES = frozenset(
    {
        "ai_intelligent_systems",
        "blockchain_web3",
        "cloud_distributed",
        "computing_hardware",
        "cybersecurity_privacy",
        "data_engineering",
        "digital_governance",
        "embedded_iot_edge",
        "hci_graphics_xr",
        "network_communications",
        "quantum_computing",
        "robotics_autonomy",
        "software_engineering",
    }
)


class CandidatePosition(StrictContract):
    position_code: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0.0, le=1.0)
