from __future__ import annotations

import json
import shutil
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from document_processing.config import AppConfig
from document_processing.errors import ProcessingError
from document_processing.models import ProcessingStatus


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _validated_uuid(value: str, kind: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise ProcessingError(
            "INVALID_IDENTIFIER",
            f"Некорректный идентификатор {kind}",
        ) from error


class FileJobStore:
    """Потокобезопасное файловое хранилище заданий и их manifests."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.root = config.jobs_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create_job(self, job_id: str | None = None) -> dict[str, Any]:
        normalized_job_id = _validated_uuid(
            job_id or str(uuid4()),
            "задания",
        )
        job_dir = self.job_dir(normalized_job_id)
        with self._lock:
            try:
                job_dir.mkdir(parents=True, exist_ok=False)
            except FileExistsError as error:
                raise ProcessingError(
                    "JOB_ALREADY_EXISTS",
                    "Задание с таким идентификатором уже существует",
                ) from error
            now = utc_now()
            manifest = {
                "schema_version": 1,
                "job_id": normalized_job_id,
                "status": ProcessingStatus.QUEUED.value,
                "created_at": now,
                "updated_at": now,
                "documents": [],
                "archive_path": None,
                "warnings": [],
                "errors": [],
            }
            self._write_manifest_unlocked(normalized_job_id, manifest)
        return deepcopy(manifest)

    def add_document(
        self,
        job_id: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            manifest = self._read_manifest_unlocked(job_id)
            manifest["documents"].append(deepcopy(document))
            manifest["updated_at"] = utc_now()
            self._write_manifest_unlocked(job_id, manifest)
        return deepcopy(document)

    def update_document(
        self,
        job_id: str,
        document_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        normalized_document_id = _validated_uuid(
            document_id,
            "документа",
        )
        with self._lock:
            manifest = self._read_manifest_unlocked(job_id)
            for document in manifest["documents"]:
                if document.get("document_id") == normalized_document_id:
                    document.update(deepcopy(changes))
                    document["updated_at"] = utc_now()
                    manifest["updated_at"] = document["updated_at"]
                    self._write_manifest_unlocked(job_id, manifest)
                    return deepcopy(document)
        raise ProcessingError(
            "DOCUMENT_NOT_FOUND",
            "Документ не найден",
        )

    def update_job(
        self,
        job_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        with self._lock:
            manifest = self._read_manifest_unlocked(job_id)
            manifest.update(deepcopy(changes))
            manifest["updated_at"] = utc_now()
            self._write_manifest_unlocked(job_id, manifest)
        return deepcopy(manifest)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._read_manifest_unlocked(job_id))

    def find_document(
        self,
        document_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        normalized_document_id = _validated_uuid(
            document_id,
            "документа",
        )
        with self._lock:
            for manifest_path in self.root.glob("*/manifest.json"):
                try:
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    continue
                for document in manifest.get("documents", []):
                    if document.get("document_id") == normalized_document_id:
                        return deepcopy(manifest), deepcopy(document)
        raise ProcessingError(
            "DOCUMENT_NOT_FOUND",
            "Документ не найден",
        )

    def job_dir(self, job_id: str) -> Path:
        normalized_job_id = _validated_uuid(job_id, "задания")
        return self._ensure_within_root(self.root / normalized_job_id)

    def source_path(
        self,
        job_id: str,
        document_id: str,
        suffix: str = ".pdf",
    ) -> Path:
        normalized_document_id = _validated_uuid(
            document_id,
            "документа",
        )
        safe_suffix = suffix.casefold()
        if safe_suffix != ".pdf":
            raise ProcessingError(
                "UNSUPPORTED_FORMAT",
                "Поддерживаются только PDF-файлы",
            )
        path = (
            self.job_dir(job_id)
            / "documents"
            / normalized_document_id
            / "source"
            / f"{normalized_document_id}{safe_suffix}"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        return self._ensure_within_root(path)

    def resolve_artifact(self, job_id: str, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ProcessingError(
                "INVALID_RESULT_PATH",
                "Некорректный путь к результату",
            )
        job_dir = self.job_dir(job_id)
        resolved_path = (job_dir / candidate).resolve()
        if resolved_path != job_dir and job_dir not in resolved_path.parents:
            raise ProcessingError(
                "INVALID_RESULT_PATH",
                "Путь выходит за пределы каталога задания",
            )
        return self._ensure_within_root(resolved_path)

    def cleanup_expired(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(
            hours=self.config.retention_hours
        )
        removed = 0
        with self._lock:
            for manifest_path in self.root.glob("*/manifest.json"):
                try:
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    updated = datetime.fromisoformat(
                        manifest["updated_at"]
                    )
                except (
                    OSError,
                    KeyError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    continue
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                if updated >= cutoff:
                    continue
                job_dir = manifest_path.parent.resolve()
                self._ensure_within_root(job_dir)
                shutil.rmtree(job_dir)
                removed += 1
        return removed

    def _read_manifest_unlocked(self, job_id: str) -> dict[str, Any]:
        path = self.job_dir(job_id) / "manifest.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ProcessingError(
                "JOB_NOT_FOUND",
                "Задание не найдено",
            ) from error
        except json.JSONDecodeError as error:
            raise ProcessingError(
                "JOB_STORE_ERROR",
                "Manifest задания повреждён",
            ) from error

    def _write_manifest_unlocked(
        self,
        job_id: str,
        manifest: dict[str, Any],
    ) -> None:
        path = self.job_dir(job_id) / "manifest.json"
        temporary_path = path.with_name(
            f".{path.name}.{uuid4().hex}.tmp"
        )
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    def _ensure_within_root(self, path: Path) -> Path:
        resolved_root = self.root.resolve()
        resolved_path = path.resolve()
        if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
            raise ProcessingError(
                "INVALID_RESULT_PATH",
                "Путь выходит за пределы хранилища заданий",
            )
        return resolved_path
