from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

from contract_extractor import (
    ContractExtractionResult,
    extract_contract_data,
)
from document_processing.config import AppConfig
from document_processing.errors import ProcessingError
from document_processing.models import (
    DocumentProcessingResult,
    ProcessingIssue,
    ProcessingStatus,
)
from ocr import TesseractOCRProcessor


logger = logging.getLogger(__name__)


class OCRProcessor(Protocol):
    def process(
        self,
        source_path: str | Path,
        output_path: str | Path,
    ) -> dict[str, Any]:
        ...

    def health(self) -> dict[str, Any]:
        ...


class ResultVisualizer(Protocol):
    def render_pdf(
        self,
        source_pdf_path: str | Path,
        result_json_path: str | Path,
        output_dir: str | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        ...


ExtractionFunction = Callable[
    [str | Path],
    ContractExtractionResult,
]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class DocumentProcessingService:
    """Оркестрирует существующие компоненты, не дублируя их логику."""

    def __init__(
        self,
        config: AppConfig,
        *,
        ocr_processor: OCRProcessor | None = None,
        extractor: ExtractionFunction = extract_contract_data,
        visualizer_factory: Callable[[], ResultVisualizer] | None = None,
    ) -> None:
        self.config = config
        self.ocr_processor = (
            ocr_processor or TesseractOCRProcessor(config.ocr)
        )
        self.extractor = extractor
        self.visualizer_factory = (
            visualizer_factory or self._build_visualizer
        )

    def health(self) -> dict[str, Any]:
        try:
            import fitz
            from PIL import Image

            visualizer_health: dict[str, Any] = {
                "ready": True,
                "pymupdf_version": fitz.VersionBind,
                "pillow_version": Image.__version__,
            }
        except Exception as error:
            visualizer_health = {
                "ready": False,
                "message": str(error),
            }
        return {
            "ocr": self.ocr_processor.health(),
            "extractor": {"ready": True},
            "visualizer": visualizer_health,
        }

    def process_file(
        self,
        file_path: str | Path,
        *,
        output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        source_path = Path(file_path).resolve()
        job_dir = Path(output_dir).resolve() if output_dir else (
            self.config.data_dir
            / "standalone"
            / str(uuid4())
        )
        return self.process_document(
            job_dir=job_dir,
            document_id=str(uuid4()),
            source_path=source_path,
            source_file_name=source_path.name,
        )

    def process_files(
        self,
        file_paths: Iterable[str | Path],
        *,
        output_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        batch_dir = Path(output_dir).resolve() if output_dir else (
            self.config.data_dir
            / "standalone"
            / str(uuid4())
        )
        results: list[dict[str, Any]] = []
        for file_path in file_paths:
            source_path = Path(file_path).resolve()
            try:
                result = self.process_document(
                    job_dir=batch_dir,
                    document_id=str(uuid4()),
                    source_path=source_path,
                    source_file_name=source_path.name,
                )
            except ProcessingError as error:
                result = DocumentProcessingResult(
                    status=ProcessingStatus.FAILED,
                    errors=[
                        ProcessingIssue(
                            error.code,
                            error.message,
                            error.details,
                        )
                    ],
                ).to_dict()
            results.append(result)
        return results

    def process_document(
        self,
        *,
        job_dir: Path,
        document_id: str,
        source_path: Path,
        source_file_name: str,
    ) -> dict[str, Any]:
        started = perf_counter()
        job_dir = job_dir.resolve()
        document_dir = job_dir / "documents" / document_id
        ocr_dir = document_dir / "ocr"
        result_dir = document_dir / "results"
        annotated_dir = document_dir / "annotated"
        for directory in (ocr_dir, result_dir, annotated_dir):
            directory.mkdir(parents=True, exist_ok=True)

        ocr_path = ocr_dir / "ocr.json"
        debug_path = result_dir / "extraction.debug.json"
        production_path = result_dir / "extraction.production.json"
        final_path = result_dir / "result.json"
        output_files = {
            "ocr_json": str(ocr_path.relative_to(job_dir)),
            "result_json": str(final_path.relative_to(job_dir)),
        }

        logger.info(
            "Начало OCR job=%s document=%s file=%s",
            job_dir.name,
            document_id,
            source_file_name,
        )
        ocr_started = perf_counter()
        try:
            ocr_result = self.ocr_processor.process(source_path, ocr_path)
        except ProcessingError:
            raise
        except Exception as error:
            raise self._map_ocr_error(error) from error
        ocr_seconds = perf_counter() - ocr_started
        page_count = ocr_result.get("meta", {}).get("pages")

        logger.info(
            "Начало извлечения job=%s document=%s pages=%s",
            job_dir.name,
            document_id,
            page_count,
        )
        extraction_started = perf_counter()
        try:
            extraction_result = self.extractor(ocr_path)
            debug_payload = extraction_result.to_dict()
            production_payload = extraction_result.to_production_dict()
            write_json(debug_path, debug_payload)
            write_json(production_path, production_payload)
            output_files.update(
                {
                    "debug_json": str(debug_path.relative_to(job_dir)),
                    "extraction_json": str(
                        production_path.relative_to(job_dir)
                    ),
                }
            )
        except Exception as error:
            extraction_seconds = perf_counter() - extraction_started
            logger.exception(
                "Ошибка извлечения job=%s document=%s",
                job_dir.name,
                document_id,
            )
            issue = ProcessingIssue(
                "EXTRACTION_ERROR",
                "OCR выполнен, но не удалось извлечь данные",
            )
            timing = self._timing(
                started,
                ocr_seconds=ocr_seconds,
                extraction_seconds=extraction_seconds,
            )
            final_payload = self._final_payload(
                document_id=document_id,
                source_file_name=source_file_name,
                status=ProcessingStatus.PARTIALLY_COMPLETED,
                page_count=page_count,
                ocr_result=ocr_result,
                extraction_payload=None,
                entities=[],
                output_files=output_files,
                warnings=[],
                errors=[issue],
                timing=timing,
            )
            write_json(final_path, final_payload)
            return DocumentProcessingResult(
                status=ProcessingStatus.PARTIALLY_COMPLETED,
                page_count=page_count,
                errors=[issue],
                output_files=output_files,
                timing=timing,
            ).to_dict()
        extraction_seconds = perf_counter() - extraction_started

        entities = self._entity_summaries(extraction_result)
        warnings = list(extraction_result.warnings)
        if not entities:
            warnings.append("В документе не найдено важных сущностей")

        logger.info(
            "Начало разметки job=%s document=%s entities=%s",
            job_dir.name,
            document_id,
            extraction_result.entity_count,
        )
        visualization_started = perf_counter()
        try:
            summary = self.visualizer_factory().render_pdf(
                source_pdf_path=source_path,
                result_json_path=debug_path,
                output_dir=annotated_dir,
            )
            annotated_path_value = summary.get("clean_pdf_path")
            if not annotated_path_value:
                raise RuntimeError(
                    "Визуализатор не сформировал clean PDF"
                )
            annotated_path = Path(annotated_path_value).resolve()
            output_files["annotated_document"] = str(
                annotated_path.relative_to(job_dir)
            )
        except Exception:
            visualization_seconds = (
                perf_counter() - visualization_started
            )
            logger.exception(
                "Ошибка разметки job=%s document=%s",
                job_dir.name,
                document_id,
            )
            issue = ProcessingIssue(
                "ANNOTATION_ERROR",
                (
                    "Данные извлечены, но не удалось сформировать "
                    "документ с выделением"
                ),
            )
            timing = self._timing(
                started,
                ocr_seconds=ocr_seconds,
                extraction_seconds=extraction_seconds,
                visualization_seconds=visualization_seconds,
            )
            final_payload = self._final_payload(
                document_id=document_id,
                source_file_name=source_file_name,
                status=ProcessingStatus.PARTIALLY_COMPLETED,
                page_count=page_count,
                ocr_result=ocr_result,
                extraction_payload=production_payload,
                entities=entities,
                output_files=output_files,
                warnings=warnings,
                errors=[issue],
                timing=timing,
            )
            write_json(final_path, final_payload)
            return DocumentProcessingResult(
                status=ProcessingStatus.PARTIALLY_COMPLETED,
                page_count=page_count,
                entity_count=extraction_result.entity_count,
                entities=entities,
                warnings=warnings,
                errors=[issue],
                output_files=output_files,
                timing=timing,
            ).to_dict()
        visualization_seconds = perf_counter() - visualization_started

        timing = self._timing(
            started,
            ocr_seconds=ocr_seconds,
            extraction_seconds=extraction_seconds,
            visualization_seconds=visualization_seconds,
        )
        final_payload = self._final_payload(
            document_id=document_id,
            source_file_name=source_file_name,
            status=ProcessingStatus.COMPLETED,
            page_count=page_count,
            ocr_result=ocr_result,
            extraction_payload=production_payload,
            entities=entities,
            output_files=output_files,
            warnings=warnings,
            errors=[],
            timing=timing,
        )
        write_json(final_path, final_payload)
        logger.info(
            (
                "Документ завершён job=%s document=%s "
                "ocr=%.3fs extraction=%.3fs "
                "annotation=%.3fs total=%.3fs"
            ),
            job_dir.name,
            document_id,
            ocr_seconds,
            extraction_seconds,
            visualization_seconds,
            timing["total_seconds"],
        )
        return DocumentProcessingResult(
            status=ProcessingStatus.COMPLETED,
            page_count=page_count,
            entity_count=extraction_result.entity_count,
            entities=entities,
            warnings=warnings,
            output_files=output_files,
            timing=timing,
        ).to_dict()

    @staticmethod
    def _entity_summaries(
        extraction_result: ContractExtractionResult,
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": entity.entity_type,
                "value": entity.value,
                "confidence": round(entity.confidence, 6),
                "locations": [
                    {
                        "page": entity.page,
                        "bbox": entity.bbox.to_list(),
                    }
                ],
            }
            for entity in extraction_result.entities
        ]

    @staticmethod
    def _final_payload(
        *,
        document_id: str,
        source_file_name: str,
        status: ProcessingStatus,
        page_count: int | None,
        ocr_result: dict[str, Any],
        extraction_payload: dict[str, Any] | None,
        entities: list[dict[str, Any]],
        output_files: dict[str, str],
        warnings: list[str],
        errors: list[ProcessingIssue],
        timing: dict[str, float],
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "source_file_name": source_file_name,
            "status": status.value,
            "page_count": page_count,
            "ocr_result": ocr_result,
            "extracted_entities": entities,
            "extraction_result": extraction_payload,
            "output_files": dict(output_files),
            "warnings": list(warnings),
            "errors": [error.to_dict() for error in errors],
            "timing": dict(timing),
        }

    @staticmethod
    def _timing(
        started: float,
        *,
        ocr_seconds: float,
        extraction_seconds: float = 0.0,
        visualization_seconds: float = 0.0,
    ) -> dict[str, float]:
        return {
            "ocr_seconds": round(ocr_seconds, 4),
            "extraction_seconds": round(extraction_seconds, 4),
            "visualization_seconds": round(
                visualization_seconds,
                4,
            ),
            "total_seconds": round(perf_counter() - started, 4),
        }

    @staticmethod
    def _map_ocr_error(error: Exception) -> ProcessingError:
        error_name = type(error).__name__
        message = str(error).casefold()
        if isinstance(error, FileNotFoundError):
            return ProcessingError(
                "SOURCE_FILE_NOT_FOUND",
                "Исходный файл не найден",
            )
        if error_name in {"FileDataError", "EmptyFileError"}:
            return ProcessingError(
                "CORRUPTED_PDF",
                "PDF повреждён или имеет некорректную структуру",
            )
        if "не содержит страниц" in message:
            return ProcessingError(
                "PDF_HAS_NO_PAGES",
                "PDF не содержит страниц",
            )
        if (
            "tesseract" in message
            and (
                "not installed" in message
                or "не установлены языки" in message
                or "not found" in message
            )
        ):
            return ProcessingError(
                "OCR_ENGINE_UNAVAILABLE",
                "OCR-движок или языковые данные недоступны",
            )
        return ProcessingError(
            "OCR_PROCESSING_ERROR",
            "Не удалось распознать документ",
        )

    @staticmethod
    def _build_visualizer() -> ResultVisualizer:
        from contract_extractor.visualization import (
            ContractResultVisualizer,
            VisualizationConfig,
        )

        return ContractResultVisualizer(
            VisualizationConfig(
                create_clean_images=False,
                create_review_images=False,
                create_clean_pdf=True,
                create_review_pdf=False,
                create_summary_json=True,
            )
        )
