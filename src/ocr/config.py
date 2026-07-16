from __future__ import annotations

from dataclasses import dataclass


DEFAULT_DPI = 300
DEFAULT_LANGUAGE = "rus+eng"
DEFAULT_TESSERACT_CONFIG = "--oem 1 --psm 3"
DEFAULT_TIMEOUT_SECONDS = 120
MAX_WORKERS = 4


@dataclass(frozen=True, slots=True)
class OCRConfig:
    dpi: int = DEFAULT_DPI
    language: str = DEFAULT_LANGUAGE
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    workers: int = MAX_WORKERS
    pretty_json: bool = False

    def __post_init__(self) -> None:
        if self.dpi < 72:
            raise ValueError("OCR dpi должен быть не меньше 72")
        if not self.language.strip():
            raise ValueError("OCR language не должен быть пустым")
        if self.timeout_seconds < 1:
            raise ValueError("OCR timeout должен быть положительным")
        if not 1 <= self.workers <= MAX_WORKERS:
            raise ValueError(
                f"OCR workers должен быть в диапазоне 1..{MAX_WORKERS}"
            )
