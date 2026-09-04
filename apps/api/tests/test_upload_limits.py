from __future__ import annotations

import asyncio

import pytest

from app.api.upload_limits import UploadSizeLimitExceeded, read_upload


class _FakeUpload:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.requested_size: int | None = None

    async def read(self, size: int = -1) -> bytes:
        self.requested_size = size
        return self.content[:size] if size >= 0 else self.content


def test_read_upload_requests_only_one_byte_over_the_limit():
    upload = _FakeUpload(b"1234")

    result = asyncio.run(read_upload(upload, 4))

    assert result == b"1234"
    assert upload.requested_size == 5


def test_read_upload_rejects_after_bounded_read():
    upload = _FakeUpload(b"12345")

    with pytest.raises(UploadSizeLimitExceeded):
        asyncio.run(read_upload(upload, 4))

    assert upload.requested_size == 5
