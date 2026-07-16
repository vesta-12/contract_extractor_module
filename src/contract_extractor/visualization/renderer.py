from __future__ import annotations
import io
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
import fitz
from PIL import Image, ImageDraw, ImageFont

LabelMode = Literal["numbered", "compact", "full", "none"]
LegendMode = Literal["side", "none"]

@dataclass(frozen=True, slots=True)
class VisualizationConfig:
    dpi: int = 170
    line_width: int = 2
    fill_alpha: int = 30

    font_size: int = 11
    badge_font_size: int = 10
    legend_font_size: int = 11
    legend_title_font_size: int = 15

    label_mode: LabelMode = "numbered"
    legend_mode: LegendMode = "side"

    draw_page_header: bool = True
    draw_confidence_in_legend: bool = False
    draw_owner_in_legend: bool = True

    legend_width_ratio: float = 0.42
    legend_padding: int = 18
    legend_item_gap: int = 10
    legend_max_value_length: int = 78

    create_clean_images: bool = True
    create_review_images: bool = True
    create_clean_pdf: bool = True
    create_review_pdf: bool = True
    create_summary_json: bool = True

    def __post_init__(self) -> None:
        if self.dpi < 72:
            raise ValueError("dpi должен быть не меньше 72")
        if self.line_width < 1:
            raise ValueError("line_width должен быть не меньше 1")
        if not 0 <= self.fill_alpha <= 255:
            raise ValueError("fill_alpha должен быть в диапазоне 0..255")
        if self.font_size < 8 or self.badge_font_size < 8:
            raise ValueError("размер шрифта должен быть не меньше 8")
        if self.legend_font_size < 8:
            raise ValueError("legend_font_size должен быть не меньше 8")
        if not 0.20 <= self.legend_width_ratio <= 0.90:
            raise ValueError("legend_width_ratio должен быть в диапазоне 0.20..0.90")
        if self.label_mode not in {"numbered", "compact", "full", "none"}:
            raise ValueError(f"неизвестный label_mode: {self.label_mode}")
        if self.legend_mode not in {"side", "none"}:
            raise ValueError(f"неизвестный legend_mode: {self.legend_mode}")


@dataclass(frozen=True, slots=True)
class VisualizationEntity:
    id: str
    entity_type: str
    value: str
    page: int
    bbox: tuple[float, float, float, float]
    confidence: float | None = None
    owner_role: str | None = None
    owner_name: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def owner_label(self) -> str | None:
        if self.owner_role and self.owner_name:
            return f"{self.owner_role}: {self.owner_name}"
        return self.owner_role or self.owner_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "value": self.value,
            "page": self.page,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "owner_role": self.owner_role,
            "owner_name": self.owner_name,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True, slots=True)
class _DrawnEntity:
    number: int
    entity: VisualizationEntity
    pixel_bbox: tuple[int, int, int, int]
    color: tuple[int, int, int]


class ContractResultVisualizer:
    _type_colors: dict[str, tuple[int, int, int]] = {
        "date": (37, 99, 235),
        "money_amount": (5, 150, 105),
        "percentage": (217, 119, 6),
        "organization": (79, 70, 229),
        "person_name": (219, 39, 119),
        "position": (234, 88, 12),
        "address": (124, 58, 237),
        "bin": (220, 38, 38),
        "iin": (185, 28, 28),
        "inn": (239, 68, 68),
        "bank_name": (2, 132, 199),
        "bik": (22, 163, 74),
        "bic_swift": (8, 145, 178),
        "iban": (13, 148, 136),
        "bank_account": (4, 120, 87),
    }

    _type_labels: dict[str, str] = {
        "date": "Дата",
        "money_amount": "Сумма",
        "percentage": "Процент",
        "organization": "Организация",
        "person_name": "ФИО",
        "position": "Должность",
        "address": "Адрес",
        "bin": "БИН",
        "iin": "ИИН",
        "inn": "ИНН",
        "bank_name": "Банк",
        "bik": "БИК",
        "bic_swift": "SWIFT/BIC",
        "iban": "IBAN",
        "bank_account": "Банковский счёт",
    }

    _role_labels: dict[str, str] = {
        "lender": "Займодавец",
        "borrower": "Заёмщик",
        "supplier": "Поставщик",
        "buyer": "Покупатель",
        "seller": "Продавец",
        "customer": "Заказчик",
        "contractor": "Исполнитель",
        "lessor": "Арендодатель",
        "lessee": "Арендатор",
        "guarantor": "Поручитель",
        "bank": "Банк",
        "correspondent_bank": "Банк-корреспондент",
    }

    _default_color = (75, 85, 99)

    def __init__(self, config: VisualizationConfig | None = None) -> None:
        self.config = config or VisualizationConfig()

    def render_pdf(
        self,
        source_pdf_path: str | Path,
        result_json_path: str | Path,
        output_dir: str | Path,
        include_entity_types: set[str] | None = None,
        exclude_entity_types: set[str] | None = None,
        page_numbers: set[int] | None = None,
    ) -> dict[str, Any]:
        source_pdf_path = Path(source_pdf_path)
        result_json_path = Path(result_json_path)
        output_dir = Path(output_dir)

        if not source_pdf_path.exists():
            raise FileNotFoundError(f"PDF не найден: {source_pdf_path}")
        if not result_json_path.exists():
            raise FileNotFoundError(f"JSON результата не найден: {result_json_path}")

        output_dir.mkdir(parents=True, exist_ok=True)

        payload = self._load_json(result_json_path)
        entities = self._extract_entities(payload)
        entities = self._filter_entities(
            entities,
            include_entity_types,
            exclude_entity_types,
            page_numbers,
        )

        entities_by_page: dict[int, list[VisualizationEntity]] = defaultdict(list)
        for entity in entities:
            entities_by_page[entity.page].append(entity)

        document = fitz.open(source_pdf_path)
        clean_files: list[str] = []
        review_files: list[str] = []
        clean_pdf_images: list[Image.Image] = []
        review_pdf_images: list[Image.Image] = []

        matrix = fitz.Matrix(self.config.dpi / 72.0, self.config.dpi / 72.0)

        try:
            for page_index in range(document.page_count):
                page_number = page_index + 1
                if page_numbers and page_number not in page_numbers:
                    continue

                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                source_image = Image.open(
                    io.BytesIO(pixmap.tobytes("png"))
                ).convert("RGBA")

                page_entities = sorted(
                    entities_by_page.get(page_number, []),
                    key=self._entity_sort_key,
                )

                clean_image, drawn_entities = self._render_clean_page(
                    source_image,
                    page_entities,
                    page_number,
                )

                if self.config.create_clean_images:
                    clean_path = output_dir / f"page_{page_number:03d}.clean.png"
                    clean_image.save(clean_path)
                    clean_files.append(str(clean_path))

                if self.config.create_clean_pdf:
                    clean_pdf_images.append(clean_image.convert("RGB"))

                review_image = (
                    self._create_review_page(clean_image, drawn_entities, page_number)
                    if self.config.legend_mode == "side"
                    else clean_image.copy()
                )

                if self.config.create_review_images:
                    review_path = output_dir / f"page_{page_number:03d}.review.png"
                    review_image.save(review_path)
                    review_files.append(str(review_path))

                if self.config.create_review_pdf:
                    review_pdf_images.append(review_image.convert("RGB"))

            clean_pdf_path = self._save_pdf(
                clean_pdf_images,
                output_dir / "annotated_document.clean.pdf",
            )
            review_pdf_path = self._save_pdf(
                review_pdf_images,
                output_dir / "annotated_document.review.pdf",
            )

            summary = self._build_summary(
                source_pdf_path,
                result_json_path,
                output_dir,
                document.page_count,
                entities,
                clean_files,
                review_files,
                clean_pdf_path,
                review_pdf_path,
            )

            if self.config.create_summary_json:
                (output_dir / "visualization_summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            return summary
        finally:
            document.close()

    def _render_clean_page(
        self,
        source_image: Image.Image,
        entities: list[VisualizationEntity],
        page_number: int,
    ) -> tuple[Image.Image, list[_DrawnEntity]]:
        base = source_image.copy().convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        width, height = base.size

        drawn_entities: list[_DrawnEntity] = []
        for number, entity in enumerate(entities, start=1):
            pixel_bbox = self._bbox_to_pixels(entity.bbox, width, height)
            color = self._type_colors.get(entity.entity_type, self._default_color)
            overlay_draw.rectangle(
                pixel_bbox,
                fill=(*color, self.config.fill_alpha),
                outline=(*color, 255),
                width=self.config.line_width,
            )
            drawn_entities.append(
                _DrawnEntity(number, entity, pixel_bbox, color)
            )

        result = Image.alpha_composite(base, overlay)
        draw = ImageDraw.Draw(result)
        occupied: list[tuple[int, int, int, int]] = []

        badge_font = self._load_font(self.config.badge_font_size, bold=True)
        label_font = self._load_font(self.config.font_size)

        for item in drawn_entities:
            if self.config.label_mode == "numbered":
                rect = self._place_badge(
                    draw,
                    badge_font,
                    item.number,
                    item.pixel_bbox,
                    result.size,
                    occupied,
                )
                self._draw_number_badge(
                    draw,
                    badge_font,
                    rect,
                    item.number,
                    item.color,
                )
                occupied.append(rect)

            elif self.config.label_mode in {"compact", "full"}:
                text = self._build_inline_label(
                    item.entity,
                    full=self.config.label_mode == "full",
                )
                rect = self._place_text_label(
                    draw,
                    label_font,
                    text,
                    item.pixel_bbox,
                    result.size,
                    occupied,
                )
                self._draw_text_label(draw, label_font, rect, text, item.color)
                occupied.append(rect)

        if self.config.draw_page_header:
            self._draw_page_header(result, page_number, len(entities))

        return result, drawn_entities

    def _create_review_page(
        self,
        clean_image: Image.Image,
        drawn_entities: list[_DrawnEntity],
        page_number: int,
    ) -> Image.Image:
        page_width, page_height = clean_image.size
        legend_width = max(380, int(page_width * self.config.legend_width_ratio))
        canvas = Image.new(
            "RGBA",
            (page_width + legend_width, page_height),
            (248, 250, 252, 255),
        )
        canvas.alpha_composite(clean_image, (0, 0))

        panel = Image.new(
            "RGBA",
            (legend_width, page_height),
            (248, 250, 252, 255),
        )
        draw = ImageDraw.Draw(panel)

        title_font = self._load_font(self.config.legend_title_font_size, bold=True)
        body_font = self._load_font(self.config.legend_font_size)
        body_bold = self._load_font(self.config.legend_font_size, bold=True)
        small_font = self._load_font(max(8, self.config.legend_font_size - 1))

        padding = self.config.legend_padding
        title = f"найденные сущности - страница {page_number}"
        draw.text((padding, padding), title, fill=(17, 24, 39, 255), font=title_font)
        title_box = draw.textbbox((padding, padding), title, font=title_font)
        y = title_box[3] + 14
        max_text_width = legend_width - padding * 2 - 42

        for item in drawn_entities:
            entity = item.entity
            type_label = self._type_labels.get(
                entity.entity_type,
                entity.entity_type.upper(),
            )

            lines: list[tuple[str, ImageFont.ImageFont, tuple[int, int, int, int]]] = [
                (type_label, body_bold, (17, 24, 39, 255))
            ]

            for line in self._wrap_text(
                draw,
                body_font,
                self._shorten(entity.value, self.config.legend_max_value_length),
                max_text_width,
            ):
                lines.append((line, body_font, (31, 41, 55, 255)))

            if self.config.draw_owner_in_legend and entity.owner_label:
                for line in self._wrap_text(
                    draw,
                    small_font,
                    self._translate_owner(entity),
                    max_text_width,
                ):
                    lines.append((line, small_font, (75, 85, 99, 255)))

            if self.config.draw_confidence_in_legend and entity.confidence is not None:
                lines.append(
                    (
                        f"уверенность: {entity.confidence:.3f}",
                        small_font,
                        (75, 85, 99, 255),
                    )
                )

            heights = []
            for text, font, _ in lines:
                box = draw.textbbox((0, 0), text or " ", font=font)
                heights.append(max(13, box[3] - box[1] + 3))

            item_height = max(24, sum(heights) + 6)
            if y + item_height + padding > page_height:
                draw.text(
                    (padding, y),
                    "часть списка не поместилась на страницу.",
                    fill=(185, 28, 28, 255),
                    font=small_font,
                )
                break

            badge_size = 26
            draw.rounded_rectangle(
                [padding, y, padding + badge_size, y + badge_size],
                radius=5,
                fill=(*item.color, 255),
            )
            number_text = str(item.number)
            number_box = draw.textbbox((0, 0), number_text, font=body_bold)
            draw.text(
                (
                    padding + (badge_size - (number_box[2] - number_box[0])) / 2,
                    y + (badge_size - (number_box[3] - number_box[1])) / 2 - 1,
                ),
                number_text,
                fill=(255, 255, 255, 255),
                font=body_bold,
            )

            text_x = padding + badge_size + 10
            text_y = y
            for (text, font, fill), height in zip(lines, heights):
                draw.text((text_x, text_y), text, fill=fill, font=font)
                text_y += height

            draw.line(
                [(padding, y + item_height + 3), (legend_width - padding, y + item_height + 3)],
                fill=(226, 232, 240, 255),
                width=1,
            )
            y += item_height + self.config.legend_item_gap

        canvas.alpha_composite(panel, (page_width, 0))
        canvas_draw = ImageDraw.Draw(canvas)
        canvas_draw.line(
            [(page_width, 0), (page_width, page_height)],
            fill=(203, 213, 225, 255),
            width=2,
        )
        return canvas

    def _place_badge(
        self,
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
        number: int,
        target_box: tuple[int, int, int, int],
        image_size: tuple[int, int],
        occupied: list[tuple[int, int, int, int]],
    ) -> tuple[int, int, int, int]:
        text_box = draw.textbbox((0, 0), str(number), font=font)
        width = max(18, text_box[2] - text_box[0] + 10)
        height = max(18, text_box[3] - text_box[1] + 8)
        return self._place_rectangle(
            width,
            height,
            target_box,
            image_size,
            occupied,
        )

    def _place_text_label(
        self,
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
        text: str,
        target_box: tuple[int, int, int, int],
        image_size: tuple[int, int],
        occupied: list[tuple[int, int, int, int]],
    ) -> tuple[int, int, int, int]:
        text_box = draw.textbbox((0, 0), text, font=font)
        width = min(image_size[0], text_box[2] - text_box[0] + 12)
        height = text_box[3] - text_box[1] + 8
        return self._place_rectangle(
            width,
            height,
            target_box,
            image_size,
            occupied,
        )

    def _place_rectangle(
        self,
        width: int,
        height: int,
        target_box: tuple[int, int, int, int],
        image_size: tuple[int, int],
        occupied: list[tuple[int, int, int, int]],
    ) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = target_box
        gap = 2
        starts = [
            (x1, y1 - height - gap),
            (x2 - width, y1 - height - gap),
            (x1, y2 + gap),
            (x2 - width, y2 + gap),
            (x1, y1),
            (x2 - width, y1),
            (x1 - width - gap, y1),
            (x2 + gap, y1),
        ]

        candidates = []
        for px, py in starts:
            px = max(0, min(image_size[0] - width, px))
            py = max(0, min(image_size[1] - height, py))
            candidates.append((int(px), int(py), int(px + width), int(py + height)))

        for candidate in candidates:
            if not any(self._rects_overlap(candidate, used) for used in occupied):
                return candidate

        return min(
            candidates,
            key=lambda candidate: sum(
                self._intersection_area(candidate, used)
                for used in occupied
            ),
        )

    def _draw_number_badge(
        self,
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
        rect: tuple[int, int, int, int],
        number: int,
        color: tuple[int, int, int],
    ) -> None:
        draw.rounded_rectangle(
            rect,
            radius=4,
            fill=(*color, 240),
            outline=(255, 255, 255, 255),
            width=1,
        )
        text = str(number)
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (
                rect[0] + (rect[2] - rect[0] - (box[2] - box[0])) / 2,
                rect[1] + (rect[3] - rect[1] - (box[3] - box[1])) / 2 - 1,
            ),
            text,
            fill=(255, 255, 255, 255),
            font=font,
        )

    def _draw_text_label(
        self,
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
        rect: tuple[int, int, int, int],
        text: str,
        color: tuple[int, int, int],
    ) -> None:
        draw.rounded_rectangle(rect, radius=3, fill=(*color, 235))
        draw.text(
            (rect[0] + 6, rect[1] + 4),
            text,
            fill=(255, 255, 255, 255),
            font=font,
        )

    def _draw_page_header(
        self,
        image: Image.Image,
        page_number: int,
        entity_count: int,
    ) -> None:
        draw = ImageDraw.Draw(image)
        font = self._load_font(max(9, self.config.font_size - 1), bold=True)
        text = f"Страница {page_number} · найдено: {entity_count}"
        box = draw.textbbox((0, 0), text, font=font)
        rect = (8, 8, 8 + box[2] - box[0] + 14, 8 + box[3] - box[1] + 10)
        draw.rounded_rectangle(rect, radius=4, fill=(17, 24, 39, 215))
        draw.text(
            (rect[0] + 7, rect[1] + 5),
            text,
            fill=(255, 255, 255, 255),
            font=font,
        )

    def _extract_entities(self, payload: dict[str, Any]) -> list[VisualizationEntity]:
        registry = self._extract_registry(payload)
        owner_map = self._build_owner_map(payload, registry)
        entities = []
        for item in registry.values():
            entity = self._parse_entity(
                item,
                owner_map.get(str(item.get("id"))),
            )
            if entity is not None:
                entities.append(entity)
        return entities

    def _extract_registry(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if isinstance(payload.get("entity_registry"), dict):
            result = {}
            for entity_id, value in payload["entity_registry"].items():
                if not isinstance(value, dict):
                    continue
                item = dict(value)
                item.setdefault("id", entity_id)
                if "entity_type" not in item and isinstance(item.get("type"), str):
                    item["entity_type"] = item["type"]
                result[str(item["id"])] = item
            return result

        if isinstance(payload.get("entities"), list):
            return {
                item["id"]: item
                for item in payload["entities"]
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }

        raise ValueError("ожидались поля 'entities' или 'entity_registry'")

    def _build_owner_map(
        self,
        payload: dict[str, Any],
        registry: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, str | None]]:
        result = {}
        for party in payload.get("parties", []):
            if not isinstance(party, dict):
                continue
            role = party.get("role") if isinstance(party.get("role"), str) else None
            name = self._resolve_party_organization_name(party, registry)
            for entity_id in self._collect_party_entity_ids(party):
                result[entity_id] = {"role": role, "name": name}
        return result

    def _resolve_party_organization_name(
        self,
        party: dict[str, Any],
        registry: dict[str, dict[str, Any]],
    ) -> str | None:
        organization = party.get("organization")
        if isinstance(organization, dict):
            value = organization.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()

        entity_id = party.get("organization_entity_id")
        if isinstance(entity_id, str):
            entity = registry.get(entity_id)
            if isinstance(entity, dict):
                value = entity.get("value")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _collect_party_entity_ids(self, party: dict[str, Any]) -> set[str]:
        result: set[str] = set()

        def walk(value: Any, key: str | None = None) -> None:
            if isinstance(value, dict):
                entity_id = value.get("id")
                entity_type = value.get("entity_type")
                if isinstance(entity_id, str) and isinstance(entity_type, str):
                    result.add(entity_id)
                for child_key, child_value in value.items():
                    walk(child_value, child_key)
            elif isinstance(value, list):
                if key and (
                    key.endswith("_ids")
                    or key in {
                        "candidate_ids",
                        "name_occurrence_ids",
                        "position_occurrence_ids",
                        "organization_occurrence_ids",
                    }
                ):
                    result.update(item for item in value if isinstance(item, str))
                else:
                    for item in value:
                        walk(item)
            elif isinstance(value, str) and key and key.endswith("_entity_id"):
                result.add(value)

        walk(party)
        return result

    def _parse_entity(
        self,
        item: dict[str, Any],
        owner_data: dict[str, str | None] | None,
    ) -> VisualizationEntity | None:
        entity_id = item.get("id")
        entity_type = item.get("entity_type") or item.get("type")
        page = item.get("page")
        bbox = self._parse_bbox(item.get("bbox"))
        if not isinstance(entity_id, str) or not isinstance(entity_type, str):
            return None
        if not isinstance(page, int) or bbox is None:
            return None

        value = item.get("value")
        value = value if isinstance(value, str) else "" if value is None else str(value)
        confidence = (
            float(item["confidence"])
            if isinstance(item.get("confidence"), (int, float))
            else None
        )
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

        return VisualizationEntity(
            id=entity_id,
            entity_type=entity_type,
            value=value,
            page=page,
            bbox=bbox,
            confidence=confidence,
            owner_role=owner_data.get("role") if owner_data else None,
            owner_name=owner_data.get("name") if owner_data else None,
            metadata=metadata,
        )

    @staticmethod
    def _filter_entities(
        entities: list[VisualizationEntity],
        include_entity_types: set[str] | None,
        exclude_entity_types: set[str] | None,
        page_numbers: set[int] | None,
    ) -> list[VisualizationEntity]:
        include = {value.casefold() for value in include_entity_types} if include_entity_types else None
        exclude = {value.casefold() for value in exclude_entity_types} if exclude_entity_types else set()
        return [
            entity
            for entity in entities
            if (not include or entity.entity_type.casefold() in include)
            and entity.entity_type.casefold() not in exclude
            and (not page_numbers or entity.page in page_numbers)
        ]

    def _build_inline_label(self, entity: VisualizationEntity, full: bool) -> str:
        label = self._type_labels.get(entity.entity_type, entity.entity_type.upper())
        role = self._translate_role(entity.owner_role)
        if not full:
            return f"{label} · {role}" if role else label
        parts = [label]
        if entity.value:
            parts.append(self._shorten(entity.value, 38))
        if role:
            parts.append(role)
        return " | ".join(parts)

    def _translate_owner(self, entity: VisualizationEntity) -> str:
        role = self._translate_role(entity.owner_role)
        if role and entity.owner_name:
            return f"Сторона: {role} — {entity.owner_name}"
        if role:
            return f"Сторона: {role}"
        if entity.owner_name:
            return f"Владелец: {entity.owner_name}"
        return ""

    def _translate_role(self, role: str | None) -> str | None:
        return self._role_labels.get(role, role) if role else None

    @staticmethod
    def _bbox_to_pixels(
        bbox: tuple[float, float, float, float],
        image_width: int,
        image_height: int,
    ) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = bbox
        px1 = int(round(max(0.0, min(1.0, x1)) * image_width))
        py1 = int(round(max(0.0, min(1.0, y1)) * image_height))
        px2 = int(round(max(0.0, min(1.0, x2)) * image_width))
        py2 = int(round(max(0.0, min(1.0, y2)) * image_height))
        return px1, py1, max(px1 + 1, px2), max(py1 + 1, py2)

    def _build_summary(
        self,
        source_pdf_path: Path,
        result_json_path: Path,
        output_dir: Path,
        page_count: int,
        entities: list[VisualizationEntity],
        clean_files: list[str],
        review_files: list[str],
        clean_pdf_path: str | None,
        review_pdf_path: str | None,
    ) -> dict[str, Any]:
        return {
            "source_pdf_path": str(source_pdf_path),
            "result_json_path": str(result_json_path),
            "output_dir": str(output_dir),
            "page_count": page_count,
            "entity_count": len(entities),
            "entity_type_counts": dict(Counter(entity.entity_type for entity in entities)),
            "page_entity_counts": dict(sorted(Counter(entity.page for entity in entities).items())),
            "owner_counts": dict(
                Counter(self._translate_role(entity.owner_role) or "Без стороны" for entity in entities)
            ),
            "clean_image_files": clean_files,
            "review_image_files": review_files,
            "clean_pdf_path": clean_pdf_path,
            "review_pdf_path": review_pdf_path,
            "config": {
                "dpi": self.config.dpi,
                "line_width": self.config.line_width,
                "fill_alpha": self.config.fill_alpha,
                "label_mode": self.config.label_mode,
                "legend_mode": self.config.legend_mode,
            },
            "entities": [entity.to_dict() for entity in entities],
        }

    @staticmethod
    def _wrap_text(
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
        text: str,
        max_width: int,
    ) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            box = draw.textbbox((0, 0), candidate, font=font)
            if box[2] - box[0] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    @staticmethod
    def _save_pdf(images: list[Image.Image], output_path: Path) -> str | None:
        if not images:
            return None
        images[0].save(
            output_path,
            save_all=True,
            append_images=images[1:],
            resolution=150.0,
        )
        return str(output_path)

    @staticmethod
    def _entity_sort_key(entity: VisualizationEntity) -> tuple[float, float, str, str]:
        return entity.bbox[1], entity.bbox[0], entity.entity_type, entity.id

    @staticmethod
    def _parse_bbox(bbox: Any) -> tuple[float, float, float, float] | None:
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None
        if not all(isinstance(value, (int, float)) for value in bbox):
            return None
        values = tuple(map(float, bbox))
        if not all(math.isfinite(value) for value in values):
            return None
        return values

    @staticmethod
    def _shorten(value: str, max_length: int) -> str:
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= max_length else normalized[: max_length - 1] + "…"

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
        candidates = (
            ["arialbd.ttf", "DejaVuSans-Bold.ttf", "tahomabd.ttf"]
            if bold
            else ["arial.ttf", "DejaVuSans.ttf", "tahoma.ttf"]
        )
        for name in candidates:
            try:
                return ImageFont.truetype(name, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _rects_overlap(
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> bool:
        return not (
            first[2] <= second[0]
            or second[2] <= first[0]
            or first[3] <= second[1]
            or second[3] <= first[1]
        )

    @staticmethod
    def _intersection_area(
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> int:
        x1 = max(first[0], second[0])
        y1 = max(first[1], second[1])
        x2 = min(first[2], second[2])
        y2 = min(first[3], second[3])
        return 0 if x2 <= x1 or y2 <= y1 else (x2 - x1) * (y2 - y1)