from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from document_processing.errors import ProcessingError
from document_processing.files import safe_file_name
from document_processing.storage import FileJobStore


def create_job_archive(
    store: FileJobStore,
    job: dict[str, Any],
) -> str:
    job_id = job["job_id"]
    archive_dir = store.job_dir(job_id) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / "results.zip"
    temporary_path = archive_dir / ".results.zip.tmp"

    public_manifest = {
        key: value
        for key, value in job.items()
        if key not in {"archive_path"}
    }
    for document in public_manifest.get("documents", []):
        document.pop("source_path", None)

    written_files = 0
    try:
        with ZipFile(
            temporary_path,
            mode="w",
            compression=ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    public_manifest,
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for document in job.get("documents", []):
                source_name = safe_file_name(
                    document.get("source_file_name", "document.pdf")
                )
                folder = (
                    f"{Path(source_name).stem}_"
                    f"{document['document_id'][:8]}"
                )
                output_files = document.get("output_files", {})
                for key, archive_name in (
                    ("annotated_document", "annotated.pdf"),
                    ("result_json", "result.json"),
                ):
                    relative_path = output_files.get(key)
                    if not relative_path:
                        continue
                    artifact = store.resolve_artifact(
                        job_id,
                        relative_path,
                    )
                    if artifact.is_file():
                        archive.write(
                            artifact,
                            arcname=f"{folder}/{archive_name}",
                        )
                        written_files += 1
        if written_files == 0:
            raise ProcessingError(
                "NO_RESULTS_FOR_ARCHIVE",
                "Нет готовых файлов для формирования ZIP",
            )
        temporary_path.replace(archive_path)
    except ProcessingError:
        temporary_path.unlink(missing_ok=True)
        raise
    except Exception as error:
        temporary_path.unlink(missing_ok=True)
        raise ProcessingError(
            "ZIP_CREATION_ERROR",
            "Не удалось сформировать ZIP-архив",
        ) from error

    return str(archive_path.relative_to(store.job_dir(job_id)))
