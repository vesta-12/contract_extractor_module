from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

from document_processing.archive import create_job_archive
from document_processing.config import AppConfig
from document_processing.errors import ProcessingError
from document_processing.models import ProcessingStatus
from document_processing.pipeline import DocumentProcessingService
from document_processing.storage import FileJobStore, utc_now


logger = logging.getLogger(__name__)


class JobManager:
    """Локальная заменяемая очередь поверх файлового job store."""

    def __init__(
        self,
        config: AppConfig,
        store: FileJobStore,
        processor: DocumentProcessingService,
    ) -> None:
        self.config = config
        self.store = store
        self.processor = processor
        self._executor = ThreadPoolExecutor(
            max_workers=config.job_workers,
            thread_name_prefix="document-job",
        )
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.Lock()

    def submit(self, job_id: str) -> None:
        with self._lock:
            current = self._futures.get(job_id)
            if current is not None and not current.done():
                return
            future = self._executor.submit(self._run_job, job_id)
            self._futures[job_id] = future
            future.add_done_callback(
                lambda _future: self._forget(job_id)
            )

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def _run_job(self, job_id: str) -> None:
        logger.info("Начало задания job=%s", job_id)
        try:
            self.store.update_job(
                job_id,
                status=ProcessingStatus.PROCESSING.value,
                started_at=utc_now(),
            )
            manifest = self.store.get_job(job_id)
            for document in manifest.get("documents", []):
                if document.get("status") != ProcessingStatus.QUEUED.value:
                    continue
                self._run_document(job_id, document)
            final_manifest = self.store.get_job(job_id)
            final_status = self._derive_status(final_manifest)
            completed_at = utc_now()
            archive_manifest = deepcopy(final_manifest)
            archive_manifest["status"] = final_status.value
            archive_manifest["completed_at"] = completed_at
            try:
                archive_path = create_job_archive(
                    self.store,
                    archive_manifest,
                )
            except ProcessingError as error:
                if error.code != "NO_RESULTS_FOR_ARCHIVE":
                    logger.exception(
                        "Ошибка ZIP job=%s code=%s",
                        job_id,
                        error.code,
                    )
                    errors = list(final_manifest.get("errors", []))
                    errors.append(error.to_dict())
                    if final_status == ProcessingStatus.COMPLETED:
                        final_status = ProcessingStatus.PARTIALLY_COMPLETED
                    self.store.update_job(
                        job_id,
                        status=final_status.value,
                        completed_at=completed_at,
                        errors=errors,
                    )
                else:
                    self.store.update_job(
                        job_id,
                        status=final_status.value,
                        completed_at=completed_at,
                    )
            else:
                self.store.update_job(
                    job_id,
                    status=final_status.value,
                    completed_at=completed_at,
                    archive_path=archive_path,
                )
            logger.info(
                "Задание завершено job=%s status=%s",
                job_id,
                final_status.value,
            )
        except Exception:
            logger.exception("Критическая ошибка задания job=%s", job_id)
            try:
                manifest = self.store.get_job(job_id)
                errors = list(manifest.get("errors", []))
                errors.append(
                    {
                        "code": "JOB_PROCESSING_ERROR",
                        "message": "Не удалось обработать задание",
                        "details": None,
                    }
                )
                self.store.update_job(
                    job_id,
                    status=ProcessingStatus.FAILED.value,
                    completed_at=utc_now(),
                    errors=errors,
                )
            except Exception:
                logger.exception(
                    "Не удалось сохранить ошибку задания job=%s",
                    job_id,
                )

    def _run_document(
        self,
        job_id: str,
        document: dict[str, Any],
    ) -> None:
        document_id = document["document_id"]
        self.store.update_document(
            job_id,
            document_id,
            status=ProcessingStatus.PROCESSING.value,
            started_at=utc_now(),
        )
        try:
            source_path = self.store.resolve_artifact(
                job_id,
                document["source_path"],
            )
            result = self.processor.process_document(
                job_dir=self.store.job_dir(job_id),
                document_id=document_id,
                source_path=source_path,
                source_file_name=document["source_file_name"],
            )
        except ProcessingError as error:
            logger.exception(
                "Ошибка документа job=%s document=%s code=%s",
                job_id,
                document_id,
                error.code,
            )
            result = {
                "status": ProcessingStatus.FAILED.value,
                "page_count": None,
                "entity_count": 0,
                "entities": [],
                "warnings": [],
                "errors": [error.to_dict()],
                "output_files": {},
                "timing": {},
            }
        except Exception:
            logger.exception(
                "Неожиданная ошибка job=%s document=%s",
                job_id,
                document_id,
            )
            result = {
                "status": ProcessingStatus.FAILED.value,
                "page_count": None,
                "entity_count": 0,
                "entities": [],
                "warnings": [],
                "errors": [
                    {
                        "code": "DOCUMENT_PROCESSING_ERROR",
                        "message": "Не удалось обработать документ",
                        "details": None,
                    }
                ],
                "output_files": {},
                "timing": {},
            }
        result["completed_at"] = utc_now()
        self.store.update_document(job_id, document_id, **result)

    @staticmethod
    def _derive_status(job: dict[str, Any]) -> ProcessingStatus:
        statuses = {
            document.get("status")
            for document in job.get("documents", [])
        }
        if statuses == {ProcessingStatus.COMPLETED.value}:
            return ProcessingStatus.COMPLETED
        if statuses and statuses <= {ProcessingStatus.FAILED.value}:
            return ProcessingStatus.FAILED
        if (
            ProcessingStatus.COMPLETED.value in statuses
            or ProcessingStatus.PARTIALLY_COMPLETED.value in statuses
        ):
            return ProcessingStatus.PARTIALLY_COMPLETED
        return ProcessingStatus.FAILED
