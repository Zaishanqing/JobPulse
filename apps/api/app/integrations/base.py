from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CapabilityStatus:
    capability: str
    provider: str
    implementation_status: str
    enabled: bool
    persistent: bool
    detail: str
    version: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class IntegrationError(RuntimeError):
    def __init__(
        self,
        capability: str,
        provider: str,
        message: str,
        *,
        retryable: bool = False,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.capability = capability
        self.provider = provider
        self.retryable = retryable
        self.code = code

    def as_dict(self) -> dict:
        return {
            "error_type": self.__class__.__name__,
            "capability": self.capability,
            "provider": self.provider,
            "message": str(self),
            "retryable": self.retryable,
            "code": self.code,
        }


class IntegrationUnavailableError(IntegrationError):
    pass


class IntegrationInputError(IntegrationError):
    pass
