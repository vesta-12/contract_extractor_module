from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse

from document_processing.errors import ProcessingError
from document_processing.files import (
    safe_file_name,
    validate_upload_metadata,
)
from document_processing.jobs import JobManager
from document_processing.models import ProcessingStatus
from document_processing.storage import FileJobStore, utc_now
from document_processing.uploads import save_upload


router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _components(request: Request) -> tuple[FileJobStore, JobManager]:
    return request.app.state.job_store, request.app.state.job_manager


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in job.items()
        if key not in {"archive_path"}
    }
    documents: list[dict[str, Any]] = []
    for document in job.get("documents", []):
        public_document = {
            key: value
            for key, value in document.items()
            if key not in {"source_path", "output_files"}
        }
        output_files = document.get("output_files", {})
        document_id = document["document_id"]
        public_document["downloads"] = {
            "annotated_document": (
                f"/api/documents/{document_id}/download"
                if output_files.get("annotated_document")
                else None
            ),
            "result_json": (
                f"/api/documents/{document_id}/json"
                if output_files.get("result_json")
                else None
            ),
        }
        documents.append(public_document)
    payload["documents"] = documents
    payload["download_url"] = (
        f"/api/jobs/{job['job_id']}/download"
        if job.get("archive_path")
        else None
    )
    return payload


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    components = request.app.state.processor.health()
    components_ready = all(
        bool(component.get("ready"))
        for component in components.values()
    )
    storage_ready = request.app.state.job_store.root.is_dir()
    return {
        "status": (
            "ready"
            if components_ready and storage_ready
            else "degraded"
        ),
        "components": components,
        "storage": {
            "ready": storage_ready
        },
        "limits": {
            "max_file_size_bytes": (
                request.app.state.config.max_file_size_bytes
            ),
            "max_files": request.app.state.config.max_files,
            "allowed_extensions": list(
                request.app.state.config.allowed_extensions
            ),
        },
    }


@router.post(
    "/documents/process",
    status_code=status.HTTP_202_ACCEPTED,
)
async def process_documents(
    request: Request,
    files: list[UploadFile] = File(...),
) -> JSONResponse:
    config = request.app.state.config
    store, manager = _components(request)
    if not files:
        raise ProcessingError(
            "NO_FILES",
            "Не выбран ни один файл",
        )
    if len(files) > config.max_files:
        raise ProcessingError(
            "TOO_MANY_FILES",
            "Превышено максимальное количество файлов",
            details={"max_files": config.max_files},
        )

    job = store.create_job()
    job_id = job["job_id"]
    for upload in files:
        document_id = str(uuid4())
        source_file_name = safe_file_name(
            upload.filename or "document.pdf"
        )
        document = {
            "document_id": document_id,
            "source_file_name": source_file_name,
            "content_type": upload.content_type,
            "size_bytes": 0,
            "status": ProcessingStatus.QUEUED.value,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "page_count": None,
            "entity_count": 0,
            "entities": [],
            "warnings": [],
            "errors": [],
            "output_files": {},
            "timing": {},
        }
        try:
            source_file_name = validate_upload_metadata(
                upload.filename or "document.pdf",
                upload.content_type,
                allowed_extensions=frozenset(
                    config.allowed_extensions
                ),
                allowed_mime_types=frozenset(
                    config.allowed_mime_types
                ),
            )
            source_path = store.source_path(
                job_id,
                document_id,
                Path(source_file_name).suffix,
            )
            size_bytes = await save_upload(
                upload,
                source_path,
                max_size_bytes=config.max_file_size_bytes,
                chunk_size=config.upload_chunk_size_bytes,
            )
            document.update(
                {
                    "source_file_name": source_file_name,
                    "size_bytes": size_bytes,
                    "source_path": str(
                        source_path.relative_to(store.job_dir(job_id))
                    ),
                }
            )
        except ProcessingError as error:
            document.update(
                {
                    "status": ProcessingStatus.FAILED.value,
                    "errors": [error.to_dict()],
                    "completed_at": utc_now(),
                }
            )
        except Exception:
            logger.exception(
                "Ошибка сохранения upload job=%s document=%s",
                job_id,
                document_id,
            )
            document.update(
                {
                    "status": ProcessingStatus.FAILED.value,
                    "errors": [
                        {
                            "code": "UPLOAD_SAVE_ERROR",
                            "message": "Не удалось сохранить файл",
                            "details": None,
                        }
                    ],
                    "completed_at": utc_now(),
                }
            )
        finally:
            await upload.close()
        store.add_document(job_id, document)

    manager.submit(job_id)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=public_job(store.get_job(job_id)),
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    store, _ = _components(request)
    return public_job(store.get_job(job_id))


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, request: Request) -> FileResponse:
    return _document_file_response(
        request,
        document_id,
        artifact_key="annotated_document",
        media_type="application/pdf",
        output_suffix="_annotated.pdf",
    )


@router.get("/documents/{document_id}/json")
def download_json(document_id: str, request: Request) -> FileResponse:
    return _document_file_response(
        request,
        document_id,
        artifact_key="result_json",
        media_type="application/json",
        output_suffix="_result.json",
    )


@router.get("/jobs/{job_id}/download")
def download_job(job_id: str, request: Request) -> FileResponse:
    store, _ = _components(request)
    job = store.get_job(job_id)
    archive_path = job.get("archive_path")
    if not archive_path:
        raise ProcessingError(
            "RESULT_NOT_READY",
            "Архив задания ещё не готов",
        )
    path = store.resolve_artifact(job_id, archive_path)
    if not path.is_file():
        raise ProcessingError(
            "RESULT_NOT_FOUND",
            "Архив задания не найден",
        )
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"job_{job_id}_results.zip",
    )


def _document_file_response(
    request: Request,
    document_id: str,
    *,
    artifact_key: str,
    media_type: str,
    output_suffix: str,
) -> FileResponse:
    store, _ = _components(request)
    job, document = store.find_document(document_id)
    relative_path = document.get("output_files", {}).get(artifact_key)
    if not relative_path:
        raise ProcessingError(
            "RESULT_NOT_READY",
            "Запрошенный результат ещё не готов",
        )
    path = store.resolve_artifact(job["job_id"], relative_path)
    if not path.is_file():
        raise ProcessingError(
            "RESULT_NOT_FOUND",
            "Файл результата не найден",
        )
    source_name = safe_file_name(document["source_file_name"])
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"{Path(source_name).stem}{output_suffix}",
    )
