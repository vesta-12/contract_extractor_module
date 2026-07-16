from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ocr import OCRConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as error:
        raise ValueError(
            f"{name} должен быть целым числом"
        ) from error


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} должен быть true или false")


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    values = tuple(
        item.strip().casefold()
        for item in raw_value.split(",")
        if item.strip()
    )
    if not values:
        raise ValueError(f"{name} не должен быть пустым")
    return values


@dataclass(frozen=True, slots=True)
class AppConfig:
    data_dir: Path = PROJECT_ROOT / "data"
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    max_file_size_bytes: int = 25 * 1024 * 1024
    max_files: int = 10
    allowed_extensions: tuple[str, ...] = (".pdf",)
    allowed_mime_types: tuple[str, ...] = (
        "application/pdf",
        "application/octet-stream",
    )
    upload_chunk_size_bytes: int = 1024 * 1024
    job_workers: int = 1
    retention_hours: int = 24
    host: str = "127.0.0.1"
    port: int = 8000
    development: bool = False
    log_level: str = "INFO"
    ocr: OCRConfig = OCRConfig()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "data_dir",
            Path(self.data_dir).expanduser().resolve(),
        )
        object.__setattr__(
            self,
            "frontend_dir",
            Path(self.frontend_dir).expanduser().resolve(),
        )
        if self.max_file_size_bytes < 1:
            raise ValueError(
                "max_file_size_bytes должен быть положительным"
            )
        if self.max_files < 1:
            raise ValueError("max_files должен быть положительным")
        normalized_extensions = tuple(
            extension.casefold()
            if extension.startswith(".")
            else f".{extension.casefold()}"
            for extension in self.allowed_extensions
        )
        object.__setattr__(
            self,
            "allowed_extensions",
            normalized_extensions,
        )
        unsupported_extensions = set(normalized_extensions) - {".pdf"}
        if unsupported_extensions:
            raise ValueError(
                "Текущий OCR поддерживает только расширение .pdf"
            )
        object.__setattr__(
            self,
            "allowed_mime_types",
            tuple(
                mime_type.casefold()
                for mime_type in self.allowed_mime_types
            ),
        )
        if self.upload_chunk_size_bytes < 1024:
            raise ValueError(
                "upload_chunk_size_bytes должен быть не меньше 1024"
            )
        if self.job_workers < 1:
            raise ValueError("job_workers должен быть положительным")
        if self.retention_hours < 1:
            raise ValueError(
                "retention_hours должен быть положительным"
            )
        if not 1 <= self.port <= 65535:
            raise ValueError("port должен быть в диапазоне 1..65535")

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            data_dir=Path(
                os.getenv("APP_DATA_DIR", PROJECT_ROOT / "data")
            ),
            frontend_dir=Path(
                os.getenv(
                    "APP_FRONTEND_DIR",
                    PROJECT_ROOT / "frontend",
                )
            ),
            max_file_size_bytes=_env_int(
                "APP_MAX_FILE_SIZE_MB",
                25,
            )
            * 1024
            * 1024,
            max_files=_env_int("APP_MAX_FILES", 10),
            allowed_extensions=_env_csv(
                "APP_ALLOWED_EXTENSIONS",
                (".pdf",),
            ),
            allowed_mime_types=_env_csv(
                "APP_ALLOWED_MIME_TYPES",
                ("application/pdf", "application/octet-stream"),
            ),
            upload_chunk_size_bytes=_env_int(
                "APP_UPLOAD_CHUNK_SIZE_BYTES",
                1024 * 1024,
            ),
            job_workers=_env_int("APP_JOB_WORKERS", 1),
            retention_hours=_env_int(
                "APP_RETENTION_HOURS",
                24,
            ),
            host=os.getenv("APP_HOST", "127.0.0.1"),
            port=_env_int("APP_PORT", 8000),
            development=_env_bool(
                "APP_DEVELOPMENT",
                False,
            ),
            log_level=os.getenv(
                "APP_LOG_LEVEL",
                "INFO",
            ).upper(),
            ocr=OCRConfig(
                dpi=_env_int("OCR_DPI", 300),
                language=os.getenv(
                    "OCR_LANGUAGE",
                    "rus+eng",
                ),
                timeout_seconds=_env_int(
                    "OCR_TIMEOUT_SECONDS",
                    120,
                ),
                workers=_env_int("OCR_WORKERS", 4),
                pretty_json=_env_bool(
                    "OCR_PRETTY_JSON",
                    False,
                ),
            ),
        )
