from __future__ import annotations

from pathlib import Path
from typing import Protocol

from document_processing.errors import ProcessingError
from document_processing.files import validate_pdf_header


class AsyncUpload(Protocol):
    async def read(self, size: int = -1) -> bytes:
        ...


async def save_upload(
    upload: AsyncUpload,
    destination: Path,
    *,
    max_size_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    total_size = 0
    header = b""
    try:
        with destination.open("xb") as output:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size_bytes:
                    raise ProcessingError(
                        "FILE_TOO_LARGE",
                        "Размер файла превышает допустимый лимит",
                        details={"max_size_bytes": max_size_bytes},
                    )
                if len(header) < 5:
                    header += chunk[: 5 - len(header)]
                output.write(chunk)
        validate_pdf_header(header)
        return total_size
    except Exception:
        destination.unlink(missing_ok=True)
        raise
