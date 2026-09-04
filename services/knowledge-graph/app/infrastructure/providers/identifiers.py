"""Technical identifier generators."""

import uuid


class UuidSkillIdGenerator:
    def new_skill_id(self) -> str:
        return f"SKILL_{uuid.uuid4().hex[:12].upper()}"
