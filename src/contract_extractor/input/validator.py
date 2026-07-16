from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any
from contract_extractor.exceptions import OCRSchemaError

def validate_ocr_payload(payload: Any) -> None:

    errors: list[str] = []

    if not isinstance(payload, Mapping):
        raise OCRSchemaError(
            [
                "корневой элемент JSON должен быть объектом."
            ]
        )

    document_text = payload.get("document_text")

    if not isinstance(document_text, str):
        errors.append(
            "поле 'document_text' должно быть строкой."
        )

    pages = payload.get("pages")

    if not isinstance(pages, list) or not pages:
        errors.append(
            "поле 'pages' должно быть непустым массивом."
        )
        pages = []

    values = payload.get("values", [])

    if not isinstance(values, list):
        errors.append(
            "поле 'values' должно быть массивом, "
            "если оно присутствует."
        )
        values = []

    page_word_counts: dict[int, int] = {}
    page_numbers: list[int] = []

    for page_position, page in enumerate(pages):
        page_path = f"pages[{page_position}]"

        if not isinstance(page, Mapping):
            errors.append(
                f"{page_path} должен быть объектом."
            )
            continue

        page_number = page.get("page")

        if (
            not _is_int(page_number)
            or page_number < 1
        ):
            errors.append(
                f"{page_path}.page должен быть "
                "целым числом >= 1."
            )
            continue

        page_numbers.append(page_number)

        words = page.get("words")

        if not isinstance(words, list):
            errors.append(
                f"{page_path}.words должен быть массивом."
            )
            words = []

        page_word_counts[page_number] = len(words)

        for word_position, word in enumerate(words):
            _validate_word(
                word=word,
                path=f"{page_path}.words[{word_position}]",
                value_count=len(values),
                errors=errors,
            )

        regions = page.get("regions", [])

        if not isinstance(regions, list):
            errors.append(
                f"{page_path}.regions должен быть массивом."
            )
        else:
            for region_position, region in enumerate(
                regions
            ):
                _validate_region(
                    region=region,
                    path=(
                        f"{page_path}.regions"
                        f"[{region_position}]"
                    ),
                    word_count=len(words),
                    errors=errors,
                )

        _validate_optional_pair(
            value=page.get("text_span"),
            path=f"{page_path}.text_span",
            errors=errors,
            integer=True,
        )

        _validate_optional_pair(
            value=page.get("pdf_size"),
            path=f"{page_path}.pdf_size",
            errors=errors,
            integer=False,
        )

        _validate_optional_mapping(
            value=page.get("quality"),
            path=f"{page_path}.quality",
            errors=errors,
        )

        _validate_optional_mapping(
            value=page.get("timing"),
            path=f"{page_path}.timing",
            errors=errors,
        )

    expected_page_numbers = list(
        range(1, len(pages) + 1)
    )

    if (
        page_numbers
        and page_numbers != expected_page_numbers
    ):
        errors.append(
            "страницы должны идти последовательно, "
            "начиная с 1: "
            f"получено {page_numbers}, "
            f"ожидалось {expected_page_numbers}."
        )

    for value_position, value in enumerate(values):
        _validate_value(
            value=value,
            path=f"values[{value_position}]",
            page_word_counts=page_word_counts,
            errors=errors,
        )

    for key in ("meta", "quality", "timing"):
        _validate_optional_mapping(
            value=payload.get(key),
            path=key,
            errors=errors,
        )

    if errors:
        raise OCRSchemaError(errors)


def _validate_word(
    word: Any,
    path: str,
    value_count: int,
    errors: list[str],
) -> None:
    if not isinstance(word, Mapping):
        errors.append(
            f"{path} должен быть объектом."
        )
        return

    text = word.get("t")

    if (
        not isinstance(text, str)
        or not text.strip()
    ):
        errors.append(
            f"{path}.t должен быть непустой строкой."
        )

    confidence = word.get("c")

    if (
        not _is_number(confidence)
        or not 0.0 <= float(confidence) <= 1.0
    ):
        errors.append(
            f"{path}.c должен быть числом "
            "от 0.0 до 1.0."
        )

    _validate_bbox(
        value=word.get("b"),
        path=f"{path}.b",
        errors=errors,
    )

    for key in ("r", "n"):
        value = word.get(key)

        if (
            value is not None
            and not isinstance(value, str)
        ):
            errors.append(
                f"{path}.{key} должен быть строкой."
            )

    value_refs = word.get("v", [])

    if not isinstance(value_refs, list):
        errors.append(
            f"{path}.v должен быть массивом индексов."
        )
        return

    for ref_position, value_ref in enumerate(
        value_refs
    ):
        ref_path = f"{path}.v[{ref_position}]"

        if not _is_int(value_ref):
            errors.append(
                f"{ref_path} должен быть целым числом."
            )
            continue

        if value_ref < 0 or value_ref >= value_count:
            errors.append(
                f"{ref_path} ссылается на отсутствующий "
                f"элемент values[{value_ref}]."
            )


def _validate_region(
    region: Any,
    path: str,
    word_count: int,
    errors: list[str],
) -> None:
    if not isinstance(region, Mapping):
        errors.append(
            f"{path} должен быть объектом."
        )
        return

    region_type = region.get("type")

    if (
        not isinstance(region_type, str)
        or not region_type.strip()
    ):
        errors.append(
            f"{path}.type должен быть непустой строкой."
        )

    word_range = region.get("words")

    if (
        not _is_pair(word_range)
        or not all(
            _is_int(value)
            for value in word_range
        )
    ):
        errors.append(
            f"{path}.words должен содержать "
            "два целых индекса."
        )
        return

    word_start, word_end = word_range

    if (
        word_start < 0
        or word_end < word_start
        or word_end > word_count
    ):
        errors.append(
            f"{path}.words содержит некорректный "
            f"диапазон [{word_start}, {word_end}] "
            f"для страницы из {word_count} слов."
        )

    region_bbox = region.get("bbox")

    if region_bbox is not None:
        _validate_bbox(
            value=region_bbox,
            path=f"{path}.bbox",
            errors=errors,
        )


def _validate_value(
    value: Any,
    path: str,
    page_word_counts: dict[int, int],
    errors: list[str],
) -> None:
    if not isinstance(value, Mapping):
        errors.append(
            f"{path} должен быть объектом."
        )
        return

    for key in ("type", "value"):
        item = value.get(key)

        if (
            not isinstance(item, str)
            or not item.strip()
        ):
            errors.append(
                f"{path}.{key} должен быть "
                "непустой строкой."
            )

    page_number = value.get("page")

    if (
        not _is_int(page_number)
        or page_number not in page_word_counts
    ):
        errors.append(
            f"{path}.page ссылается "
            "на отсутствующую страницу."
        )
        page_word_count: int | None = None
    else:
        page_word_count = page_word_counts[
            page_number
        ]

    _validate_bbox(
        value=value.get("bbox"),
        path=f"{path}.bbox",
        errors=errors,
    )

    source_bbox = value.get("source_bbox")

    if source_bbox is not None:
        _validate_bbox(
            value=source_bbox,
            path=f"{path}.source_bbox",
            errors=errors,
        )

    word_indices = value.get("words")

    if not isinstance(word_indices, list):
        errors.append(
            f"{path}.words должен быть массивом "
            "индексов слов."
        )
    else:
        for word_position, word_index in enumerate(
            word_indices
        ):
            word_path = (
                f"{path}.words[{word_position}]"
            )

            if not _is_int(word_index):
                errors.append(
                    f"{word_path} должен быть "
                    "целым числом."
                )
                continue

            if (
                page_word_count is not None
                and not (
                    0 <= word_index < page_word_count
                )
            ):
                errors.append(
                    f"{word_path} ссылается "
                    f"на отсутствующее слово "
                    f"{word_index} страницы "
                    f"{page_number}."
                )

    confidence = value.get("confidence")

    if (
        confidence is not None
        and (
            not _is_number(confidence)
            or not (
                0.0
                <= float(confidence)
                <= 1.0
            )
        )
    ):
        errors.append(
            f"{path}.confidence должен быть "
            "числом от 0.0 до 1.0."
        )

    _validate_optional_mapping(
        value=value.get("validation"),
        path=f"{path}.validation",
        errors=errors,
    )

    corrections = value.get("corrections")

    if corrections is not None:
        corrections_are_valid = (
            isinstance(corrections, list)
            and all(
                isinstance(item, str)
                for item in corrections
            )
        )

        if not corrections_are_valid:
            errors.append(
                f"{path}.corrections должен быть "
                "массивом строк."
            )


def _validate_bbox(
    value: Any,
    path: str,
    errors: list[str],
) -> None:

    if (
        not _is_sequence_with_length(
            value=value,
            length=4,
        )
        or not all(
            _is_number(item)
            for item in value
        )
    ):
        errors.append(
            f"{path} должен содержать "
            "четыре числовые координаты."
        )
        return

    x1, y1, x2, y2 = map(float, value)

    if x1 > x2 or y1 > y2:
        errors.append(
            f"{path} содержит перепутанные "
            f"границы: {list(value)}."
        )


def _validate_optional_pair(
    value: Any,
    path: str,
    errors: list[str],
    *,
    integer: bool,
) -> None:

    if value is None:
        return

    value_validator = (
        _is_int
        if integer
        else _is_number
    )

    if (
        not _is_pair(value)
        or not all(
            value_validator(item)
            for item in value
        )
    ):
        value_type = (
            "целых"
            if integer
            else "числовых"
        )

        errors.append(
            f"{path} должен содержать "
            f"два {value_type} значения."
        )


def _validate_optional_mapping(
    value: Any,
    path: str,
    errors: list[str],
) -> None:
    if (
        value is not None
        and not isinstance(value, Mapping)
    ):
        errors.append(
            f"{path} должен быть объектом."
        )


def _is_number(value: Any) -> bool:

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _is_int(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
    )


def _is_pair(value: Any) -> bool:
    return _is_sequence_with_length(
        value=value,
        length=2,
    )

def _is_sequence_with_length(
    value: Any,
    *,
    length: int,
) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == length
    )