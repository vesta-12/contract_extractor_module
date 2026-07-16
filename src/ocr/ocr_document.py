from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
import os
import re
import statistics
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf
import pytesseract
from PIL import Image
from pytesseract import Output

try:
    from ocr.config import (
        DEFAULT_DPI,
        DEFAULT_LANGUAGE,
        DEFAULT_TESSERACT_CONFIG,
        DEFAULT_TIMEOUT_SECONDS,
        MAX_WORKERS,
    )
except ModuleNotFoundError:
    # Сохраняет совместимость прямого запуска этого файла.
    from config import (
        DEFAULT_DPI,
        DEFAULT_LANGUAGE,
        DEFAULT_TESSERACT_CONFIG,
        DEFAULT_TIMEOUT_SECONDS,
        MAX_WORKERS,
    )

TABLE_HEADINGS = (
    "реквизиты сторон",
    "адреса и реквизиты",
    "реквизиты и подписи",
    "банковские реквизиты",
    "подписи сторон",
)

MIN_COLUMN_WORDS = 6
COLUMN_SPLIT_RATIO = 0.5
MIN_COLUMN_GAP_RATIO = 0.035
LOW_CONFIDENCE_THRESHOLD = 0.80
VERY_LOW_CONFIDENCE_THRESHOLD = 0.60

KNOWN_TEXT_CORRECTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bзаимодавец\b", re.IGNORECASE), "займодавец"),
    (re.compile(r"\bbuk\s*/\s*swift\b", re.IGNORECASE), "бик/swift"),
    (re.compile(r"\bbik\s*/\s*swift\b", re.IGNORECASE), "бик/swift"),
    (re.compile(r"\bвик\s*/\s*swift\b", re.IGNORECASE), "бик/swift"),
)

ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"
DASHES_RE = re.compile(r"[‐‑‒–—―−]")
SPACES_RE = re.compile(r"[ \t\f\v]+")
MULTIPLE_NEWLINES_RE = re.compile(r"\n{3,}")

SPECIAL_IDENTIFIER_CHAR_MAP = str.maketrans(
    {
        "$": "S",
        "§": "S",
        "@": "A",
        "€": "E",
        "£": "L",
        "|": "I",
        "!": "I",
        "®": "R",
        "©": "C",
    }
)

CYRILLIC_TO_LATIN_LOOKALIKE = str.maketrans(
    {
        "А": "A",
        "В": "B",
        "С": "C",
        "Е": "E",
        "Н": "H",
        "І": "I",
        "К": "K",
        "М": "M",
        "О": "O",
        "Р": "P",
        "Т": "T",
        "Х": "X",
        "У": "Y",
        "З": "Z",
        "а": "A",
        "в": "B",
        "с": "C",
        "е": "E",
        "н": "H",
        "і": "I",
        "к": "K",
        "м": "M",
        "о": "O",
        "р": "P",
        "т": "T",
        "х": "X",
        "у": "Y",
        "з": "Z",
    }
)

DIGIT_CONFUSABLES = str.maketrans(
    {
        "O": "0",
        "О": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "І": "1",
        "l": "1",
        "L": "1",
        "|": "1",
        "!": "1",
        "Z": "2",
        "З": "3",
        "S": "5",
        "$": "5",
        "G": "6",
        "B": "8",
    }
)

BANK_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bin",
        re.compile(
            r"(?i)(?<![\w])(?:б\s*[иі]\s*н|b\s*i\s*n|b\s*ин)"
            r"\s*[:№#-]?\s*([a-zа-яё0-9$§@€£|!®©._/-]{8,24})"
        ),
    ),
    (
        "inn",
        re.compile(
            r"(?i)(?<![\w])(?:инн|inn)\s*[:№#-]?\s*"
            r"([a-zа-яё0-9$§@€£|!®©._/-]{8,24})"
        ),
    ),
    (
        "okpo",
        re.compile(
            r"(?i)(?<![\w])(?:окпо|okpo)\s*[:№#-]?\s*"
            r"([a-zа-яё0-9$§@€£|!®©._/-]{6,16})"
        ),
    ),
    (
        "bic_swift",
        re.compile(
            r"(?i)(?<![\w])(?:бик\s*/\s*swift|bic\s*/\s*swift|swift)"
            r"\s*[:№#-]?\s*([a-zа-яё0-9$§@€£|!®©._/-]{6,15})"
        ),
    ),
    (
        "bik",
        re.compile(
            r"(?i)(?<![\w])(?:бик|bic)\s*[:№#-]?\s*"
            r"([a-zа-яё0-9$§@€£|!®©._/-]{5,15})"
        ),
    ),
    (
        "account",
        re.compile(
            r"(?i)(?<![\w])(?:р\s*/\s*с(?:чет)?|расч[её]тный\s+сч[её]т|"
            r"сч[её]т|account|iban)\s*[:№#-]?\s*"
            r"([a-zа-яё0-9$§@€£|!®©._/-]{6,40})"
        ),
    ),
)


def configure_tesseract() -> None:
    command = os.getenv("TESSERACT_CMD")
    if command:
        pytesseract.pytesseract.tesseract_cmd = command


def normalize_unicode(value: str) -> str:
    # NFKC превращает знак № в строку No, поэтому временно защищаем его.
    numero_placeholder = "__NUMERO_SIGN__"
    value = value.replace("№", numero_placeholder)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace(numero_placeholder, "№")
    value = value.replace("\u00ad", "")
    value = value.replace("\u00a0", " ")
    for char in ZERO_WIDTH_CHARS:
        value = value.replace(char, "")
    value = DASHES_RE.sub("-", value)
    return value


def normalize_text(value: str, *, lowercase: bool = True) -> str:
    value = normalize_unicode(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(SPACES_RE.sub(" ", line).strip() for line in value.split("\n"))
    value = MULTIPLE_NEWLINES_RE.sub("\n\n", value).strip()
    value = value.replace("ё", "е").replace("Ё", "Е")
    if lowercase:
        value = value.lower()
    for pattern, replacement in KNOWN_TEXT_CORRECTIONS:
        value = pattern.sub(replacement, value)
    return value


def compact_identifier(value: str) -> str:
    value = normalize_unicode(value).translate(SPECIAL_IDENTIFIER_CHAR_MAP)
    value = value.translate(CYRILLIC_TO_LATIN_LOOKALIKE).upper()
    return re.sub(r"[^A-Z0-9]", "", value)


def normalize_digits_only(value: str) -> str:
    value = normalize_unicode(value).translate(DIGIT_CONFUSABLES)
    return re.sub(r"\D", "", value)


def iban_mod97_is_valid(value: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", value):
        return False
    rearranged = value[4:] + value[:4]
    numeric = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
    remainder = 0
    for chunk_start in range(0, len(numeric), 9):
        remainder = int(str(remainder) + numeric[chunk_start:chunk_start + 9]) % 97
    return remainder == 1


def normalize_iban(value: str) -> tuple[str, dict[str, Any], list[str]]:
    compact = compact_identifier(value)
    corrections: list[str] = []

    if len(compact) >= 2:
        prefix = compact[:2].replace("0", "O").replace("1", "I")
        if prefix in {"KZ", "K2", "K3"}:
            prefix = "KZ"
        compact = prefix + compact[2:]

    if compact.startswith("KZ"):
        chars = list(compact)
        for index in range(2, min(4, len(chars))):
            translated = chars[index].translate(DIGIT_CONFUSABLES)
            if translated != chars[index]:
                corrections.append(f"position_{index + 1}:{chars[index]}→{translated}")
            chars[index] = translated

        # В казахстанской части IBAN буквы I, O и Q не используются.
        # Поэтому их OCR-появление внутри BBAN безопаснее трактовать как цифры.
        for index in range(4, len(chars)):
            replacement = {"O": "0", "Q": "0", "I": "1"}.get(chars[index])
            if replacement:
                corrections.append(f"position_{index + 1}:{chars[index]}→{replacement}")
                chars[index] = replacement
        compact = "".join(chars)

        shape_valid = bool(re.fullmatch(r"KZ\d{2}[A-HJ-NP-Z0-9]{16}", compact))
        checksum_valid = iban_mod97_is_valid(compact) if shape_valid else False
        return compact, {
            "detected_format": "kz_iban",
            "expected_length": 20,
            "shape_valid": shape_valid,
            "checksum_valid": checksum_valid,
            "format_valid": shape_valid and checksum_valid,
        }, corrections

    shape_valid = bool(re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact))
    checksum_valid = iban_mod97_is_valid(compact) if shape_valid else False
    return compact, {
        "detected_format": "iban" if shape_valid else "generic_alphanumeric_account",
        "shape_valid": shape_valid,
        "checksum_valid": checksum_valid,
        "format_valid": shape_valid and checksum_valid,
    }, corrections


def normalize_swift_bic(value: str) -> tuple[str, dict[str, Any], list[str]]:
    normalized = compact_identifier(value)
    corrections: list[str] = []

    # Первые 4 позиции — идентификатор стороны, 5-6 — буквенный код страны.
    chars = list(normalized)
    for index in range(min(6, len(chars))):
        if chars[index].isdigit():
            replacement = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B"}.get(chars[index])
            if replacement:
                corrections.append(f"position_{index + 1}:{chars[index]}→{replacement}")
                chars[index] = replacement
    normalized = "".join(chars)

    shape_valid = bool(
        re.fullmatch(r"[A-Z0-9]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?", normalized)
    )
    return normalized, {
        "detected_format": "swift_bic",
        "expected_lengths": [8, 11],
        "shape_valid": shape_valid,
        "format_valid": shape_valid,
    }, corrections


def normalize_bank_value(entity_type: str, raw_value: str) -> tuple[str, dict[str, Any], list[str]]:
    raw_compact = compact_identifier(raw_value)
    corrections: list[str] = []

    special_replaced = normalize_unicode(raw_value).translate(SPECIAL_IDENTIFIER_CHAR_MAP)
    if special_replaced != normalize_unicode(raw_value):
        corrections.append("special_symbols_replaced")

    if entity_type == "bin":
        value = normalize_digits_only(raw_value)
        return value, {
            "detected_format": "kz_bin",
            "expected_length": 12,
            "shape_valid": len(value) == 12,
            "format_valid": len(value) == 12,
        }, corrections

    if entity_type == "inn":
        value = normalize_digits_only(raw_value)
        shape_valid = len(value) in {12, 14}
        return value, {
            "detected_format": "tax_identifier",
            "expected_lengths": [12, 14],
            "shape_valid": shape_valid,
            "format_valid": shape_valid,
        }, corrections

    if entity_type == "okpo":
        value = normalize_digits_only(raw_value)
        shape_valid = len(value) == 8
        return value, {
            "detected_format": "okpo",
            "expected_length": 8,
            "shape_valid": shape_valid,
            "format_valid": shape_valid,
        }, corrections

    if entity_type == "bic_swift":
        value, validation, extra = normalize_swift_bic(raw_value)
        return value, validation, corrections + extra

    if entity_type == "bik":
        digits = normalize_digits_only(raw_value)
        if len(digits) == 6:
            return digits, {
                "detected_format": "numeric_bik",
                "expected_length": 6,
                "shape_valid": True,
                "format_valid": True,
            }, corrections
        value, validation, extra = normalize_swift_bic(raw_value)
        return value, validation, corrections + extra

    if entity_type == "account":
        value_candidate = raw_compact
        if value_candidate.startswith(("KZ", "КЗ")) or re.match(r"^[KК][ZЗ20OО]", value_candidate):
            value, validation, extra = normalize_iban(raw_value)
            return value, validation, corrections + extra

        digits = normalize_digits_only(raw_value)
        if len(digits) == 16 and len(raw_compact) == len(digits):
            return digits, {
                "detected_format": "kg_numeric_account",
                "expected_length": 16,
                "shape_valid": True,
                "format_valid": True,
            }, corrections

        value = compact_identifier(raw_value)
        shape_valid = 6 <= len(value) <= 34
        return value, {
            "detected_format": "generic_bank_account",
            "shape_valid": shape_valid,
            "strict_format_known": False,
            "format_valid": None,
        }, corrections

    return raw_compact, {
        "detected_format": "unknown",
        "shape_valid": False,
        "format_valid": False,
    }, corrections


def merge_bboxes(bboxes: Iterable[list[int]]) -> list[int] | None:
    boxes = [box for box in bboxes if box]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def normalize_bbox(bbox: list[int] | None, page_width: int, page_height: int) -> list[float] | None:
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    return [
        round(x0 / page_width, 6),
        round(y0 / page_height, 6),
        round(x1 / page_width, 6),
        round(y1 / page_height, 6),
    ]


def render_page(page: pymupdf.Page, dpi: int) -> Image.Image:
    pixmap = page.get_pixmap(
        dpi=dpi,
        colorspace=pymupdf.csRGB,
        alpha=False,
        annots=False,
    )
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def recognize_words(
    image: Image.Image,
    page_number: int,
    language: str,
    config: str,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    data = pytesseract.image_to_data(
        image,
        lang=language,
        config=config,
        output_type=Output.DICT,
        timeout=timeout_seconds,
    )

    words: list[dict[str, Any]] = []
    page_width, page_height = image.size

    for index, raw_text_value in enumerate(data["text"]):
        raw_text = str(raw_text_value).strip()
        if not raw_text:
            continue

        try:
            confidence_raw = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence_raw = -1.0
        if confidence_raw < 0:
            continue

        x = int(data["left"][index])
        y = int(data["top"][index])
        width = int(data["width"][index])
        height = int(data["height"][index])
        bbox_px = [x, y, x + width, y + height]

        words.append(
            {
                "id": f"p{page_number}_w{len(words) + 1}",
                "text": normalize_text(raw_text),
                "raw_text": raw_text,
                "confidence": round(confidence_raw / 100, 4),
                "bbox_px": bbox_px,
                "bbox_norm": normalize_bbox(bbox_px, page_width, page_height),
                "ocr_position": {
                    "block": int(data["block_num"][index]),
                    "paragraph": int(data["par_num"][index]),
                    "line": int(data["line_num"][index]),
                    "word": int(data["word_num"][index]),
                },
            }
        )

    return words


def group_words_into_lines(
    words: list[dict[str, Any]],
    page_width: int,
    page_height: int,
    page_number: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        position = word["ocr_position"]
        grouped[(position["block"], position["paragraph"], position["line"])].append(word)

    lines: list[dict[str, Any]] = []
    for line_words in grouped.values():
        line_words.sort(key=lambda item: (item["bbox_px"][0], item["ocr_position"]["word"]))
        bbox_px = merge_bboxes(word["bbox_px"] for word in line_words)
        raw_text = " ".join(word["raw_text"] for word in line_words)
        lines.append(
            {
                "id": "",
                "text": normalize_text_with_bank_values(raw_text)[0],
                "raw_text": raw_text,
                "bbox_px": bbox_px,
                "bbox_norm": normalize_bbox(bbox_px, page_width, page_height),
                "word_ids": [word["id"] for word in line_words],
                "words": line_words,
            }
        )

    lines.sort(key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))
    for index, line in enumerate(lines, start=1):
        line["id"] = f"p{page_number}_l{index}"
    return lines


def locate_value_word_ids(
    raw_value: str,
    word_ids: Sequence[str],
    word_lookup: dict[str, dict[str, Any]],
) -> list[str]:
    target = compact_identifier(raw_value)
    if not target:
        return []

    words = [word_lookup[word_id] for word_id in word_ids if word_id in word_lookup]
    for start in range(len(words)):
        combined = ""
        selected: list[str] = []
        for word in words[start:start + 8]:
            compact_word = compact_identifier(word["raw_text"])
            if not compact_word:
                # Не привязываем значение к служебным знакам вроде №.
                continue
            combined += compact_word
            selected.append(word["id"])
            if combined == target:
                return selected
            if len(combined) > len(target) + 4:
                break
    return []


def find_bank_values(
    raw_text: str,
    *,
    page_number: int | None = None,
    source_id: str | None = None,
    source_word_ids: Sequence[str] = (),
    word_lookup: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []

    for entity_type, pattern in BANK_VALUE_PATTERNS:
        for match in pattern.finditer(raw_text):
            value_span = match.span(1)
            if any(not (value_span[1] <= start or value_span[0] >= end) for start, end in occupied):
                continue

            raw_value = match.group(1).strip(".,;:()[]{}")
            normalized_value, validation, corrections = normalize_bank_value(entity_type, raw_value)
            if not normalized_value:
                continue

            matched_word_ids: list[str] = []
            value_bbox_px = None
            source_bbox_px = None
            if word_lookup is not None and source_word_ids:
                matched_word_ids = locate_value_word_ids(raw_value, source_word_ids, word_lookup)
                if matched_word_ids:
                    value_bbox_px = merge_bboxes(word_lookup[word_id]["bbox_px"] for word_id in matched_word_ids)
                source_bbox_px = merge_bboxes(word_lookup[word_id]["bbox_px"] for word_id in source_word_ids if word_id in word_lookup)

            value_confidences = (
                [word_lookup[word_id]["confidence"] for word_id in matched_word_ids]
                if word_lookup is not None
                else []
            )
            found.append(
                {
                    "type": entity_type,
                    "raw_value": raw_value,
                    "normalized_value": normalized_value,
                    "validation": validation,
                    "correction_applied": bool(corrections) or compact_identifier(raw_value) != normalized_value,
                    "corrections": corrections,
                    "ocr_confidence": (
                        round(statistics.fmean(value_confidences), 4)
                        if value_confidences
                        else None
                    ),
                    "page": page_number,
                    "source_id": source_id,
                    "word_ids": matched_word_ids,
                    "value_bbox_px": value_bbox_px,
                    "source_bbox_px": source_bbox_px,
                }
            )
            occupied.append(value_span)

    return found


def normalize_text_with_bank_values(raw_text: str) -> tuple[str, list[dict[str, Any]]]:
    values = find_bank_values(raw_text)
    replacements: list[tuple[int, int, str]] = []

    for entity_type, pattern in BANK_VALUE_PATTERNS:
        for match in pattern.finditer(raw_text):
            raw_value = match.group(1).strip(".,;:()[]{}")
            normalized_value, _, _ = normalize_bank_value(entity_type, raw_value)
            if normalized_value:
                start, end = match.span(1)
                replacements.append((start, end, normalized_value.lower()))

    updated = raw_text
    for start, end, replacement in sorted(replacements, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    return normalize_text(updated), values


def line_to_item(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": line["id"],
        "source_line_id": line["id"],
        "text": line["text"],
        "raw_text": line["raw_text"],
        "bbox_px": line["bbox_px"],
        "bbox_norm": line["bbox_norm"],
        "word_ids": list(line["word_ids"]),
    }


def make_fragment(
    words: list[dict[str, Any]],
    source_line_id: str,
    fragment_id: str,
    page_width: int,
    page_height: int,
) -> dict[str, Any]:
    words = sorted(words, key=lambda word: word["bbox_px"][0])
    bbox_px = merge_bboxes(word["bbox_px"] for word in words)
    raw_text = " ".join(word["raw_text"] for word in words)
    normalized_text, _ = normalize_text_with_bank_values(raw_text)
    return {
        "id": fragment_id,
        "source_line_id": source_line_id,
        "text": normalized_text,
        "raw_text": raw_text,
        "bbox_px": bbox_px,
        "bbox_norm": normalize_bbox(bbox_px, page_width, page_height),
        "word_ids": [word["id"] for word in words],
    }


def make_region(
    region_id: str,
    region_type: str,
    items: list[dict[str, Any]],
    page_width: int,
    page_height: int,
) -> dict[str, Any]:
    bbox_px = merge_bboxes(item["bbox_px"] for item in items if item.get("bbox_px") is not None)
    return {
        "id": region_id,
        "type": region_type,
        "text": "\n".join(item["text"] for item in items if item["text"].strip()),
        "raw_text": "\n".join(item["raw_text"] for item in items if item["raw_text"].strip()),
        "bbox_px": bbox_px,
        "bbox_norm": normalize_bbox(bbox_px, page_width, page_height),
        "word_ids": [word_id for item in items for word_id in item["word_ids"]],
        "item_ids": [item["id"] for item in items],
        "items": items,
    }


def find_table_heading_index(lines: list[dict[str, Any]]) -> int | None:
    for index, line in enumerate(lines):
        normalized = normalize_text(line["raw_text"])
        if any(heading in normalized for heading in TABLE_HEADINGS):
            return index
    return None


def split_line_by_column(
    line: dict[str, Any],
    page_width: int,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    midpoint = page_width * COLUMN_SPLIT_RATIO
    left_words: list[dict[str, Any]] = []
    right_words: list[dict[str, Any]] = []

    for word in line["words"]:
        x0, _, x1, _ = word["bbox_px"]
        if (x0 + x1) / 2 < midpoint:
            left_words.append(word)
        else:
            right_words.append(word)

    if left_words and right_words:
        left_right_edge = max(word["bbox_px"][2] for word in left_words)
        right_left_edge = min(word["bbox_px"][0] for word in right_words)
        if right_left_edge - left_right_edge >= page_width * MIN_COLUMN_GAP_RATIO:
            return "split", left_words, right_words
        return "full_width", line["words"], []
    if left_words:
        return "left", left_words, []
    if right_words:
        return "right", [], right_words
    return "empty", [], []


def build_standard_layout(
    lines: list[dict[str, Any]],
    page_width: int,
    page_height: int,
    page_number: int,
) -> tuple[str, str, list[dict[str, Any]], str]:
    items = [line_to_item(line) for line in lines]
    region = make_region(f"p{page_number}_r1", "body", items, page_width, page_height)
    return region["text"], region["raw_text"], [region], "standard"


def try_build_two_column_layout(
    lines: list[dict[str, Any]],
    page_width: int,
    page_height: int,
    page_number: int,
) -> tuple[str, str, list[dict[str, Any]], str] | None:
    heading_index = find_table_heading_index(lines)
    if heading_index is None:
        return None

    prefix_items = [line_to_item(line) for line in lines[:heading_index + 1]]
    lines_after_heading = lines[heading_index + 1:]
    left_items: list[dict[str, Any]] = []
    right_items: list[dict[str, Any]] = []
    suffix_items: list[dict[str, Any]] = []
    left_word_count = 0
    right_word_count = 0
    fragment_counter = 0

    for current_index, line in enumerate(lines_after_heading):
        mode, left_words, right_words = split_line_by_column(line, page_width)
        columns_are_established = left_word_count >= MIN_COLUMN_WORDS and right_word_count >= MIN_COLUMN_WORDS

        if mode == "full_width":
            if columns_are_established:
                suffix_items = [line_to_item(item) for item in lines_after_heading[current_index:]]
                break
            prefix_items.append(line_to_item(line))
            continue

        if mode in {"left", "split"} and left_words:
            fragment_counter += 1
            left_items.append(
                make_fragment(
                    left_words,
                    line["id"],
                    f"p{page_number}_left_{fragment_counter}",
                    page_width,
                    page_height,
                )
            )
            left_word_count += len(left_words)

        if mode in {"right", "split"} and right_words:
            fragment_counter += 1
            right_items.append(
                make_fragment(
                    right_words,
                    line["id"],
                    f"p{page_number}_right_{fragment_counter}",
                    page_width,
                    page_height,
                )
            )
            right_word_count += len(right_words)

    if left_word_count < MIN_COLUMN_WORDS or right_word_count < MIN_COLUMN_WORDS:
        return None

    left_items.sort(key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))
    right_items.sort(key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))

    regions = [
        make_region(f"p{page_number}_r1", "before_columns", prefix_items, page_width, page_height),
        make_region(f"p{page_number}_r2", "left_column", left_items, page_width, page_height),
        make_region(f"p{page_number}_r3", "right_column", right_items, page_width, page_height),
    ]
    if suffix_items:
        regions.append(make_region(f"p{page_number}_r4", "after_columns", suffix_items, page_width, page_height))

    normalized_text = "\n\n".join(region["text"] for region in regions if region["text"].strip())
    raw_text = "\n\n".join(region["raw_text"] for region in regions if region["raw_text"].strip())
    return normalized_text, raw_text, regions, "left_column_then_right_column"


def build_page_layout(
    lines: list[dict[str, Any]],
    page_width: int,
    page_height: int,
    page_number: int,
) -> tuple[str, str, list[dict[str, Any]], str]:
    two_column = try_build_two_column_layout(lines, page_width, page_height, page_number)
    if two_column is not None:
        return two_column
    return build_standard_layout(lines, page_width, page_height, page_number)


def extract_page_values(
    page_number: int,
    regions: list[dict[str, Any]],
    words: list[dict[str, Any]],
    page_width: int,
    page_height: int,
) -> list[dict[str, Any]]:
    word_lookup = {word["id"]: word for word in words}
    values: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for region in regions:
        for item in region["items"]:
            for value in find_bank_values(
                item["raw_text"],
                page_number=page_number,
                source_id=item["id"],
                source_word_ids=item["word_ids"],
                word_lookup=word_lookup,
            ):
                key = (value["type"], value["normalized_value"], item["id"])
                if key in seen:
                    continue
                seen.add(key)
                value["value_bbox_norm"] = normalize_bbox(value["value_bbox_px"], page_width, page_height)
                value["source_bbox_norm"] = normalize_bbox(value["source_bbox_px"], page_width, page_height)
                values.append(value)
    return values


def synchronize_normalized_values_with_words(
    words: list[dict[str, Any]],
    normalized_values: list[dict[str, Any]],
) -> None:
    """Связывает контекстно исправленные значения с исходными OCR-словами."""
    word_lookup = {word["id"]: word for word in words}
    for local_value_index, value in enumerate(normalized_values):
        matched_ids = value.get("word_ids") or []
        for position, word_id in enumerate(matched_ids):
            word = word_lookup.get(word_id)
            if word is None:
                continue
            word.setdefault("value_refs_local", []).append(local_value_index)
            if position == 0:
                word["contextual_normalized_text"] = normalize_text(
                    value["normalized_value"]
                )


def calculate_quality(
    words: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    normalized_values: list[dict[str, Any]],
) -> dict[str, Any]:
    confidences = [float(word["confidence"]) for word in words]
    if confidences:
        average = statistics.fmean(confidences)
        median = statistics.median(confidences)
        minimum = min(confidences)
    else:
        average = median = minimum = 0.0

    low = [word for word in words if word["confidence"] < LOW_CONFIDENCE_THRESHOLD]
    very_low = [word for word in words if word["confidence"] < VERY_LOW_CONFIDENCE_THRESHOLD]
    suspicious_symbols = sum(
        1
        for word in words
        if re.search(r"[$§@€£|!®©]", word["raw_text"])
    )
    invalid_values = [
        value
        for value in normalized_values
        if value["validation"].get("format_valid") is False
    ]
    unverified_values = [
        value
        for value in normalized_values
        if value["validation"].get("format_valid") is None
    ]
    corrections_count = sum(len(value["corrections"]) for value in normalized_values)

    if average >= 0.95:
        grade = "excellent"
    elif average >= 0.90:
        grade = "good"
    elif average >= 0.80:
        grade = "review"
    else:
        grade = "poor"

    return {
        "grade": grade,
        "word_count": len(words),
        "line_count": len(lines),
        "average_confidence": round(average, 4),
        "median_confidence": round(median, 4),
        "minimum_confidence": round(minimum, 4),
        "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
        "low_confidence_word_count": len(low),
        "very_low_confidence_threshold": VERY_LOW_CONFIDENCE_THRESHOLD,
        "very_low_confidence_word_count": len(very_low),
        "low_confidence_examples": [
            {
                "id": word["id"],
                "raw_text": word["raw_text"],
                "confidence": word["confidence"],
                "bbox_px": word["bbox_px"],
            }
            for word in sorted(low, key=lambda item: item["confidence"])[:20]
        ],
        "suspicious_symbol_word_count": suspicious_symbols,
        "normalized_value_count": len(normalized_values),
        "normalization_correction_count": corrections_count,
        "invalid_normalized_value_count": len(invalid_values),
        "unverified_normalized_value_count": len(unverified_values),
        "invalid_normalized_values": [
            {
                "type": value["type"],
                "raw_value": value["raw_value"],
                "normalized_value": value["normalized_value"],
                "validation": value["validation"],
            }
            for value in invalid_values
        ],
    }


def aggregate_quality(pages: list[dict[str, Any]]) -> dict[str, Any]:
    word_count = sum(page["quality"]["word_count"] for page in pages)
    line_count = sum(page["quality"]["line_count"] for page in pages)
    weighted_confidence = (
        sum(page["quality"]["average_confidence"] * page["quality"]["word_count"] for page in pages)
        / word_count
        if word_count
        else 0.0
    )
    return {
        "page_count": len(pages),
        "word_count": word_count,
        "line_count": line_count,
        "weighted_average_confidence": round(weighted_confidence, 4),
        "low_confidence_word_count": sum(page["quality"]["low_confidence_word_count"] for page in pages),
        "very_low_confidence_word_count": sum(page["quality"]["very_low_confidence_word_count"] for page in pages),
        "suspicious_symbol_word_count": sum(page["quality"]["suspicious_symbol_word_count"] for page in pages),
        "normalized_value_count": sum(page["quality"]["normalized_value_count"] for page in pages),
        "normalization_correction_count": sum(page["quality"]["normalization_correction_count"] for page in pages),
        "invalid_normalized_value_count": sum(page["quality"]["invalid_normalized_value_count"] for page in pages),
        "unverified_normalized_value_count": sum(page["quality"]["unverified_normalized_value_count"] for page in pages),
    }


def process_page_worker(task: tuple[str, int, int, str, str, int]) -> dict[str, Any]:
    input_path_value, page_index, dpi, language, config, timeout_seconds = task
    configure_tesseract()

    # Один экземпляр Tesseract на worker. Это не дает каждому процессу
    # дополнительно развернуть несколько OpenMP-потоков и перегрузить CPU.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    page_started = time.perf_counter()
    page_number = page_index + 1

    with pymupdf.open(input_path_value) as document:
        page = document.load_page(page_index)
        pdf_width = round(page.rect.width, 3)
        pdf_height = round(page.rect.height, 3)

        stage_started = time.perf_counter()
        image = render_page(page, dpi)
        render_seconds = time.perf_counter() - stage_started

    try:
        page_width, page_height = image.size
        stage_started = time.perf_counter()
        words = recognize_words(
            image,
            page_number,
            language,
            config,
            timeout_seconds,
        )
        ocr_seconds = time.perf_counter() - stage_started
    finally:
        image.close()

    stage_started = time.perf_counter()
    lines = group_words_into_lines(words, page_width, page_height, page_number)
    line_build_seconds = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    page_text, page_raw_text, regions, reading_order = build_page_layout(
        lines,
        page_width,
        page_height,
        page_number,
    )
    layout_seconds = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    normalized_values = extract_page_values(
        page_number,
        regions,
        words,
        page_width,
        page_height,
    )
    synchronize_normalized_values_with_words(words, normalized_values)
    quality = calculate_quality(words, lines, normalized_values)
    normalization_and_quality_seconds = time.perf_counter() - stage_started

    lines_output = [
        {key: value for key, value in line.items() if key != "words"}
        for line in lines
    ]

    return {
        "page_number": page_number,
        "text": page_text,
        "raw_text": page_raw_text,
        "reading_order": reading_order,
        "image_size_px": [page_width, page_height],
        "pdf_size_points": [pdf_width, pdf_height],
        "quality": quality,
        "processing": {
            "render_seconds": round(render_seconds, 4),
            "ocr_seconds": round(ocr_seconds, 4),
            "line_build_seconds": round(line_build_seconds, 4),
            "layout_seconds": round(layout_seconds, 4),
            "normalization_and_quality_seconds": round(
                normalization_and_quality_seconds,
                4,
            ),
            "total_seconds": round(time.perf_counter() - page_started, 4),
        },
        "normalized_values": normalized_values,
        "regions": regions,
        "lines": lines_output,
        "words": words,
    }


def raw_token_needs_preserving(word: dict[str, Any]) -> bool:
    raw = word["raw_text"]
    simple_raw = normalize_unicode(raw)
    simple_raw = SPACES_RE.sub(" ", simple_raw).strip()
    simple_raw = simple_raw.replace("ё", "е").replace("Ё", "Е").lower()
    return bool(
        simple_raw != word["text"]
        or word["confidence"] < LOW_CONFIDENCE_THRESHOLD
        or re.search(r"[$§@€£|!®©]", raw)
        or word.get("contextual_normalized_text")
    )


def validation_status(validation: dict[str, Any]) -> str:
    format_valid = validation.get("format_valid")
    if format_valid is True:
        return "valid"
    if format_valid is False:
        return "invalid"
    return "unverified"


def compact_validation(validation: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "format": validation.get("detected_format", "unknown"),
        "status": validation_status(validation),
    }
    if "shape_valid" in validation:
        compact["shape"] = validation["shape_valid"]
    if "checksum_valid" in validation:
        compact["checksum"] = validation["checksum_valid"]
    if validation.get("strict_format_known") is False:
        compact["strict"] = False
    return compact


def build_compact_page_structure(
    page: dict[str, Any],
    global_value_indices: dict[int, int],
) -> tuple[dict[str, Any], dict[str, int]]:
    word_lookup = {word["id"]: word for word in page["words"]}
    ordered_word_ids: list[str] = []
    seen: set[str] = set()
    compact_regions: list[dict[str, Any]] = []

    for region in page["regions"]:
        region_ids = [
            word_id
            for word_id in region["word_ids"]
            if word_id in word_lookup and word_id not in seen
        ]
        start = len(ordered_word_ids)
        ordered_word_ids.extend(region_ids)
        seen.update(region_ids)
        end = len(ordered_word_ids)
        compact_regions.append(
            {
                "type": region["type"],
                "words": [start, end],
            }
        )

    # Защитный fallback: сохраняем слова, которые по необычной верстке
    # не попали ни в один регион.
    for word in page["words"]:
        if word["id"] not in seen:
            ordered_word_ids.append(word["id"])
            seen.add(word["id"])

    word_index_map = {
        word_id: index
        for index, word_id in enumerate(ordered_word_ids)
    }

    value_refs_by_word: dict[str, list[int]] = defaultdict(list)
    for local_index, value in enumerate(page["normalized_values"]):
        global_index = global_value_indices[local_index]
        for word_id in value.get("word_ids") or []:
            value_refs_by_word[word_id].append(global_index)

    compact_words: list[dict[str, Any]] = []
    for word_id in ordered_word_ids:
        word = word_lookup[word_id]
        output_word: dict[str, Any] = {
            "t": word["text"],
            "c": round(float(word["confidence"]), 3),
            "b": [round(float(value), 6) for value in word["bbox_norm"]],
        }
        if raw_token_needs_preserving(word):
            output_word["r"] = word["raw_text"]
        contextual = word.get("contextual_normalized_text")
        if contextual and contextual != word["text"]:
            output_word["n"] = contextual
        refs = value_refs_by_word.get(word_id)
        if refs:
            output_word["v"] = refs
        compact_words.append(output_word)

    quality = page["quality"]
    compact_page = {
        "page": page["page_number"],
        "text_span": [0, 0],
        "order": (
            "left_then_right"
            if page["reading_order"] == "left_column_then_right_column"
            else "standard"
        ),
        "pdf_size": page["pdf_size_points"],
        "quality": {
            "grade": quality["grade"],
            "avg": quality["average_confidence"],
            "min": quality["minimum_confidence"],
            "words": quality["word_count"],
            "low": quality["low_confidence_word_count"],
            "very_low": quality["very_low_confidence_word_count"],
            "suspicious": quality["suspicious_symbol_word_count"],
        },
        "timing": {
            "render": page["processing"]["render_seconds"],
            "ocr": page["processing"]["ocr_seconds"],
            "total": page["processing"]["total_seconds"],
        },
        "regions": compact_regions,
        "words": compact_words,
    }
    return compact_page, word_index_map


def compact_normalized_value(
    value: dict[str, Any],
    word_index_map: dict[str, int],
    region_type: str | None,
) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "type": value["type"],
        "value": value["normalized_value"],
        "validation": compact_validation(value["validation"]),
        "page": value["page"],
        "bbox": value.get("value_bbox_norm"),
        "words": [
            word_index_map[word_id]
            for word_id in value.get("word_ids") or []
            if word_id in word_index_map
        ],
    }
    if value["raw_value"] != value["normalized_value"]:
        compact["raw"] = value["raw_value"]
    if value.get("source_bbox_norm"):
        compact["source_bbox"] = value["source_bbox_norm"]
    if value.get("ocr_confidence") is not None:
        compact["confidence"] = value["ocr_confidence"]
    if value.get("corrections"):
        compact["corrections"] = value["corrections"]
    if region_type:
        compact["region"] = region_type
    return compact


def compact_document_result(
    pages: list[dict[str, Any]],
    *,
    input_path: Path,
    tesseract_version: str,
    language: str,
    dpi: int,
    worker_count: int,
    setup_seconds: float,
    pages_wall_seconds: float,
    pipeline_started: float,
) -> dict[str, Any]:
    document_parts = [page["text"] for page in pages]
    document_text = "\n\n".join(document_parts)

    # Назначаем стабильные глобальные индексы значениям.
    global_values_verbose: list[dict[str, Any]] = []
    page_value_global_indices: dict[int, dict[int, int]] = {}
    for page in pages:
        local_to_global: dict[int, int] = {}
        for local_index, value in enumerate(page["normalized_values"]):
            global_index = len(global_values_verbose)
            local_to_global[local_index] = global_index
            global_values_verbose.append(value)
        page_value_global_indices[page["page_number"]] = local_to_global

    compact_pages: list[dict[str, Any]] = []
    page_word_maps: dict[int, dict[str, int]] = {}
    cursor = 0
    for index, page in enumerate(pages):
        compact_page, word_map = build_compact_page_structure(
            page,
            page_value_global_indices[page["page_number"]],
        )
        start = cursor
        end = start + len(page["text"])
        compact_page["text_span"] = [start, end]
        compact_pages.append(compact_page)
        page_word_maps[page["page_number"]] = word_map
        cursor = end + (2 if index < len(pages) - 1 else 0)

    compact_values: list[dict[str, Any]] = []
    for value in global_values_verbose:
        page = next(item for item in pages if item["page_number"] == value["page"])
        region_type = None
        for region in page["regions"]:
            if any(
                item["id"] == value.get("source_id")
                for item in region.get("items", [])
            ):
                region_type = region["type"]
                break
        compact_values.append(
            compact_normalized_value(
                value,
                page_word_maps[value["page"]],
                region_type,
            )
        )

    aggregate = aggregate_quality(pages)
    result: dict[str, Any] = {
        # Первое поле оставляем текстом документа, как требовалось изначально.
        "document_text": document_text,
        "meta": {
            "schema": "3.0-production",
            "file": input_path.name,
            "pages": len(pages),
            "engine": "tesseract",
            "engine_version": tesseract_version,
            "lang": language,
            "dpi": dpi,
            "workers": worker_count,
            "coordinates": "normalized_xyxy",
        },
        "quality": {
            "avg": aggregate["weighted_average_confidence"],
            "words": aggregate["word_count"],
            "lines": aggregate["line_count"],
            "low": aggregate["low_confidence_word_count"],
            "very_low": aggregate["very_low_confidence_word_count"],
            "suspicious": aggregate["suspicious_symbol_word_count"],
            "values": aggregate["normalized_value_count"],
            "corrected": aggregate["normalization_correction_count"],
            "invalid": aggregate["invalid_normalized_value_count"],
            "unverified": aggregate["unverified_normalized_value_count"],
        },
        "timing": {
            "setup": round(setup_seconds, 4),
            "pages_wall": round(pages_wall_seconds, 4),
            "page_time_sum": round(
                sum(page["processing"]["total_seconds"] for page in pages),
                4,
            ),
            "ocr_sum": round(
                sum(page["processing"]["ocr_seconds"] for page in pages),
                4,
            ),
            "serialize": None,
            "write": None,
            "total": round(time.perf_counter() - pipeline_started, 4),
        },
        "values": compact_values,
        "pages": compact_pages,
    }
    return result


def process_pdf(
    input_path: Path,
    output_path: Path,
    dpi: int,
    language: str,
    timeout_seconds: int,
    requested_workers: int = MAX_WORKERS,
    pretty: bool = False,
) -> dict[str, Any]:
    pipeline_started = time.perf_counter()
    if not input_path.exists():
        raise FileNotFoundError(f"PDF не найден: {input_path}")

    setup_started = time.perf_counter()
    configure_tesseract()
    tesseract_version = str(pytesseract.get_tesseract_version())
    available_languages = pytesseract.get_languages(config="")
    missing_languages = [
        item
        for item in language.split("+")
        if item not in available_languages
    ]
    if missing_languages:
        raise RuntimeError(
            "Не установлены языки Tesseract: " + ", ".join(missing_languages)
        )

    with pymupdf.open(input_path) as document:
        page_count = document.page_count
    if page_count == 0:
        raise ValueError("PDF не содержит страниц")

    worker_count = min(
        MAX_WORKERS,
        max(1, requested_workers),
        page_count,
    )
    setup_seconds = time.perf_counter() - setup_started

    tasks = [
        (
            str(input_path.resolve()),
            page_index,
            dpi,
            language,
            DEFAULT_TESSERACT_CONFIG,
            timeout_seconds,
        )
        for page_index in range(page_count)
    ]

    pages_started = time.perf_counter()
    if worker_count == 1:
        pages_result = [process_page_worker(task) for task in tasks]
    else:
        pages_result = []
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            future_to_page = {
                executor.submit(process_page_worker, task): task[1]
                for task in tasks
            }
            for future in as_completed(future_to_page):
                page_index = future_to_page[future]
                try:
                    pages_result.append(future.result())
                except Exception as error:
                    raise RuntimeError(
                        f"Ошибка OCR на странице {page_index + 1}: {error}"
                    ) from error
    pages_wall_seconds = time.perf_counter() - pages_started
    pages_result.sort(key=lambda page: page["page_number"])

    result = compact_document_result(
        pages_result,
        input_path=input_path,
        tesseract_version=tesseract_version,
        language=language,
        dpi=dpi,
        worker_count=worker_count,
        setup_seconds=setup_seconds,
        pages_wall_seconds=pages_wall_seconds,
        pipeline_started=pipeline_started,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    dump_kwargs: dict[str, Any] = {
        "ensure_ascii": False,
    }
    if pretty:
        dump_kwargs["indent"] = 2
    else:
        dump_kwargs["separators"] = (",", ":")

    serialization_started = time.perf_counter()
    preliminary_payload = json.dumps(result, **dump_kwargs)
    serialization_seconds = time.perf_counter() - serialization_started

    benchmark_path = output_path.with_suffix(output_path.suffix + ".tmp")
    write_started = time.perf_counter()
    benchmark_path.write_text(preliminary_payload, encoding="utf-8")
    write_seconds = time.perf_counter() - write_started
    benchmark_path.unlink(missing_ok=True)

    result["timing"]["serialize"] = round(serialization_seconds, 4)
    result["timing"]["write"] = round(write_seconds, 4)
    result["timing"]["total"] = round(
        time.perf_counter() - pipeline_started,
        4,
    )

    output_path.write_text(
        json.dumps(result, **dump_kwargs),
        encoding="utf-8",
    )
    return result

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Локальное OCR PDF: компактный production JSON, "
            "координаты, нормализация и до 4 параллельных worker-процессов"
        )
    )
    parser.add_argument("input_pdf", type=Path, help="Путь к исходному PDF")
    parser.add_argument("output_json", type=Path, help="Путь к итоговому JSON")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--lang", default=DEFAULT_LANGUAGE)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help="Максимум параллельных worker-процессов; фактический предел всегда 4",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Записать JSON с отступами. По умолчанию используется компактный production-формат",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    result = process_pdf(
        input_path=arguments.input_pdf,
        output_path=arguments.output_json,
        dpi=arguments.dpi,
        language=arguments.lang,
        timeout_seconds=arguments.timeout,
        requested_workers=arguments.workers,
        pretty=arguments.pretty,
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "source_file": result["meta"]["file"],
                "page_count": result["meta"]["pages"],
                "workers": result["meta"]["workers"],
                "quality": result["quality"],
                "timing": result["timing"],
                "output_json": str(arguments.output_json),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
