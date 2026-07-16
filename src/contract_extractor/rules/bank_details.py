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
class BankCodeRuleConfig:

    max_anchor_gap: float = 0.06
    max_value_words: int = 3
    min_candidate_confidence: float = 0.45
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

        if not 0.0 <= self.min_candidate_confidence <= 1.0:
            raise ValueError(
                "min_candidate_confidence должен находиться "
                "в диапазоне от 0.0 до 1.0"
            )


BIKRuleConfig = BankCodeRuleConfig
BICSWIFTRuleConfig = BankCodeRuleConfig


class _AnchoredBankCodeRule(ExtractionRule):

    rule_id = ""
    entity_type = ""

    display_name = ""
    format_name = ""

    anchor_aliases: frozenset[str] = frozenset()
    ocr_value_types: frozenset[str] = frozenset()

    allowed_token_pattern: re.Pattern[str]

    _anchor_strip_characters = (
        " \t\r\n"
        ":;,.!?"
        "()[]{}"
        "«»\"'"
    )

    _non_alphanumeric_pattern = re.compile(
        r"[^A-ZА-Я0-9]+"
    )

    def __init__(
        self,
        config: BankCodeRuleConfig | None = None,
    ) -> None:
        self.config = config or BankCodeRuleConfig()

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
                "не указаны anchor_aliases"
            )

        if not self.ocr_value_types:
            raise ValueError(
                f"У правила {self.__class__.__name__} "
                "не указаны ocr_value_types"
            )

    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        candidates: list[EntityCandidate] = []

        seen: set[
            tuple[int, str, tuple[str, ...]]
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

            value_words, normalized_value = search_result

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
                line=line,
                value_words=value_words,
                normalized_value=normalized_value,
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
            .replace("\\", "/")
        )

        normalized = re.sub(
            r"/+",
            "/",
            normalized,
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

        if anchor_gap > self.config.max_anchor_gap:
            return None

        maximum_word_count = min(
            self.config.max_value_words,
            len(words_after_anchor),
        )

        matches: list[
            tuple[
                int,
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

            if not self.allowed_token_pattern.fullmatch(
                candidate_text
            ):
                break

            normalized_value = self._normalize_value(
                candidate_text
            )

            if not normalized_value:
                continue

            if self._is_valid_value(
                normalized_value
            ):
                matches.append(
                    (
                        len(normalized_value),
                        len(candidate_words),
                        candidate_words,
                        normalized_value,
                    )
                )

        if not matches:
            return None

        _, _, best_words, best_value = max(
            matches,
            key=lambda item: (
                item[0],
                item[1],
            ),
        )

        return best_words, best_value

    def _build_candidate(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
        anchor: OCRWord,
        line: LayoutLine,
        value_words: tuple[OCRWord, ...],
        normalized_value: str,
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
                    f"значение соответствует формату "
                    f"{self.display_name}"
                ),
                score_delta=format_score,
                data=self._validation_data(
                    normalized_value
                ),
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
                    "от банковского ключа"
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
                        "банковский код распознан "
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
                    normalized_value=normalized_value,
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

        validation_data = self._validation_data(
            normalized_value
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
                **validation_data,
            },
            evidence=tuple(evidence),
            metadata={
                "display_name": self.display_name,
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
                not in self.ocr_value_types
            ):
                continue

            normalized_ocr_value = self._normalize_value(
                ocr_value.value
            )

            if normalized_ocr_value != normalized_value:
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
    def _normalize_value(
        cls,
        value: str,
    ) -> str:

        normalized = value.upper()

        return cls._non_alphanumeric_pattern.sub(
            "",
            normalized,
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

    def _is_valid_value(
        self,
        value: str,
    ) -> bool:
        raise NotImplementedError

    def _validation_data(
        self,
        value: str,
    ) -> dict[str, object]:
        raise NotImplementedError


class BIKRule(_AnchoredBankCodeRule):

    rule_id = "bank.bik.anchor_right.v1"
    entity_type = "bik"

    display_name = "БИК"
    format_name = "bank_identifier_code"

    anchor_aliases = frozenset(
        {
            "бик",
            "bik",
            "мфо",
        }
    )

    ocr_value_types = frozenset(
        {
            "bik",
        }
    )

    allowed_token_pattern = re.compile(
        r"^[A-Za-z0-9\s.,\-]+$"
    )

    _value_pattern = re.compile(
        r"^[A-Z0-9]{6,11}$"
    )

    def _is_valid_value(
        self,
        value: str,
    ) -> bool:
        return bool(
            self._value_pattern.fullmatch(
                value
            )
        )

    def _validation_data(
        self,
        value: str,
    ) -> dict[str, object]:
        return {
            "code_length": len(value),
            "character_set": (
                "numeric"
                if value.isdigit()
                else "latin_alphanumeric"
            ),
        }


class BICSWIFTRule(_AnchoredBankCodeRule):

    rule_id = "bank.bic_swift.anchor_right.v1"
    entity_type = "bic_swift"

    display_name = "SWIFT/BIC"
    format_name = "swift_bic"

    anchor_aliases = frozenset(
        {
            "swift",
            "bic",
            "бик/swift",
            "swift/бик",
            "bik/swift",
            "swift/bik",
            "bic/swift",
            "swift/bic",
        }
    )

    ocr_value_types = frozenset(
        {
            "bic_swift",
            "swift",
            "bic",
        }
    )

    allowed_token_pattern = re.compile(
        r"^[A-Za-z0-9\s.,\-]+$"
    )

    _value_pattern = re.compile(
        r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}"
        r"(?:[A-Z0-9]{3})?$"
    )

    def _is_valid_value(
        self,
        value: str,
    ) -> bool:
        return bool(
            self._value_pattern.fullmatch(
                value
            )
        )

    def _validation_data(
        self,
        value: str,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "code_length": len(value),
            "bank_code": value[:4],
            "country_code": value[4:6],
            "location_code": value[6:8],
        }

        if len(value) == 11:
            result["branch_code"] = value[8:11]
        else:
            result["branch_code"] = None

        return result


@dataclass(frozen=True, slots=True)
class AccountRuleConfig:

    max_anchor_gap: float = 0.12
    max_value_words: int = 5
    min_candidate_confidence: float = 0.45
    use_ocr_value_confirmation: bool = True
    allow_ocr_character_corrections: bool = True
    min_generic_length: int = 6
    max_generic_length: int = 34

    def __post_init__(self) -> None:
        if self.max_anchor_gap < 0:
            raise ValueError(
                "max_anchor_gap не может быть отрицательным"
            )

        if self.max_value_words < 1:
            raise ValueError(
                "max_value_words должен быть не меньше 1"
            )

        if not 0.0 <= self.min_candidate_confidence <= 1.0:
            raise ValueError(
                "min_candidate_confidence должен находиться "
                "в диапазоне от 0.0 до 1.0"
            )

        if self.min_generic_length < 1:
            raise ValueError(
                "min_generic_length должен быть больше 0"
            )

        if (
            self.max_generic_length
            < self.min_generic_length
        ):
            raise ValueError(
                "max_generic_length не может быть меньше "
                "min_generic_length"
            )


KZIBANRuleConfig = AccountRuleConfig
BankAccountRuleConfig = AccountRuleConfig


@dataclass(frozen=True, slots=True)
class _AccountAnchorMatch:

    line: LayoutLine
    words: tuple[OCRWord, ...]
    start_position: int
    end_position: int
    normalized_text: str

    @property
    def first_word(self) -> OCRWord:
        return self.words[0]

    @property
    def last_word(self) -> OCRWord:
        return self.words[-1]

    @property
    def word_ids(self) -> tuple[str, ...]:
        return tuple(
            word.id
            for word in self.words
        )


class _AnchoredAccountRule(ExtractionRule):

    rule_id = ""
    entity_type = ""

    display_name = ""
    format_name = ""

    anchor_aliases: tuple[
        tuple[str, ...],
        ...
    ] = ()

    ocr_value_types: frozenset[str] = frozenset()

    _allowed_value_pattern = re.compile(
        r"^[A-Za-zА-Яа-яЁё0-9"
        r"\s.,\-_/]+$"
    )

    _non_alphanumeric_pattern = re.compile(
        r"[^A-Z0-9]+"
    )

    _anchor_strip_characters = (
        " \t\r\n"
        ":;,.!?"
        "()[]{}"
        "«»\"'"
    )

    _value_prefixes = frozenset(
        {
            "№",
            "n",
            "no",
        }
    )

    def __init__(
        self,
        config: AccountRuleConfig | None = None,
    ) -> None:
        self.config = config or AccountRuleConfig()

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
                "не указаны anchor_aliases"
            )

        if not self.ocr_value_types:
            raise ValueError(
                f"У правила {self.__class__.__name__} "
                "не указаны ocr_value_types"
            )

    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        candidates: list[EntityCandidate] = []

        seen: set[
            tuple[int, str, tuple[str, ...]]
        ] = set()

        for line in spatial.lines:
            anchor_matches = self._find_anchor_matches(
                line
            )

            for anchor_match in anchor_matches:
                search_result = self._find_value_words(
                    anchor_match
                )

                if search_result is None:
                    continue

                (
                    value_words,
                    normalized_value,
                    corrections,
                ) = search_result

                word_ids = tuple(
                    word.id
                    for word in value_words
                )

                deduplication_key = (
                    line.page,
                    normalized_value,
                    word_ids,
                )

                if deduplication_key in seen:
                    continue

                seen.add(deduplication_key)

                candidate = self._build_candidate(
                    document=document,
                    spatial=spatial,
                    anchor_match=anchor_match,
                    value_words=value_words,
                    normalized_value=normalized_value,
                    corrections=corrections,
                )

                if (
                    candidate.confidence
                    < self.config.min_candidate_confidence
                ):
                    continue

                candidates.append(candidate)

        return tuple(candidates)

    def _find_anchor_matches(
        self,
        line: LayoutLine,
    ) -> tuple[_AccountAnchorMatch, ...]:

        normalized_words = tuple(
            self._normalize_anchor_token(
                word.search_text
            )
            for word in line.words
        )

        sorted_aliases = sorted(
            self.anchor_aliases,
            key=len,
            reverse=True,
        )

        matches: list[_AccountAnchorMatch] = []

        position = 0

        while position < len(line.words):
            matched_alias: tuple[str, ...] | None = None

            for alias in sorted_aliases:
                alias_length = len(alias)

                candidate_tokens = normalized_words[
                    position:
                    position + alias_length
                ]

                if candidate_tokens == alias:
                    matched_alias = alias
                    break

            if matched_alias is None:
                position += 1
                continue

            end_position = (
                position
                + len(matched_alias)
                - 1
            )

            anchor_words = tuple(
                line.words[
                    position:
                    end_position + 1
                ]
            )

            matches.append(
                _AccountAnchorMatch(
                    line=line,
                    words=anchor_words,
                    start_position=position,
                    end_position=end_position,
                    normalized_text=" ".join(
                        matched_alias
                    ),
                )
            )

            position = end_position + 1

        return tuple(matches)

    def _find_value_words(
        self,
        anchor_match: _AccountAnchorMatch,
    ) -> tuple[
        tuple[OCRWord, ...],
        str,
        tuple[str, ...],
    ] | None:

        line = anchor_match.line

        value_start = (
            anchor_match.end_position + 1
        )

        while value_start < len(line.words):
            prefix = self._normalize_prefix_token(
                line.words[
                    value_start
                ].search_text
            )

            if prefix not in self._value_prefixes:
                break

            value_start += 1

        if value_start >= len(line.words):
            return None

        first_value_word = line.words[
            value_start
        ]

        anchor_gap = (
            anchor_match.last_word
            .bbox
            .horizontal_gap(
                first_value_word.bbox
            )
        )

        if anchor_gap > self.config.max_anchor_gap:
            return None

        words_after_anchor = line.words[
            value_start:
        ]

        maximum_word_count = min(
            self.config.max_value_words,
            len(words_after_anchor),
        )

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

            (
                normalized_value,
                corrections,
            ) = self._normalize_value_words(
                candidate_words
            )

            if not normalized_value:
                continue

            if self._is_valid_value(
                normalized_value
            ):
                return (
                    candidate_words,
                    normalized_value,
                    corrections,
                )

        return None

    def _build_candidate(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
        anchor_match: _AccountAnchorMatch,
        value_words: tuple[OCRWord, ...],
        normalized_value: str,
        corrections: tuple[str, ...],
    ) -> EntityCandidate:
        line = anchor_match.line

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

        anchor_gap = (
            anchor_match.last_word
            .bbox
            .horizontal_gap(
                value_words[0].bbox
            )
        )

        evidence: list[EntityEvidence] = []

        confidence = 0.15

        anchor_score = 0.30

        evidence.append(
            EntityEvidence(
                kind="anchor",
                description=(
                    f"найден якорь {self.display_name} "
                    "перед значением"
                ),
                score_delta=anchor_score,
                data={
                    "anchor_text": (
                        anchor_match.normalized_text
                    ),
                    "anchor_word_ids": list(
                        anchor_match.word_ids
                    ),
                },
            )
        )

        confidence += anchor_score

        validation_data = self._validation_data(
            normalized_value
        )

        format_score = 0.25

        evidence.append(
            EntityEvidence(
                kind="format",
                description=(
                    f"значение соответствует формату "
                    f"{self.display_name}"
                ),
                score_delta=format_score,
                data=validation_data,
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
                        "счёт распознан одним OCR-словом"
                    ),
                    score_delta=compact_score,
                    data={
                        "word_count": 1,
                    },
                )
            )

            confidence += compact_score

        if corrections:
            evidence.append(
                EntityEvidence(
                    kind="ocr_correction",
                    description=(
                        "при нормализации сохранены "
                        "исправления OCR"
                    ),
                    score_delta=0.0,
                    data={
                        "corrections": list(
                            corrections
                        ),
                    },
                )
            )

        matching_ocr_values: tuple[
            OCRValue,
            ...
        ] = ()

        if self.config.use_ocr_value_confirmation:
            matching_ocr_values = (
                self._matching_ocr_values(
                    document=document,
                    value_words=value_words,
                    normalized_value=normalized_value,
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

        checksum_valid = validation_data.get(
            "checksum_valid"
        )

        if checksum_valid is True:
            checksum_score = 0.10

            evidence.append(
                EntityEvidence(
                    kind="checksum",
                    description=(
                        "контрольная сумма IBAN корректна"
                    ),
                    score_delta=checksum_score,
                    data={
                        "checksum_valid": True,
                    },
                )
            )

            confidence += checksum_score

        elif checksum_valid is False:
            checksum_penalty = -0.08

            evidence.append(
                EntityEvidence(
                    kind="checksum",
                    description=(
                        "форма IBAN корректна, но контрольная "
                        "сумма не прошла проверку"
                    ),
                    score_delta=checksum_penalty,
                    data={
                        "checksum_valid": False,
                    },
                )
            )

            confidence += checksum_penalty

        confidence = max(
            0.0,
            min(1.0, confidence),
        )

        first_word = value_words[0]
        last_word = value_words[-1]

        candidate_id = (
            f"candidate-{self.entity_type}-"
            f"p{line.page}-"
            f"w{first_word.index}-"
            f"{last_word.index}"
        )

        context_text = spatial.context_text(
            anchor_match.first_word,
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
            page=line.page,
            bbox=value_bbox,
            word_ids=tuple(
                word.id
                for word in value_words
            ),
            confidence=confidence,
            rule_id=self.rule_id,
            region=line.region,
            line_id=line.id,
            anchor_word_ids=(
                anchor_match.word_ids
            ),
            validation={
                "format": self.format_name,
                "status": self._validation_status(
                    normalized_value
                ),
                "shape": True,
                **validation_data,
            },
            evidence=tuple(evidence),
            metadata={
                "display_name": self.display_name,
                "anchor_text": (
                    anchor_match.normalized_text
                ),
                "anchor_bbox": (
                    self._union_word_boxes(
                        anchor_match.words
                    ).to_list()
                ),
                "context_text": context_text,
                "value_word_count": len(
                    value_words
                ),
                "corrections": list(
                    corrections
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
                not in self.ocr_value_types
            ):
                continue

            (
                normalized_ocr_value,
                _,
            ) = self._normalize_text(
                ocr_value.value
            )

            if normalized_ocr_value != normalized_value:
                continue

            matches.append(ocr_value)

        return tuple(matches)

    def _normalize_value_words(
        self,
        words: tuple[OCRWord, ...],
    ) -> tuple[str, tuple[str, ...]]:
        source_text = " ".join(
            word.search_text
            for word in words
        )

        normalized_value, corrections = (
            self._normalize_text(
                source_text
            )
        )

        correction_items = list(corrections)

        for word in words:
            if not word.normalized_text:
                continue

            raw_compact = self._compact_text(
                word.text
            )

            normalized_compact = self._compact_text(
                word.normalized_text
            )

            if raw_compact == normalized_compact:
                continue

            correction_items.append(
                f"{word.id}:"
                f"{raw_compact}→"
                f"{normalized_compact}"
            )

        unique_corrections = tuple(
            dict.fromkeys(
                correction_items
            )
        )

        return (
            normalized_value,
            unique_corrections,
        )

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
    def _normalize_anchor_token(
        cls,
        value: str,
    ) -> str:
        normalized = (
            value
            .casefold()
            .strip(
                cls._anchor_strip_characters
            )
            .replace("\\", "/")
        )

        return re.sub(
            r"/+",
            "/",
            normalized,
        )

    @classmethod
    def _normalize_prefix_token(
        cls,
        value: str,
    ) -> str:
        return (
            value
            .casefold()
            .strip(
                cls._anchor_strip_characters
            )
        )

    @classmethod
    def _compact_text(
        cls,
        value: str,
    ) -> str:
        return cls._non_alphanumeric_pattern.sub(
            "",
            value.upper(),
        )

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

    def _normalize_text(
        self,
        value: str,
    ) -> tuple[str, tuple[str, ...]]:
        raise NotImplementedError

    def _is_valid_value(
        self,
        value: str,
    ) -> bool:
        raise NotImplementedError

    def _validation_data(
        self,
        value: str,
    ) -> dict[str, object]:
        raise NotImplementedError

    def _validation_status(
        self,
        value: str,
    ) -> str:
        raise NotImplementedError


class KZIBANRule(_AnchoredAccountRule):
    rule_id = "bank.iban.kz.anchor_right.v1"
    entity_type = "iban"

    display_name = "IBAN"
    format_name = "kz_iban"

    anchor_aliases = (
        ("иик",),
        ("iban",),
        ("р/с",),
        ("р/счет",),
        ("р/счёт",),
        ("расчетный", "счет"),
        ("расчётный", "счёт"),
        ("банковский", "счет"),
        ("банковский", "счёт"),
    )

    ocr_value_types = frozenset(
        {
            "account",
            "iban",
        }
    )

    _iban_pattern = re.compile(
        r"^KZ\d{2}[A-Z0-9]{16}$"
    )

    _digit_corrections = {
        "O": "0",
        "I": "1",
        "L": "1",
        "S": "5",
        "B": "8",
    }

    def _normalize_text(
        self,
        value: str,
    ) -> tuple[str, tuple[str, ...]]:
        compact = self._compact_text(
            value
        )

        if (
            not self.config
            .allow_ocr_character_corrections
        ):
            return compact, ()

        characters = list(compact)
        corrections: list[str] = []
        for position in (2, 3):
            if position >= len(characters):
                continue

            current = characters[position]

            replacement = self._digit_corrections.get(
                current
            )

            if replacement is None:
                continue

            characters[position] = replacement

            corrections.append(
                f"position_{position + 1}:"
                f"{current}→{replacement}"
            )

        return (
            "".join(characters),
            tuple(corrections),
        )

    def _is_valid_value(
        self,
        value: str,
    ) -> bool:
        return bool(
            self._iban_pattern.fullmatch(
                value
            )
        )

    def _validation_data(
        self,
        value: str,
    ) -> dict[str, object]:
        return {
            "country_code": value[:2],
            "check_digits": value[2:4],
            "bban": value[4:],
            "code_length": len(value),
            "checksum_valid": (
                self._is_valid_iban_checksum(
                    value
                )
            ),
        }

    def _validation_status(
        self,
        value: str,
    ) -> str:
        if self._is_valid_iban_checksum(
            value
        ):
            return "valid"

        return "checksum_invalid"

    @staticmethod
    def _is_valid_iban_checksum(
        value: str,
    ) -> bool:
        if len(value) < 4:
            return False

        rearranged = (
            value[4:]
            + value[:4]
        )

        remainder = 0

        for character in rearranged:
            if character.isdigit():
                digits = character
            elif "A" <= character <= "Z":
                digits = str(
                    ord(character)
                    - ord("A")
                    + 10
                )
            else:
                return False

            for digit in digits:
                remainder = (
                    remainder * 10
                    + int(digit)
                ) % 97

        return remainder == 1


class BankAccountRule(_AnchoredAccountRule):

    rule_id = "bank.account.anchor_right.v1"
    entity_type = "bank_account"

    display_name = "банковский счёт"
    format_name = "generic_bank_account"

    anchor_aliases = (
        ("р/с",),
        ("р/счет",),
        ("р/счёт",),
        ("account",),
        ("счет",),
        ("счёт",),
        ("расчетный", "счет"),
        ("расчётный", "счёт"),
        ("банковский", "счет"),
        ("банковский", "счёт"),
        ("корреспондентский", "счет"),
        ("корреспондентский", "счёт"),
        ("корр", "счет"),
        ("корр", "счёт"),
        ("к/с",),
    )

    ocr_value_types = frozenset(
        {
            "account",
            "bank_account",
        }
    )

    _generic_pattern = re.compile(
        r"^[A-Z0-9]+$"
    )

    _iban_like_pattern = re.compile(
        r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$"
    )

    def _normalize_text(
        self,
        value: str,
    ) -> tuple[str, tuple[str, ...]]:
        return self._compact_text(value), ()

    def _is_valid_value(
        self,
        value: str,
    ) -> bool:
        if not self._generic_pattern.fullmatch(
            value
        ):
            return False

        if not (
            self.config.min_generic_length
            <= len(value)
            <= self.config.max_generic_length
        ):
            return False
        if self._iban_like_pattern.fullmatch(
            value
        ):
            return False

        digit_count = sum(
            character.isdigit()
            for character in value
        )
        if digit_count < 4:
            return False

        return True

    def _validation_data(
        self,
        value: str,
    ) -> dict[str, object]:
        digit_count = sum(
            character.isdigit()
            for character in value
        )

        letter_count = sum(
            character.isalpha()
            for character in value
        )

        return {
            "code_length": len(value),
            "digit_count": digit_count,
            "letter_count": letter_count,
            "strict": False,
            "checksum_valid": None,
        }

    def _validation_status(
        self,
        value: str,
    ) -> str:
        return "unverified"
