from __future__ import annotations
import re
from dataclasses import dataclass
from statistics import mean
from contract_extractor.layout import SpatialSearch
from contract_extractor.models import (
    BoundingBox,
    EntityCandidate,
    EntityEvidence,
    LayoutLine,
    OCRDocument,
    OCRValue,
    OCRWord,
)
from contract_extractor.rules.base import ExtractionRule

@dataclass(frozen=True, slots=True)
class NumericTaxIdRuleConfig:
    max_anchor_gap: float = 0.05

    max_value_words: int = 4

    min_candidate_confidence: float = 0.40

    use_ocr_value_confirmation: bool = True

    def __post_init__(self) -> None:
        if self.max_anchor_gap < 0:
            raise ValueError(
                "max_anchor_gap не может быть отрицательным"
            )

        if self.max_value_words < 1:
            raise ValueError(
                "max_value_words должен быть не меньше 1"
            )

        if not (
            0.0
            <= self.min_candidate_confidence
            <= 1.0
        ):
            raise ValueError(
                "min_candidate_confidence должен находиться "
                "в диапазоне от 0.0 до 1.0"
            )


BINRuleConfig = NumericTaxIdRuleConfig
IINRuleConfig = NumericTaxIdRuleConfig
INNRuleConfig = NumericTaxIdRuleConfig


class _AnchoredNumericTaxIdRule(ExtractionRule):

    rule_id = ""
    entity_type = ""

    display_name = ""

    format_name = ""

    anchor_aliases: frozenset[str] = frozenset()

    valid_lengths: frozenset[int] = frozenset()

    _allowed_value_pattern = re.compile(
        r"^[\d\s.,\-]+$"
    )

    _non_digit_pattern = re.compile(
        r"\D+"
    )

    _anchor_strip_characters = (
        " \t\r\n"
        ":;,.!?"
        "()[]{}"
        "«»\"'"
    )

    def __init__(
        self,
        config: NumericTaxIdRuleConfig | None = None,
    ) -> None:
        self.config = (
            config
            or NumericTaxIdRuleConfig()
        )

        if not self.rule_id:
            raise ValueError(
                f"У правила {self.__class__.__name__} "
                "не указан rule_id"
            )

        if not self.entity_type:
            raise ValueError(
                f"У правила {self.__class__.__name__} "
                "не указан entity_type"
            )

        if not self.display_name:
            raise ValueError(
                f"У правила {self.__class__.__name__} "
                "не указан display_name"
            )

        if not self.format_name:
            raise ValueError(
                f"У правила {self.__class__.__name__} "
                "не указан format_name"
            )

        if not self.anchor_aliases:
            raise ValueError(
                f"У правила {self.__class__.__name__} "
                "не указаны якоря"
            )

        if not self.valid_lengths:
            raise ValueError(
                f"У правила {self.__class__.__name__} "
                "не указаны допустимые длины"
            )

        if any(
            length < 1
            for length in self.valid_lengths
        ):
            raise ValueError(
                "допустимые длины идентификатора "
                "должны быть больше 0"
            )

    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:

        candidates: list[EntityCandidate] = []

        seen: set[
            tuple[
                int,
                str,
                tuple[str, ...],
            ]
        ] = set()

        for anchor in document.iter_words():
            if not self._is_anchor(anchor):
                continue

            line = spatial.line_for_word(anchor)

            if line is None:
                continue

            search_result = self._find_value_words(
                anchor=anchor,
                line=line,
            )

            if search_result is None:
                continue

            value_words, normalized_value = (
                search_result
            )

            word_ids = tuple(
                word.id
                for word in value_words
            )

            deduplication_key = (
                anchor.page,
                normalized_value,
                word_ids,
            )

            if deduplication_key in seen:
                continue

            seen.add(deduplication_key)

            candidate = self._build_candidate(
                document=document,
                spatial=spatial,
                anchor=anchor,
                value_words=value_words,
                normalized_value=normalized_value,
                line=line,
            )

            if (
                candidate.confidence
                < self.config.min_candidate_confidence
            ):
                continue

            candidates.append(candidate)

        return tuple(candidates)

    def _is_anchor(
        self,
        word: OCRWord,
    ) -> bool:

        normalized = (
            word.search_text
            .casefold()
            .strip(self._anchor_strip_characters)
        )

        return normalized in self.anchor_aliases

    def _find_value_words(
        self,
        anchor: OCRWord,
        line: LayoutLine,
    ) -> tuple[
        tuple[OCRWord, ...],
        str,
    ] | None:

        anchor_position = self._word_position(
            line=line,
            word_id=anchor.id,
        )

        if anchor_position is None:
            return None

        words_after_anchor = line.words[
            anchor_position + 1:
        ]

        if not words_after_anchor:
            return None

        first_word = words_after_anchor[0]

        anchor_gap = anchor.bbox.horizontal_gap(
            first_word.bbox
        )

        if (
            anchor_gap
            > self.config.max_anchor_gap
        ):
            return None

        maximum_length = max(
            self.valid_lengths
        )

        maximum_word_count = min(
            self.config.max_value_words,
            len(words_after_anchor),
        )

        matches: list[
            tuple[
                int,
                tuple[OCRWord, ...],
                str,
            ]
        ] = []

        for size in range(
            1,
            maximum_word_count + 1,
        ):
            candidate_words = tuple(
                words_after_anchor[:size]
            )

            candidate_text = " ".join(
                word.search_text
                for word in candidate_words
            ).strip()

            if not candidate_text:
                break

            if not self._allowed_value_pattern.fullmatch(
                candidate_text
            ):
                break

            normalized_value = (
                self._normalize_text(
                    candidate_text
                )
            )

            digit_count = len(
                normalized_value
            )

            if digit_count > maximum_length:
                break

            if digit_count in self.valid_lengths:
                matches.append(
                    (
                        digit_count,
                        candidate_words,
                        normalized_value,
                    )
                )

        if not matches:
            return None
        _, best_words, best_value = max(
            matches,
            key=lambda item: (
                item[0],
                len(item[1]),
            ),
        )

        return best_words, best_value

    def _build_candidate(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
        anchor: OCRWord,
        value_words: tuple[OCRWord, ...],
        normalized_value: str,
        line: LayoutLine,
    ) -> EntityCandidate:

        value_bbox = self._union_word_boxes(
            value_words
        )

        raw_value = " ".join(
            word.text
            for word in value_words
        )

        average_ocr_confidence = mean(
            word.confidence
            for word in value_words
        )

        anchor_gap = anchor.bbox.horizontal_gap(
            value_words[0].bbox
        )

        evidence: list[EntityEvidence] = []

        confidence = 0.20

        anchor_score = 0.30

        evidence.append(
            EntityEvidence(
                kind="anchor",
                description=(
                    f"найден якорь {self.display_name} "
                    "на той же строке, что и значение"
                ),
                score_delta=anchor_score,
                data={
                    "anchor_text": anchor.text,
                    "anchor_word_id": anchor.id,
                    "aliases": sorted(
                        self.anchor_aliases
                    ),
                },
            )
        )

        confidence += anchor_score

        format_score = 0.25

        evidence.append(
            EntityEvidence(
                kind="format",
                description=(
                    "числовое значение имеет "
                    "допустимую длину"
                ),
                score_delta=format_score,
                data={
                    "format": self.format_name,
                    "digit_count": len(
                        normalized_value
                    ),
                    "valid_lengths": sorted(
                        self.valid_lengths
                    ),
                },
            )
        )

        confidence += format_score

        gap_score = self._calculate_gap_score(
            anchor_gap
        )

        evidence.append(
            EntityEvidence(
                kind="distance",
                description=(
                    "значение расположено справа "
                    "от ключевого слова"
                ),
                score_delta=gap_score,
                data={
                    "horizontal_gap": round(
                        anchor_gap,
                        6,
                    ),
                    "max_horizontal_gap": (
                        self.config.max_anchor_gap
                    ),
                },
            )
        )

        confidence += gap_score

        ocr_score = (
            average_ocr_confidence * 0.10
        )

        evidence.append(
            EntityEvidence(
                kind="ocr_confidence",
                description=(
                    "учтена средняя уверенность OCR "
                    "для слов значения"
                ),
                score_delta=ocr_score,
                data={
                    "average_ocr_confidence": round(
                        average_ocr_confidence,
                        6,
                    ),
                },
            )
        )

        confidence += ocr_score

        if len(value_words) == 1:
            compact_score = 0.05

            evidence.append(
                EntityEvidence(
                    kind="compact_value",
                    description=(
                        f"{self.display_name} распознан "
                        "одним OCR-словом"
                    ),
                    score_delta=compact_score,
                    data={
                        "word_count": 1,
                    },
                )
            )

            confidence += compact_score

        matching_ocr_values: tuple[
            OCRValue,
            ...
        ] = ()

        if self.config.use_ocr_value_confirmation:
            matching_ocr_values = (
                self._matching_ocr_values(
                    document=document,
                    value_words=value_words,
                    normalized_value=(
                        normalized_value
                    ),
                )
            )

        if matching_ocr_values:
            confirmation_score = 0.10

            evidence.append(
                EntityEvidence(
                    kind="ocr_value_confirmation",
                    description=(
                        "значение подтверждено массивом "
                        "предварительно найденных OCR values"
                    ),
                    score_delta=confirmation_score,
                    data={
                        "ocr_value_ids": [
                            value.id
                            for value
                            in matching_ocr_values
                        ],
                        "ocr_value_types": [
                            value.value_type
                            for value
                            in matching_ocr_values
                        ],
                    },
                )
            )

            confidence += confirmation_score

        confidence = min(
            1.0,
            confidence,
        )

        first_word = value_words[0]
        last_word = value_words[-1]

        candidate_id = (
            f"candidate-{self.entity_type}-"
            f"p{anchor.page}-"
            f"w{first_word.index}-"
            f"{last_word.index}"
        )

        context_text = spatial.context_text(
            anchor,
            before=2,
            after=2,
            same_region=True,
            normalized=True,
        )

        return EntityCandidate(
            id=candidate_id,
            entity_type=self.entity_type,
            value=normalized_value,
            raw_value=raw_value,
            page=anchor.page,
            bbox=value_bbox,
            word_ids=tuple(
                word.id
                for word in value_words
            ),
            confidence=confidence,
            rule_id=self.rule_id,
            region=anchor.region,
            line_id=line.id,
            anchor_word_ids=(
                anchor.id,
            ),
            validation={
                "format": self.format_name,
                "status": "shape_valid",
                "shape": True,
                "digit_count": len(
                    normalized_value
                ),
                "valid_lengths": sorted(
                    self.valid_lengths
                ),
            },
            evidence=tuple(evidence),
            metadata={
                "display_name": (
                    self.display_name
                ),
                "anchor_text": anchor.text,
                "anchor_bbox": (
                    anchor.bbox.to_list()
                ),
                "context_text": context_text,
                "value_word_count": len(
                    value_words
                ),
                "ocr_value_confirmation": (
                    bool(matching_ocr_values)
                ),
            },
        )

    def _matching_ocr_values(
        self,
        document: OCRDocument,
        value_words: tuple[OCRWord, ...],
        normalized_value: str,
    ) -> tuple[OCRValue, ...]:
        value_refs: set[int] = set()

        for word in value_words:
            value_refs.update(
                word.value_refs
            )

        matches: list[OCRValue] = []

        for value_ref in sorted(value_refs):
            try:
                ocr_value = document.get_value(
                    value_ref
                )
            except IndexError:
                continue

            if (
                ocr_value.value_type.casefold()
                != self.entity_type.casefold()
            ):
                continue

            normalized_ocr_value = (
                self._normalize_text(
                    ocr_value.value
                )
            )

            if (
                normalized_ocr_value
                != normalized_value
            ):
                continue

            matches.append(ocr_value)

        return tuple(matches)

    def _calculate_gap_score(
        self,
        anchor_gap: float,
    ) -> float:

        if self.config.max_anchor_gap == 0:
            return (
                0.10
                if anchor_gap == 0
                else 0.0
            )

        ratio = min(
            1.0,
            anchor_gap
            / self.config.max_anchor_gap,
        )

        return max(
            0.0,
            0.10 * (1.0 - ratio),
        )

    @classmethod
    def _normalize_text(
        cls,
        value: str,
    ) -> str:
        return cls._non_digit_pattern.sub(
            "",
            value,
        )

    @staticmethod
    def _word_position(
        line: LayoutLine,
        word_id: str,
    ) -> int | None:
        for index, word in enumerate(
            line.words
        ):
            if word.id == word_id:
                return index

        return None

    @staticmethod
    def _union_word_boxes(
        words: tuple[OCRWord, ...],
    ) -> BoundingBox:
        if not words:
            raise ValueError(
                "нельзя объединить координаты "
                "пустого списка слов"
            )

        result = words[0].bbox

        for word in words[1:]:
            result = result.union(
                word.bbox
            )

        return result


class BINRule(_AnchoredNumericTaxIdRule):

    rule_id = "tax_id.bin.anchor_right.v2"

    entity_type = "bin"

    display_name = "БИН"

    format_name = "kz_bin"

    anchor_aliases = frozenset(
        {
            "бин",
            "бсн",
        }
    )

    valid_lengths = frozenset(
        {
            12,
        }
    )


class IINRule(_AnchoredNumericTaxIdRule):

    rule_id = "tax_id.iin.anchor_right.v1"

    entity_type = "iin"

    display_name = "ИИН"

    format_name = "kz_iin"

    anchor_aliases = frozenset(
        {
            "иин",
            "жсн",
        }
    )

    valid_lengths = frozenset(
        {
            12,
        }
    )


class INNRule(_AnchoredNumericTaxIdRule):
    rule_id = "tax_id.inn.anchor_right.v1"

    entity_type = "inn"

    display_name = "ИНН"

    format_name = "tax_identifier"

    anchor_aliases = frozenset(
        {
            "инн",
        }
    )

    valid_lengths = frozenset(
        {
            10,
            12,
            14,
        }
    )