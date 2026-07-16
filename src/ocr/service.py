from __future__ import annotations

from pathlib import Path
from typing import Any

from ocr.config import OCRConfig


class TesseractOCRProcessor:
    """Стабильная сервисная граница над существующим OCR-пайплайном."""

    def __init__(self, config: OCRConfig | None = None) -> None:
        self.config = config or OCRConfig()

    def process(
        self,
        source_path: str | Path,
        output_path: str | Path,
    ) -> dict[str, Any]:
        # Ленивый импорт не заставляет web-приложение и тесты
        # инициализировать тяжёлые OCR-зависимости до первого задания.
        from ocr.ocr_document import process_pdf

        return process_pdf(
            input_path=Path(source_path),
            output_path=Path(output_path),
            dpi=self.config.dpi,
            language=self.config.language,
            timeout_seconds=self.config.timeout_seconds,
            requested_workers=self.config.workers,
            pretty=self.config.pretty_json,
        )

    def health(self) -> dict[str, Any]:
        try:
            import pytesseract

            from ocr.ocr_document import configure_tesseract

            configure_tesseract()
            version = str(pytesseract.get_tesseract_version())
            available_languages = set(
                pytesseract.get_languages(config="")
            )
            required_languages = set(
                self.config.language.split("+")
            )
            missing = sorted(
                required_languages - available_languages
            )
            return {
                "ready": not missing,
                "engine": "tesseract",
                "version": version,
                "missing_languages": missing,
            }
        except Exception as error:
            return {
                "ready": False,
                "engine": "tesseract",
                "message": str(error),
            }
