from typing import Callable, Mapping

from app.contexts.jd_lifecycle._applications.jd_common import (
    JDApplicationError,
    JDFileCreateCommand,
    JDTextCreateCommand,
)
from app.domain.jd_policies import JDParseCommand, JDParseEditCommand
from app.contexts.jd_lifecycle._applications.jd_management import JDManagementUseCases
from app.contexts.jd_lifecycle._applications.jd_parsing import JDParsingUseCases
from app.contexts.jd_lifecycle._applications.jd_quality import JDQualityUseCases
from app.contexts.jd_lifecycle._applications.jd_review import JDReviewUseCases
from app.contexts.jd_lifecycle._applications.jd_support import JDSupportUseCases
from app.contexts.jd_lifecycle._ports.jd_repository import JDExportPort, JDSchemaPort, JDUoW
from app.contexts.extraction_tasks.ports import JDExtractionProvider

__all__ = [
    "JDApplicationError",
    "JDFileCreateCommand",
    "JDParseCommand",
    "JDParseEditCommand",
    "JDTextCreateCommand",
    "JDUseCases",
]


class JDUseCases(
    JDManagementUseCases,
    JDParsingUseCases,
    JDReviewUseCases,
    JDQualityUseCases,
    JDSupportUseCases,
):
    """Compatibility facade composed from single-responsibility JD use-case groups."""

    def __init__(
        self,
        uow_factory: Callable[[], JDUoW],
        exporter: JDExportPort,
        schema: JDSchemaPort,
        data_validation_mode: str = "off",
        extraction_providers: Mapping[str, JDExtractionProvider] | None = None,
    ) -> None:
        if data_validation_mode not in {"off", "observe", "enforce"}:
            raise ValueError("Unsupported data validation mode")
        self._uow_factory = uow_factory
        self._exporter = exporter
        self._schema = schema
        self._data_validation_mode = data_validation_mode
        self._extraction_providers = dict(extraction_providers or {})
