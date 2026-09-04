from pathlib import PurePath


ALLOWED_UPLOAD_TYPES = {
    "application/pdf": frozenset({".pdf"}),
    "application/msword": frozenset({".doc"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": frozenset({".docx"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/png": frozenset({".png"}),
    "text/plain": frozenset({".txt"}),
}


class FileRuleViolation(ValueError):
    pass


def validate_upload(filename: str, content_type: str | None, content: bytes, maximum_size: int) -> tuple[str, str]:
    safe_name = PurePath(filename).name
    suffix = PurePath(safe_name).suffix.lower()
    if not safe_name or suffix not in ALLOWED_UPLOAD_TYPES.get(content_type or "", frozenset()):
        raise FileRuleViolation("Unsupported file type")
    if not content:
        raise FileRuleViolation("File is empty")
    if len(content) > maximum_size:
        raise FileRuleViolation("File exceeds configured size limit")
    return safe_name, suffix
