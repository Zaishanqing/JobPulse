from app.domain.json_types import FrozenJsonObject
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from app.domain.accounts import AccountActor
from app.contexts.platform._applications.config_defaults import DEFAULT_CONFIGS
from app.domain.system_config import reject_sensitive_values
from app.domain.model_service_config import decrypt_api_key, encrypt_api_key
from app.contexts.platform._ports.system import SystemConfigRecord, SystemConfigUnitOfWork, SystemStatusPort
from app.domain.errors import PermissionDenied


@dataclass(frozen=True)
class QuerySystemStatus:
    status: SystemStatusPort

    @staticmethod
    def _authorize(actor: AccountActor) -> None:
        if actor.role not in {"admin", "developer"}:
            raise PermissionDenied("No permission to view system status")

    def overall(self, actor: AccountActor) -> FrozenJsonObject:
        self._authorize(actor)
        return self.status.overall()

    def readiness(self) -> tuple[bool, FrozenJsonObject]:
        return self.status.readiness()

    def database(self, actor: AccountActor) -> FrozenJsonObject:
        self._authorize(actor)
        return self.status.database()

    def vector_store(self, actor: AccountActor) -> FrozenJsonObject:
        self._authorize(actor)
        return self.status.vector_store()

    def model_services(self, actor: AccountActor) -> FrozenJsonObject:
        self._authorize(actor)
        return self.status.model_services()


class SystemConfigNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ManageSystemConfigs:
    uow_factory: Callable[[], SystemConfigUnitOfWork]
    secret_key: str = ""

    def get(self, actor: AccountActor, name: str) -> SystemConfigRecord:
        self._authorize(actor)
        if name not in DEFAULT_CONFIGS:
            raise SystemConfigNotFound("System config not found")
        with self.uow_factory() as uow:
            record = uow.configs.get(name)
            if record is None:
                record = uow.configs.add_default(name, deepcopy(DEFAULT_CONFIGS[name]))
                uow.commit()
            return record

    def update(self, actor: AccountActor, name: str, changes: FrozenJsonObject) -> SystemConfigRecord:
        reject_sensitive_values(changes)
        current = self.get(actor, name)
        merged = dict(current.config)
        merged.update(changes)
        with self.uow_factory() as uow:
            record = uow.configs.update(name, merged, actor.account_id)
            uow.commit()
            return record

    def get_model_service(self, actor: AccountActor) -> dict[str, object]:
        current = self.get(actor, "llm")
        return {
            "provider": "deepseek",
            "base_url": str(current.config.get("base_url") or "https://api.deepseek.com"),
            "model": str(current.config.get("model") or "deepseek-v4-flash"),
            "api_key_configured": bool(current.config.get("api_key_ciphertext")),
            "version": current.version,
            "updated_at": current.updated_at.isoformat() if current.updated_at else None,
        }

    def update_model_service(
        self,
        actor: AccountActor,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
    ) -> dict[str, object]:
        current = self.get(actor, "llm")
        merged = dict(current.config)
        merged.update({
            "enabled": True,
            "provider": "deepseek",
            "base_url": base_url.rstrip("/"),
            "model": model,
        })
        if api_key:
            merged["api_key_ciphertext"] = encrypt_api_key(api_key, self.secret_key)
        with self.uow_factory() as uow:
            uow.configs.update("llm", merged, actor.account_id)
            uow.commit()
        return self.get_model_service(actor)

    def resolve_model_api_key(self, actor: AccountActor) -> str | None:
        current = self.get(actor, "llm")
        ciphertext = current.config.get("api_key_ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            return None
        return decrypt_api_key(ciphertext, self.secret_key)

    def resolve_runtime_model_service(self) -> tuple[str, str, str] | None:
        """Resolve the persisted model configuration for an internal service call."""
        with self.uow_factory() as uow:
            current = uow.configs.get("llm")
        if current is None:
            return None
        ciphertext = current.config.get("api_key_ciphertext")
        base_url = current.config.get("base_url")
        model = current.config.get("model")
        if not all(isinstance(value, str) and value for value in (ciphertext, base_url, model)):
            return None
        return (
            str(base_url).rstrip("/"),
            str(model),
            decrypt_api_key(str(ciphertext), self.secret_key),
        )

    @staticmethod
    def _authorize(actor: AccountActor) -> None:
        if actor.role not in {"admin", "developer"}:
            raise PermissionDenied("Permission denied")
