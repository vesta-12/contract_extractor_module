from __future__ import annotations

import pytest

from document_processing.errors import ProcessingError
from document_processing.files import (
    safe_file_name,
    validate_pdf_header,
    validate_upload_metadata,
)


def test_safe_file_name_removes_path_and_unsafe_characters() -> None:
    assert safe_file_name("../../Договор № 1.pdf") == "Договор_1.pdf"
    assert safe_file_name(r"C:\temp\contract.pdf") == "contract.pdf"


def test_validate_upload_metadata_accepts_pdf() -> None:
    assert (
        validate_upload_metadata("contract.PDF", "application/pdf")
        == "contract.pdf"
    )


@pytest.mark.parametrize(
    ("file_name", "mime_type", "code"),
    [
        ("contract.txt", "text/plain", "UNSUPPORTED_FORMAT"),
        ("scan.png", "image/png", "UNSUPPORTED_FORMAT"),
        ("contract.pdf", "image/png", "INVALID_MIME_TYPE"),
    ],
)
def test_validate_upload_metadata_rejects_invalid_input(
    file_name: str,
    mime_type: str,
    code: str,
) -> None:
    with pytest.raises(ProcessingError) as caught:
        validate_upload_metadata(file_name, mime_type)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("header", "code"),
    [(b"", "EMPTY_FILE"), (b"not-pdf", "INVALID_PDF")],
)
def test_validate_pdf_header_rejects_invalid_content(
    header: bytes,
    code: str,
) -> None:
    with pytest.raises(ProcessingError) as caught:
        validate_pdf_header(header)
    assert caught.value.code == code
