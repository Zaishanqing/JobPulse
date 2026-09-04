from app.domain.value_types import SerializedPayload


class ApplicationError(Exception):
    status_code = 400

    def __init__(self, message: str, *, error_code: str | None = None,
                 details: SerializedPayload | None = None):
        self.error_code = error_code
        self.details = details or {}
        if error_code:
            self.details.setdefault("error_code", error_code)
        super().__init__(message)


class NotFoundError(ApplicationError):
    status_code = 404


class ConflictError(ApplicationError):
    status_code = 409


class ValidationError(ApplicationError):
    status_code = 422


class StructuredFactsIncompleteError(ConflictError):
    """The audit payload exists but the authoritative structured projection is incomplete."""


from app.domain.publishing import GateViolation


class PublishGateError(ConflictError):
    def __init__(self, errors: tuple[GateViolation, ...]):
        self.errors = errors
        super().__init__("graph publish gate rejected the build")


class StaleGraphDraftError(ConflictError):
    def __init__(self, *, base_version_id: int | None, current_version_id: int | None):
        super().__init__(
            "draft is based on a stale graph version",
            error_code="STALE_GRAPH_DRAFT",
            details={
                "base_version_id": base_version_id,
                "current_version_id": current_version_id,
            },
        )


class RelationEditConflictError(ConflictError):
    def __init__(self, *, current_revision: int):
        super().__init__(
            f"relation revision conflict; reload current revision {current_revision}",
            error_code="RELATION_EDIT_CONFLICT",
            details={"current_revision": current_revision},
        )


class BuildAlreadyPublishedError(ConflictError):
    def __init__(self, *, version_id: int):
        super().__init__(
            "build has already been published",
            error_code="BUILD_ALREADY_PUBLISHED",
            details={"version_id": version_id},
        )


class DuplicateFactVersion(ConflictError):
    """A concurrent import persisted the same authoritative fact version."""


class ConcurrentFactWrite(ConflictError):
    """The fact projection was temporarily locked by a concurrent writer."""


class DuplicateBuildRun(ConflictError):
    """A concurrent publisher persisted a version for the same graph build."""


class ConcurrentSkillResolution(ConflictError):
    """An unresolved normalization item changed during a CAS update."""


class ConcurrentReviewTaskWrite(ConflictError):
    """A review task changed during a CAS update."""


class ConcurrentInnovationWrite(ConflictError):
    """An immutable innovation artifact or review revision conflicts."""
