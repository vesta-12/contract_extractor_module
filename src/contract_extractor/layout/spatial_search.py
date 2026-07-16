from __future__ import annotations
import math
from collections import defaultdict
from collections.abc import Iterable
from contract_extractor.models import (
    LayoutLine,
    OCRDocument,
    OCRWord,
)

class SpatialSearch:

    def __init__(
        self,
        document: OCRDocument,
        lines: Iterable[LayoutLine],
    ) -> None:
        self.document = document
        self.lines = tuple(lines)

        self._word_by_id: dict[str, OCRWord] = {}
        self._line_by_id: dict[str, LayoutLine] = {}

        self._line_by_word: dict[
            tuple[int, int],
            LayoutLine,
        ] = {}

        self._lines_by_page: dict[
            int,
            tuple[LayoutLine, ...],
        ] = {}

        self._lines_by_page_region: dict[
            tuple[int, str | None],
            tuple[LayoutLine, ...],
        ] = {}

        self._build_indexes()

    def _build_indexes(self) -> None:

        for word in self.document.iter_words():
            if word.id in self._word_by_id:
                raise ValueError(
                    f"обнаружен повторяющийся ID слова: {word.id}"
                )

            self._word_by_id[word.id] = word

        mutable_lines_by_page: dict[
            int,
            list[LayoutLine],
        ] = defaultdict(list)

        mutable_lines_by_region: dict[
            tuple[int, str | None],
            list[LayoutLine],
        ] = defaultdict(list)

        for line in self.lines:
            if line.id in self._line_by_id:
                raise ValueError(
                    f"обнаружен повторяющийся ID строки: {line.id}"
                )

            if line.page > self.document.page_count:
                raise ValueError(
                    f"строка {line.id} относится "
                    f"к несуществующей странице {line.page}"
                )

            self._line_by_id[line.id] = line

            mutable_lines_by_page[
                line.page
            ].append(line)

            mutable_lines_by_region[
                (line.page, line.region)
            ].append(line)

            for word in line.words:
                key = (word.page, word.index)

                if key in self._line_by_word:
                    existing_line = self._line_by_word[key]

                    raise ValueError(
                        f"слово {word.id} одновременно находится "
                        f"в строках {existing_line.id} и {line.id}"
                    )

                self._line_by_word[key] = line

        for page_number, page_lines in (
            mutable_lines_by_page.items()
        ):
            self._lines_by_page[page_number] = tuple(
                sorted(
                    page_lines,
                    key=self._line_sort_key,
                )
            )

        for key, region_lines in (
            mutable_lines_by_region.items()
        ):
            self._lines_by_page_region[key] = tuple(
                sorted(
                    region_lines,
                    key=self._line_sort_key,
                )
            )

    @staticmethod
    def _line_sort_key(
        line: LayoutLine,
    ) -> tuple[float, float, int]:
        return (
            line.bbox.center_y,
            line.bbox.x1,
            line.index,
        )

    def get_word(
        self,
        word_id: str,
    ) -> OCRWord:

        try:
            return self._word_by_id[word_id]
        except KeyError as error:
            raise KeyError(
                f"слово с ID {word_id!r} не найдено"
            ) from error

    def get_line(
        self,
        line_id: str,
    ) -> LayoutLine:

        try:
            return self._line_by_id[line_id]
        except KeyError as error:
            raise KeyError(
                f"строка с ID {line_id!r} не найдена"
            ) from error

    def line_for_word(
        self,
        word: OCRWord,
    ) -> LayoutLine | None:
        return self._line_by_word.get(
            (word.page, word.index)
        )

    def line_for_word_id(
        self,
        word_id: str,
    ) -> LayoutLine | None:

        word = self.get_word(word_id)
        return self.line_for_word(word)

    def lines_on_page(
        self,
        page_number: int,
    ) -> tuple[LayoutLine, ...]:

        self.document.get_page(page_number)

        return self._lines_by_page.get(
            page_number,
            (),
        )

    def lines_in_region(
        self,
        page_number: int,
        region: str | None,
    ) -> tuple[LayoutLine, ...]:
        self.document.get_page(page_number)

        return self._lines_by_page_region.get(
            (page_number, region),
            (),
        )

    def words_on_same_line(
        self,
        word: OCRWord,
        *,
        include_source: bool = False,
    ) -> tuple[OCRWord, ...]:
        line = self.line_for_word(word)

        if line is None:
            return ()

        if include_source:
            return line.words

        return tuple(
            candidate
            for candidate in line.words
            if candidate.id != word.id
        )

    def words_to_right(
        self,
        word: OCRWord,
        *,
        max_horizontal_gap: float | None = None,
        limit: int | None = None,
    ) -> tuple[OCRWord, ...]:

        self._validate_limit(limit)
        self._validate_distance(
            max_horizontal_gap,
            "max_horizontal_gap",
        )

        line = self.line_for_word(word)

        if line is None:
            return ()

        candidates = [
            candidate
            for candidate in line.words
            if candidate.bbox.center_x > word.bbox.center_x
        ]

        if max_horizontal_gap is not None:
            candidates = [
                candidate
                for candidate in candidates
                if word.bbox.horizontal_gap(
                    candidate.bbox
                ) <= max_horizontal_gap
            ]

        candidates.sort(
            key=lambda candidate: (
                candidate.bbox.x1,
                candidate.index,
            )
        )

        return self._apply_limit(
            candidates,
            limit,
        )

    def words_to_left(
        self,
        word: OCRWord,
        *,
        max_horizontal_gap: float | None = None,
        limit: int | None = None,
    ) -> tuple[OCRWord, ...]:
        self._validate_limit(limit)
        self._validate_distance(
            max_horizontal_gap,
            "max_horizontal_gap",
        )

        line = self.line_for_word(word)

        if line is None:
            return ()

        candidates = [
            candidate
            for candidate in line.words
            if candidate.bbox.center_x < word.bbox.center_x
        ]

        if max_horizontal_gap is not None:
            candidates = [
                candidate
                for candidate in candidates
                if word.bbox.horizontal_gap(
                    candidate.bbox
                ) <= max_horizontal_gap
            ]

        candidates.sort(
            key=lambda candidate: (
                -candidate.bbox.x2,
                -candidate.index,
            )
        )

        return self._apply_limit(
            candidates,
            limit,
        )

    def lines_below(
        self,
        line: LayoutLine,
        *,
        same_region: bool = True,
        max_vertical_gap: float | None = None,
        limit: int | None = None,
    ) -> tuple[LayoutLine, ...]:

        self._validate_limit(limit)
        self._validate_distance(
            max_vertical_gap,
            "max_vertical_gap",
        )

        candidates = self._candidate_lines(
            line=line,
            same_region=same_region,
        )

        candidates = [
            candidate
            for candidate in candidates
            if candidate.bbox.center_y > line.bbox.center_y
        ]

        if max_vertical_gap is not None:
            candidates = [
                candidate
                for candidate in candidates
                if line.bbox.vertical_gap(
                    candidate.bbox
                ) <= max_vertical_gap
            ]

        candidates.sort(
            key=lambda candidate: (
                line.bbox.vertical_gap(
                    candidate.bbox
                ),
                abs(
                    candidate.bbox.center_x
                    - line.bbox.center_x
                ),
                candidate.bbox.x1,
            )
        )

        return self._apply_limit(
            candidates,
            limit,
        )

    def lines_above(
        self,
        line: LayoutLine,
        *,
        same_region: bool = True,
        max_vertical_gap: float | None = None,
        limit: int | None = None,
    ) -> tuple[LayoutLine, ...]:

        self._validate_limit(limit)
        self._validate_distance(
            max_vertical_gap,
            "max_vertical_gap",
        )

        candidates = self._candidate_lines(
            line=line,
            same_region=same_region,
        )

        candidates = [
            candidate
            for candidate in candidates
            if candidate.bbox.center_y < line.bbox.center_y
        ]

        if max_vertical_gap is not None:
            candidates = [
                candidate
                for candidate in candidates
                if line.bbox.vertical_gap(
                    candidate.bbox
                ) <= max_vertical_gap
            ]

        candidates.sort(
            key=lambda candidate: (
                line.bbox.vertical_gap(
                    candidate.bbox
                ),
                abs(
                    candidate.bbox.center_x
                    - line.bbox.center_x
                ),
                candidate.bbox.x1,
            )
        )

        return self._apply_limit(
            candidates,
            limit,
        )

    def nearest_words(
        self,
        word: OCRWord,
        *,
        same_region: bool = True,
        max_distance: float | None = None,
        limit: int = 10,
    ) -> tuple[OCRWord, ...]:

        self._validate_limit(limit)

        if limit == 0:
            return ()

        self._validate_distance(
            max_distance,
            "max_distance",
        )

        page = self.document.get_page(word.page)

        candidates: list[
            tuple[float, OCRWord]
        ] = []

        for candidate in page.words:
            if candidate.id == word.id:
                continue

            if (
                same_region
                and candidate.region != word.region
            ):
                continue

            distance = self.distance_between_words(
                word,
                candidate,
            )

            if (
                max_distance is not None
                and distance > max_distance
            ):
                continue

            candidates.append(
                (distance, candidate)
            )

        candidates.sort(
            key=lambda item: (
                item[0],
                item[1].bbox.center_y,
                item[1].bbox.x1,
            )
        )

        return tuple(
            candidate
            for _, candidate
            in candidates[:limit]
        )

    def context_lines(
        self,
        word: OCRWord,
        *,
        before: int = 1,
        after: int = 1,
        same_region: bool = True,
    ) -> tuple[LayoutLine, ...]:

        if before < 0:
            raise ValueError(
                "before не может быть отрицательным"
            )

        if after < 0:
            raise ValueError(
                "after не может быть отрицательным"
            )

        current_line = self.line_for_word(word)

        if current_line is None:
            return ()

        if same_region:
            lines = list(
                self.lines_in_region(
                    word.page,
                    word.region,
                )
            )
        else:
            lines = list(
                self.lines_on_page(word.page)
            )

        try:
            current_position = lines.index(
                current_line
            )
        except ValueError:
            return (current_line,)

        start = max(
            0,
            current_position - before,
        )

        end = min(
            len(lines),
            current_position + after + 1,
        )

        return tuple(lines[start:end])

    def context_text(
        self,
        word: OCRWord,
        *,
        before: int = 1,
        after: int = 1,
        same_region: bool = True,
        normalized: bool = True,
    ) -> str:
        lines = self.context_lines(
            word=word,
            before=before,
            after=after,
            same_region=same_region,
        )

        if normalized:
            return "\n".join(
                line.normalized_text
                for line in lines
            )

        return "\n".join(
            line.text
            for line in lines
        )

    def find_words(
        self,
        text: str,
        *,
        exact: bool = True,
        page_number: int | None = None,
        region: str | None = None,
        normalized: bool = True,
    ) -> tuple[OCRWord, ...]:

        query = text.strip().casefold()

        if not query:
            raise ValueError(
                "поисковый текст не может быть пустым"
            )

        if page_number is not None:
            words = self.document.get_page(
                page_number
            ).words
        else:
            words = tuple(
                self.document.iter_words()
            )

        result: list[OCRWord] = []

        for word in words:
            if (
                region is not None
                and word.region != region
            ):
                continue

            candidate_text = (
                word.search_text
                if normalized
                else word.text
            ).strip().casefold()

            matches = (
                candidate_text == query
                if exact
                else query in candidate_text
            )

            if matches:
                result.append(word)

        return tuple(result)

    def find_lines(
        self,
        text: str,
        *,
        page_number: int | None = None,
        region: str | None = None,
        normalized: bool = True,
    ) -> tuple[LayoutLine, ...]:
        query = text.strip().casefold()

        if not query:
            raise ValueError(
                "поисковый текст не может быть пустым"
            )

        if page_number is not None:
            candidate_lines = self.lines_on_page(
                page_number
            )
        else:
            candidate_lines = self.lines

        result: list[LayoutLine] = []

        for line in candidate_lines:
            if (
                region is not None
                and line.region != region
            ):
                continue

            candidate_text = (
                line.normalized_text
                if normalized
                else line.text
            ).casefold()

            if query in candidate_text:
                result.append(line)

        return tuple(result)

    @staticmethod
    def distance_between_words(
        first: OCRWord,
        second: OCRWord,
    ) -> float:

        return math.hypot(
            first.bbox.center_x
            - second.bbox.center_x,
            first.bbox.center_y
            - second.bbox.center_y,
        )

    def _candidate_lines(
        self,
        line: LayoutLine,
        same_region: bool,
    ) -> list[LayoutLine]:
        if same_region:
            candidates = self.lines_in_region(
                line.page,
                line.region,
            )
        else:
            candidates = self.lines_on_page(
                line.page
            )

        return [
            candidate
            for candidate in candidates
            if candidate.id != line.id
        ]

    @staticmethod
    def _validate_limit(
        limit: int | None,
    ) -> None:
        if limit is not None and limit < 0:
            raise ValueError(
                "limit не может быть отрицательным"
            )

    @staticmethod
    def _validate_distance(
        distance: float | None,
        field_name: str,
    ) -> None:
        if distance is not None and distance < 0:
            raise ValueError(
                f"{field_name} не может быть отрицательным"
            )

    @staticmethod
    def _apply_limit(
        items: list[OCRWord] | list[LayoutLine],
        limit: int | None,
    ) -> tuple[OCRWord, ...] | tuple[LayoutLine, ...]:
        if limit is None:
            return tuple(items)

        return tuple(items[:limit])