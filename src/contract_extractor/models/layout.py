from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator
from contract_extractor.models.geometry import BoundingBox
from contract_extractor.models.ocr import OCRWord

@dataclass(frozen=True, slots=True)
class LayoutLine:

    id: str
    page: int
    index: int
    region: str | None

    words: tuple[OCRWord, ...]
    text: str
    bbox: BoundingBox
    confidence: float

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError(
                f"номер страницы должен начинаться с 1: {self.page}"
            )

        if self.index < 0:
            raise ValueError(
                f"индекс строки не может быть отрицательным: {self.index}"
            )

        if not self.words:
            raise ValueError(
                f"строка {self.id} не может быть пустой"
            )

        if not self.text:
            raise ValueError(
                f"текст строки {self.id} не может быть пустым"
            )

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "уверенность строки должна находиться "
                f"в диапазоне от 0.0 до 1.0: {self.confidence}"
            )

        for word in self.words:
            if word.page != self.page:
                raise ValueError(
                    f"слово {word.id} относится к странице "
                    f"{word.page}, а строка {self.id} — "
                    f"к странице {self.page}"
                )

            if word.region != self.region:
                raise ValueError(
                    f"слово {word.id} относится к региону "
                    f"{word.region}, а строка {self.id} — "
                    f"к региону {self.region}"
                )

    @property
    def word_count(self) -> int:
        return len(self.words)

    @property
    def word_ids(self) -> tuple[str, ...]:
        return tuple(
            word.id
            for word in self.words
        )

    @property
    def word_indices(self) -> tuple[int, ...]:
        return tuple(
            word.index
            for word in self.words
        )

    @property
    def first_word(self) -> OCRWord:
        return self.words[0]

    @property
    def last_word(self) -> OCRWord:
        return self.words[-1]

    @property
    def start_word_index(self) -> int:
        return min(self.word_indices)

    @property
    def end_word_index(self) -> int:
        return max(self.word_indices)

    @property
    def normalized_text(self) -> str:

        return " ".join(
            word.search_text
            for word in self.words
        )

    @property
    def lowercase_text(self) -> str:
        return self.normalized_text.casefold()

    @property
    def center_x(self) -> float:
        return self.bbox.center_x

    @property
    def center_y(self) -> float:
        return self.bbox.center_y

    def contains_word_id(self, word_id: str) -> bool:
        return any(
            word.id == word_id
            for word in self.words
        )

    def contains_word_index(self, word_index: int) -> bool:
        return any(
            word.index == word_index
            for word in self.words
        )

    def get_word(self, word_index: int) -> OCRWord:

        for word in self.words:
            if word.index == word_index:
                return word

        raise KeyError(
            f"строка {self.id} не содержит "
            f"слово с индексом {word_index}"
        )

    def iter_words(self) -> Iterator[OCRWord]:
        yield from self.words