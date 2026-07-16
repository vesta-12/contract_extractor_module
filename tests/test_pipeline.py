from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from document_processing.config import AppConfig
from document_processing.pipeline import DocumentProcessingService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_OCR = (
    PROJECT_ROOT
    / "data"
    / "input"
    / "formatted"
    / "test_loan_agreement_ocr.json"
)
SAMPLE_PDF = (
    PROJECT_ROOT
    / "data"
    / "input"
    / "raw"
    / "test_loan_agreement_anonymized.pdf"
)


class PayloadOCR:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def health(self) -> dict[str, Any]:
        return {"ready": True, "engine": "test"}

    def process(
        self,
        _source_path: str | Path,
        output_path: str | Path,
    ) -> dict[str, Any]:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.payload


class FileVisualizer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def render_pdf(
        self,
        source_pdf_path: str | Path,
        result_json_path: str | Path,
        output_dir: str | Path,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert Path(source_pdf_path).is_file()
        assert Path(result_json_path).is_file()
        if self.fail:
            raise RuntimeError("annotation failed")
        path = Path(output_dir) / "annotated.clean.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n%%EOF")
        return {"clean_pdf_path": str(path)}


def sample_payload() -> dict[str, Any]:
    return json.loads(SAMPLE_OCR.read_text(encoding="utf-8"))


def build_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
    )


def test_orchestration_uses_current_ocr_output(tmp_path: Path) -> None:
    processor = DocumentProcessingService(
        build_config(tmp_path),
        ocr_processor=PayloadOCR(sample_payload()),
        visualizer_factory=lambda: FileVisualizer(),
    )
    document_id = str(uuid4())
    job_dir = tmp_path / "job"

    result = processor.process_document(
        job_dir=job_dir,
        document_id=document_id,
        source_path=SAMPLE_PDF,
        source_file_name=SAMPLE_PDF.name,
    )

    assert result["status"] == "completed"
    assert result["page_count"] == 2
    assert result["entity_count"] == 31
    final_path = job_dir / result["output_files"]["result_json"]
    final_payload = json.loads(final_path.read_text(encoding="utf-8"))
    assert final_payload["ocr_result"]["meta"]["schema"] == "3.0-production"
    assert len(final_payload["extracted_entities"]) == 31


def test_extraction_failure_preserves_ocr_result(tmp_path: Path) -> None:
    def failing_extractor(_path: str | Path):
        raise RuntimeError("extractor failed")

    processor = DocumentProcessingService(
        build_config(tmp_path),
        ocr_processor=PayloadOCR(sample_payload()),
        extractor=failing_extractor,
        visualizer_factory=lambda: FileVisualizer(),
    )
    job_dir = tmp_path / "job"

    result = processor.process_document(
        job_dir=job_dir,
        document_id=str(uuid4()),
        source_path=SAMPLE_PDF,
        source_file_name=SAMPLE_PDF.name,
    )

    assert result["status"] == "partially_completed"
    assert result["errors"][0]["code"] == "EXTRACTION_ERROR"
    final_path = job_dir / result["output_files"]["result_json"]
    payload = json.loads(final_path.read_text(encoding="utf-8"))
    assert payload["ocr_result"]["meta"]["pages"] == 2
    assert payload["extraction_result"] is None


def test_annotation_failure_preserves_extraction(tmp_path: Path) -> None:
    processor = DocumentProcessingService(
        build_config(tmp_path),
        ocr_processor=PayloadOCR(sample_payload()),
        visualizer_factory=lambda: FileVisualizer(fail=True),
    )
    job_dir = tmp_path / "job"

    result = processor.process_document(
        job_dir=job_dir,
        document_id=str(uuid4()),
        source_path=SAMPLE_PDF,
        source_file_name=SAMPLE_PDF.name,
    )

    assert result["status"] == "partially_completed"
    assert result["entity_count"] == 31
    assert result["errors"][0]["code"] == "ANNOTATION_ERROR"
    final_path = job_dir / result["output_files"]["result_json"]
    payload = json.loads(final_path.read_text(encoding="utf-8"))
    assert payload["extraction_result"]["summary"]["entity_count"] == 31


def test_document_without_entities_is_completed_with_warning(
    tmp_path: Path,
) -> None:
    payload = {
        "document_text": "обычный текст",
        "meta": {"schema": "3.0-production", "pages": 1},
        "quality": {},
        "timing": {},
        "values": [],
        "pages": [
            {
                "page": 1,
                "words": [
                    {
                        "t": "текст",
                        "c": 0.99,
                        "b": [0.1, 0.1, 0.2, 0.2],
                    }
                ],
                "regions": [],
            }
        ],
    }
    processor = DocumentProcessingService(
        build_config(tmp_path),
        ocr_processor=PayloadOCR(payload),
        visualizer_factory=lambda: FileVisualizer(),
    )

    result = processor.process_document(
        job_dir=tmp_path / "job",
        document_id=str(uuid4()),
        source_path=SAMPLE_PDF,
        source_file_name="no-entities.pdf",
    )

    assert result["status"] == "completed"
    assert result["entity_count"] == 0
    assert "не найдено" in result["warnings"][0]
