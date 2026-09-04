from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from jobgraph_contracts.offline_bundle import (
    BUNDLE_FILES,
    BUNDLE_FILES_LEGACY,
)

from app.offline_import.contracts import BundleVerificationError


MAX_BUNDLE_MEMBER_BYTES = 512 * 1024 * 1024


def read_bundle_members(path: Path) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(path) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            names = {item.filename for item in files}
            if names not in (BUNDLE_FILES, BUNDLE_FILES_LEGACY) or len(files) != len(names):
                raise BundleVerificationError(
                    f"Bundle files must be exactly {sorted(BUNDLE_FILES)} "
                    f"or legacy {sorted(BUNDLE_FILES_LEGACY)}"
                )
            for item in files:
                parts = PurePosixPath(item.filename).parts
                if (
                    PurePosixPath(item.filename).is_absolute()
                    or ".." in parts
                    or "\\" in item.filename
                ):
                    raise BundleVerificationError("Unsafe ZIP member path")
                if item.file_size > MAX_BUNDLE_MEMBER_BYTES:
                    raise BundleVerificationError("Bundle member exceeds size limit")
            return {item.filename: archive.read(item) for item in files}
    except BundleVerificationError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise BundleVerificationError(f"Unreadable ZIP: {exc}") from exc
