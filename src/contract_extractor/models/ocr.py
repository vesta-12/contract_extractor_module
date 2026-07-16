from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from contract_extractor.models.geometry import BoundingBox

@dataclass(frozen=True, slots=True)
class OCRRegion:
    id: str
    page: int
    region_type: str
    word_start: int
    word_end: int
    bbox: BoundingBox | None = None

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError(
                f"номер страницы должен начинаться с 1: {self.page}"
            )

        if self.word_start < 0:
            raise ValueError(
                f"word_start не может быть отрицательным: {self.word_start}"
            )

        if self.word_end < self.word_start:
            raise ValueError(
                "word_end не может быть меньше word_start: "
                f"{self.word_start}, {self.word_end}"
            )

    @property
    def word_count(self) -> int:
        return self.word_end - self.word_start

    def contains_word(self, word_index: int) -> bool:
        return self.word_start <= word_index < self.word_end


@dataclass(frozen=True, slots=True)
class OCRWord:
    id: str
    page: int
    index: int
    text: str
    confidence: float
    bbox: BoundingBox

    raw_text: str | None = None
    normalized_text: str | None = None

    value_refs: tuple[int, ...] = ()
    region: str | None = None

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError(
                f"номер страницы должен начинаться с 1: {self.page}"
            )

        if self.index < 0:
            raise ValueError(
                f"индекс слова не может быть отрицательным: {self.index}"
            )

        if not self.text:
            raise ValueError(
                f"текст OCR-слова {self.id} не может быть пустым"
            )

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "уверенность OCR должна находиться в диапазоне "
                f"от 0.0 до 1.0: {self.confidence}"
            )

    @property
    def search_text(self) -> str:

        if self.normalized_text:
            return self.normalized_text

        return self.text

    @property
    def lowercase_text(self) -> str:
        return self.search_text.casefold()

    @property
    def has_value_reference(self) -> bool:
        return bool(self.value_refs)

    def references_value(self, value_index: int) -> bool:
        return value_index in self.value_refs


@dataclass(frozen=True, slots=True)
class OCRValue:
    id: str
    index: int
    value_type: str
    value: str
    page: int
    bbox: BoundingBox
    word_indices: tuple[int, ...]

    confidence: float | None = None
    source_bbox: BoundingBox | None = None
    raw_value: str | None = None
    region: str | None = None

    validation: dict[str, Any] = field(default_factory=dict)
    corrections: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(
                f"индекс OCRValue не может быть отрицательным: {self.index}"
            )

        if self.page < 1:
            raise ValueError(
                f"номер страницы должен начинаться с 1: {self.page}"
            )

        if not self.value_type:
            raise ValueError(
                f"тип OCRValue {self.id} не может быть пустым"
            )

        if not self.value:
            raise ValueError(
                f"значение OCRValue {self.id} не может быть пустым"
            )

        if self.confidence is not None:
            if not 0.0 <= self.confidence <= 1.0:
                raise ValueError(
                    "уверенность OCRValue должна находиться "
                    f"в диапазоне от 0.0 до 1.0: {self.confidence}"
                )


@dataclass(slots=True)
class OCRPage:

    number: int
    words: tuple[OCRWord, ...]
    regions: tuple[OCRRegion, ...] = ()

    text_span: tuple[int, int] | None = None
    order: str | None = None
    pdf_size: tuple[float, float] | None = None

    quality: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(
                f"номер страницы должен начинаться с 1: {self.number}"
            )

        for expected_index, word in enumerate(self.words):
            if word.page != self.number:
                raise ValueError(
                    f"слово {word.id} относится к странице {word.page}, "
                    f"но находится в объекте страницы {self.number}"
                )

            if word.index != expected_index:
                raise ValueError(
                    f"нарушена последовательность индексов слов "
                    f"на странице {self.number}: ожидался индекс "
                    f"{expected_index}, получен {word.index}"
                )

    @property
    def word_count(self) -> int:
        return len(self.words)

    def get_word(self, index: int) -> OCRWord:
        try:
            return self.words[index]
        except IndexError as error:
            raise IndexError(
                f"на странице {self.number} нет слова с индексом {index}"
            ) from error

    def get_region(self, region_type: str) -> OCRRegion | None:
        normalized_type = region_type.casefold()

        for region in self.regions:
            if region.region_type.casefold() == normalized_type:
                return region

        return None

    def words_in_region(self, region_type: str) -> tuple[OCRWord, ...]:
        region = self.get_region(region_type)

        if region is None:
            return ()

        return self.words[region.word_start:region.word_end]

    def iter_words(self) -> Iterator[OCRWord]:
        yield from self.words


@dataclass(slots=True)
class OCRDocument:
    document_text: str
    pages: tuple[OCRPage, ...]
    values: tuple[OCRValue, ...] = ()

    meta: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)

    source_path: Path | None = None

    def __post_init__(self) -> None:
        for expected_page_number, page in enumerate(
            self.pages,
            start=1,
        ):
            if page.number != expected_page_number:
                raise ValueError(
                    "страницы документа должны идти последовательно: "
                    f"ожидалась страница {expected_page_number}, "
                    f"получена {page.number}"
                )

        for expected_value_index, value in enumerate(self.values):
            if value.index != expected_value_index:
                raise ValueError(
                    "OCR-значения должны идти последовательно: "
                    f"ожидался индекс {expected_value_index}, "
                    f"получен {value.index}"
                )

            if value.page > len(self.pages):
                raise ValueError(
                    f"OCRValue {value.id} ссылается на несуществующую "
                    f"страницу {value.page}"
                )

            page = self.get_page(value.page)

            for word_index in value.word_indices:
                if word_index < 0 or word_index >= page.word_count:
                    raise ValueError(
                        f"OCRValue {value.id} ссылается на "
                        f"несуществующее слово {word_index} "
                        f"страницы {value.page}"
                    )

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def word_count(self) -> int:
        return sum(page.word_count for page in self.pages)

    @property
    def value_count(self) -> int:
        return len(self.values)

    def get_page(self, page_number: int) -> OCRPage:
        if page_number < 1:
            raise IndexError(
                "номер страницы должен начинаться с 1"
            )

        try:
            return self.pages[page_number - 1]
        except IndexError as error:
            raise IndexError(
                f"В документе нет страницы {page_number}"
            ) from error

    def get_word(
        self,
        page_number: int,
        word_index: int,
    ) -> OCRWord:
        page = self.get_page(page_number)
        return page.get_word(word_index)

    def get_value(self, value_index: int) -> OCRValue:
        try:
            return self.values[value_index]
        except IndexError as error:
            raise IndexError(
                f"В документе нет OCR-значения с индексом {value_index}"
            ) from error

    def iter_words(self) -> Iterator[OCRWord]:
        for page in self.pages:
            yield from page.iter_words()

    def values_for_word(
        self,
        page_number: int,
        word_index: int,
    ) -> tuple[OCRValue, ...]:

        result: list[OCRValue] = []

        for value in self.values:
            if value.page != page_number:
                continue

            if word_index in value.word_indices:
                result.append(value)

        return tuple(result)

    def words_for_value(
        self,
        value_index: int,
    ) -> tuple[OCRWord, ...]:

        value = self.get_value(value_index)
        page = self.get_page(value.page)

        return tuple(
            page.get_word(word_index)
            for word_index in value.word_indices
        )