class EmergingDiscoveryError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        error_code: str = "emerging_discovery_unavailable",
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or {}
