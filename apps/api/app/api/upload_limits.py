from __future__ import annotations

from fastapi import UploadFile


class UploadSizeLimitExceeded(ValueError):
    def __init__(self, maximum_size: int) -> None:
        super().__init__(f"File exceeds configured size limit of {maximum_size} bytes")
        self.maximum_size = maximum_size


async def read_upload(file: UploadFile, maximum_size: int) -> bytes:
    if maximum_size <= 0:
        raise ValueError("maximum upload size must be positive")
    content = await file.read(maximum_size + 1)
    if len(content) > maximum_size:
        raise UploadSizeLimitExceeded(maximum_size)
    return content


__all__ = ["UploadSizeLimitExceeded", "read_upload"]
