from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from document_processing.config import AppConfig
from document_processing.errors import ProcessingError
from document_processing.storage import FileJobStore


def build_store(tmp_path: Path) -> FileJobStore:
    return FileJobStore(
        AppConfig(
            data_dir=tmp_path / "data",
            frontend_dir=tmp_path / "frontend",
        )
    )


def test_source_paths_are_unique_for_duplicate_names(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    job = store.create_job()
    first_id = str(uuid4())
    second_id = str(uuid4())

    first = store.source_path(job["job_id"], first_id)
    second = store.source_path(job["job_id"], second_id)

    assert first != second
    assert first.name == f"{first_id}.pdf"
    assert second.name == f"{second_id}.pdf"


def test_store_rejects_path_traversal(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    job = store.create_job()

    with pytest.raises(ProcessingError) as caught:
        store.resolve_artifact(job["job_id"], "../outside.json")

    assert caught.value.code == "INVALID_RESULT_PATH"


def test_store_persists_and_finds_document(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    job = store.create_job()
    document_id = str(uuid4())
    store.add_document(
        job["job_id"],
        {
            "document_id": document_id,
            "source_file_name": "contract.pdf",
            "status": "queued",
        },
    )

    found_job, document = store.find_document(document_id)

    assert found_job["job_id"] == job["job_id"]
    assert document["source_file_name"] == "contract.pdf"
