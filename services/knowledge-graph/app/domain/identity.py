from dataclasses import dataclass
from enum import Enum


VALID_ROLES = frozenset(
    {"personal_user", "enterprise_user", "reviewer", "admin", "developer", "integration_service"}
)


class Permission(str, Enum):
    GRAPH_EDIT = "graph_edit"
    REVIEW = "review"
    PUBLISH = "publish"
    INTERNAL_READ = "internal_read"


_ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "personal_user": frozenset(),
    "enterprise_user": frozenset(),
    "reviewer": frozenset({Permission.REVIEW, Permission.INTERNAL_READ}),
    "admin": frozenset(Permission),
    "developer": frozenset(
        {Permission.GRAPH_EDIT, Permission.PUBLISH, Permission.INTERNAL_READ}
    ),
    "integration_service": frozenset(
        {Permission.GRAPH_EDIT, Permission.REVIEW, Permission.PUBLISH, Permission.INTERNAL_READ}
    ),
}


@dataclass(frozen=True)
class IdentityActor:
    user_id: int
    username: str
    role: str

    @property
    def id(self) -> int:
        """Compatibility alias for existing use-case actor inputs."""
        return self.user_id


def has_permission(actor: IdentityActor, permission: Permission) -> bool:
    return permission in _ROLE_PERMISSIONS.get(actor.role, frozenset())
