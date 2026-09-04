from pydantic import BaseModel


class FileAssetResponse(BaseModel):
    file_id: str
    filename: str
    content_type: str | None = None
    path: str
    storage_key: str
    size: int
    purpose: str | None = None
