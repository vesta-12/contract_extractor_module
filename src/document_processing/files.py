from __future__ import annotations

import re
from pathlib import Path

from document_processing.errors import ProcessingError


ALLOWED_EXTENSIONS = frozenset({".pdf"})
ALLOWED_PDF_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/octet-stream",
    }
)
_SAFE_NAME_RE = re.compile(r"[^\w.-]+", re.UNICODE)


def safe_file_name(file_name: str) -> str:
    raw_name = Path(
        (file_name or "document.pdf").replace("\\", "/")
    ).name
    stem = _SAFE_NAME_RE.sub("_", Path(raw_name).stem)
    stem = stem.strip("._")[:100] or "document"
    suffix = Path(raw_name).suffix.casefold()
    return f"{stem}{suffix}"


def validate_upload_metadata(
    file_name: str,
    content_type: str | None,
    *,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
    allowed_mime_types: frozenset[str] = ALLOWED_PDF_MIME_TYPES,
) -> str:
    safe_name = safe_file_name(file_name)
    extension = Path(safe_name).suffix.casefold()
    if extension not in allowed_extensions:
        raise ProcessingError(
            "UNSUPPORTED_FORMAT",
            "Поддерживаются только PDF-файлы",
        )

    normalized_mime = (
        content_type.split(";", 1)[0].strip().casefold()
        if content_type
        else ""
    )
    if (
        normalized_mime
        and normalized_mime not in allowed_mime_types
    ):
        raise ProcessingError(
            "INVALID_MIME_TYPE",
            f"Недопустимый MIME-тип: {normalized_mime}",
        )
    return safe_name


def validate_pdf_header(header: bytes) -> None:
    if not header:
        raise ProcessingError(
            "EMPTY_FILE",
            "Загружен пустой файл",
        )
    if not header.startswith(b"%PDF-"):
        raise ProcessingError(
            "INVALID_PDF",
            "Содержимое файла не похоже на PDF",
        )
