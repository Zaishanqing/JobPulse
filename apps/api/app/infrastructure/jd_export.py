from app.contexts.jd_lifecycle import JDExportFile
from app.infrastructure.jd_export_serialization import serialize_export
from app.domain.jd import Document, NormalizationResult


class OpenPyxlJDExporter:
    def export(
        self, document: Document, normalization: NormalizationResult
    ) -> JDExportFile:
        payload = serialize_export(document, normalization)
        return JDExportFile(
            payload["filename"],
            payload["media_type"],
            payload["content_base64"],
            tuple(payload["worksheets"]),
        )
