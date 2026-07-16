from __future__ import annotations
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from contract_extractor.exceptions import OCRLoadError
from contract_extractor.input.validator import (
    validate_ocr_payload,
)
from contract_extractor.models import (
    BoundingBox,
    OCRDocument,
    OCRPage,
    OCRRegion,
    OCRValue,
    OCRWord,
)


class OCRDocumentLoader:

    def load(
        self,
        file_path: str | Path,
    ) -> OCRDocument:
        path = Path(file_path).expanduser().resolve()

        if not path.exists():
            raise OCRLoadError(
                f"OCR JSON не найден: {path}"
            )

        if not path.is_file():
            raise OCRLoadError(
                "указанный путь не является файлом: "
                f"{path}"
            )

        try:
            content = path.read_text(
                encoding="utf-8-sig"
            )
        except OSError as error:
            raise OCRLoadError(
                "ошибка при чтении: "
                f"{path}"
            ) from error

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise OCRLoadError(
                f"некорректный JSON в файле {path}: "
                f"строка {error.lineno}, "
                f"столбец {error.colno}: "
                f"{error.msg}"
            ) from error

        return self.from_dict(
            payload,
            source_path=path,
        )

    def from_dict(
        self,
        payload: Mapping[str, Any],
        *,
        source_path: str | Path | None = None,
    ) -> OCRDocument:
        validate_ocr_payload(payload)

        values = tuple(
            self._build_value(
                data=value_data,
                index=value_index,
            )
            for value_index, value_data
            in enumerate(
                payload.get("values", [])
            )
        )

        pages = tuple(
            self._build_page(page_data)
            for page_data in payload["pages"]
        )

        resolved_source_path = (
            Path(source_path).expanduser().resolve()
            if source_path is not None
            else None
        )

        return OCRDocument(
            document_text=payload["document_text"],
            pages=pages,
            values=values,
            meta=dict(payload.get("meta", {})),
            quality=dict(
                payload.get("quality", {})
            ),
            timing=dict(
                payload.get("timing", {})
            ),
            source_path=resolved_source_path,
        )

    def _build_page(
        self,
        data: Mapping[str, Any],
    ) -> OCRPage:
        page_number = int(data["page"])

        regions = tuple(
            self._build_region(
                data=region_data,
                page_number=page_number,
                index=region_index,
            )
            for region_index, region_data
            in enumerate(
                data.get("regions", [])
            )
        )

        words = tuple(
            self._build_word(
                data=word_data,
                page_number=page_number,
                index=word_index,
                region=self._resolve_region(
                    word_index=word_index,
                    regions=regions,
                ),
            )
            for word_index, word_data
            in enumerate(data["words"])
        )

        text_span = self._optional_pair(
            value=data.get("text_span"),
            caster=int,
        )

        pdf_size = self._optional_pair(
            value=data.get("pdf_size"),
            caster=float,
        )

        return OCRPage(
            number=page_number,
            words=words,
            regions=regions,
            text_span=text_span,
            order=data.get("order"),
            pdf_size=pdf_size,
            quality=dict(
                data.get("quality", {})
            ),
            timing=dict(
                data.get("timing", {})
            ),
        )

    def _build_region(
        self,
        data: Mapping[str, Any],
        page_number: int,
        index: int,
    ) -> OCRRegion:

        word_start, word_end = data["words"]

        region_bbox = (
            BoundingBox.from_sequence(
                data["bbox"]
            )
            if data.get("bbox") is not None
            else None
        )

        return OCRRegion(
            id=f"p{page_number}-r{index}",
            page=page_number,
            region_type=data["type"],
            word_start=int(word_start),
            word_end=int(word_end),
            bbox=region_bbox,
        )

    def _build_word(
        self,
        data: Mapping[str, Any],
        page_number: int,
        index: int,
        region: str | None,
    ) -> OCRWord:
        return OCRWord(
            id=f"p{page_number}-w{index}",
            page=page_number,
            index=index,
            text=data["t"],
            confidence=float(data["c"]),
            bbox=BoundingBox.from_sequence(
                data["b"]
            ),
            raw_text=data.get("r"),
            normalized_text=data.get("n"),
            value_refs=tuple(
                int(value_ref)
                for value_ref
                in data.get("v", [])
            ),
            region=region,
        )

    def _build_value(
        self,
        data: Mapping[str, Any],
        index: int,
    ) -> OCRValue:

        known_fields = {
            "type",
            "value",
            "validation",
            "page",
            "bbox",
            "words",
            "source_bbox",
            "confidence",
            "region",
            "raw",
            "corrections",
        }

        extra = {
            key: value
            for key, value in data.items()
            if key not in known_fields
        }

        source_bbox = (
            BoundingBox.from_sequence(
                data["source_bbox"]
            )
            if data.get("source_bbox") is not None
            else None
        )

        confidence = (
            float(data["confidence"])
            if data.get("confidence") is not None
            else None
        )

        return OCRValue(
            id=f"ocr-value-{index}",
            index=index,
            value_type=data["type"],
            value=data["value"],
            page=int(data["page"]),
            bbox=BoundingBox.from_sequence(
                data["bbox"]
            ),
            word_indices=tuple(
                int(word_index)
                for word_index
                in data["words"]
            ),
            confidence=confidence,
            source_bbox=source_bbox,
            raw_value=data.get("raw"),
            region=data.get("region"),
            validation=dict(
                data.get("validation", {})
            ),
            corrections=tuple(
                data.get("corrections", [])
            ),
            extra=extra,
        )

    @staticmethod
    def _resolve_region(
        word_index: int,
        regions: tuple[OCRRegion, ...],
    ) -> str | None:

        candidates = [
            region
            for region in regions
            if region.contains_word(word_index)
        ]

        if not candidates:
            return None

        best_region = min(
            candidates,
            key=lambda region: region.word_count,
        )

        return best_region.region_type

    @staticmethod
    def _optional_pair(
        value: Any,
        caster: type[int] | type[float],
    ) -> tuple[int, int] | tuple[float, float] | None:

        if value is None:
            return None

        return (
            caster(value[0]),
            caster(value[1]),
        )


def load_ocr_document(
    file_path: str | Path,
) -> OCRDocument:

    return OCRDocumentLoader().load(file_path)