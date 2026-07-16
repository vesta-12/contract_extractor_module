from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean
from contract_extractor.layout import (
    SpatialSearch,
)
from contract_extractor.models import (
    BoundingBox,
    EntityCandidate,
    EntityEvidence,
    LayoutLine,
    OCRDocument,
    OCRWord,
)
from contract_extractor.rules.base import (
    ExtractionRule,
)


@dataclass(frozen=True, slots=True)
class AddressRuleConfig:

    min_candidate_confidence: float = 0.45

    max_address_lines: int = 3

    min_address_score: int = 2

    def __post_init__(self) -> None:
        if not (
            0.0
            <= self.min_candidate_confidence
            <= 1.0
        ):
            raise ValueError(
                "min_candidate_confidence должен находиться "
                "в диапазоне от 0.0 до 1.0"
            )

        if self.max_address_lines < 1:
            raise ValueError(
                "max_address_lines должен быть не меньше 1"
            )

        if self.min_address_score < 1:
            raise ValueError(
                "min_address_score должен быть не меньше 1"
            )


class AddressRule(ExtractionRule):
    rule_id = "legal.address.context.v1"

    entity_type = "address"

    _anchor_aliases: tuple[
        tuple[str, ...],
        ...
    ] = (
        (
            "юридический",
            "адрес",
        ),
        (
            "почтовый",
            "адрес",
        ),
        (
            "фактический",
            "адрес",
        ),
        (
            "адрес",
            "местонахождения",
        ),
        (
            "место",
            "нахождения",
        ),
        (
            "registered",
            "address",
        ),
        (
            "legal",
            "address",
        ),
        (
            "address",
        ),
        (
            "адрес",
        ),
        (
            "местонахождение",
        ),
    )

    _address_tokens = frozenset(
        {
            "республика",
            "область",
            "район",
            "город",
            "г",
            "улица",
            "ул",
            "проспект",
            "пр",
            "переулок",
            "пер",
            "шоссе",
            "набережная",
            "дом",
            "д",
            "корпус",
            "корп",
            "строение",
            "стр",
            "квартира",
            "кв",
            "офис",
            "помещение",
            "микрорайон",
            "мкр",
            "село",
            "аул",
            "поселок",
            "посёлок",
            "city",
            "street",
            "avenue",
            "road",
            "building",
            "office",
            "district",
            "region",
        }
    )

    _country_tokens = frozenset(
        {
            "казахстан",
            "россия",
            "кыргызстан",
            "узбекистан",
            "республика",
            "kz",
            "ru",
            "kg",
        }
    )

    _stop_tokens = frozenset(
        {
            "бин",
            "иин",
            "инн",
            "бик",
            "swift",
            "bic",
            "iban",
            "иик",
            "р/с",
            "к/с",
            "счет",
            "счёт",
            "account",
            "банк",
            "bank",
            "телефон",
            "тел",
            "email",
            "e-mail",
            "директор",
            "подпись",
        }
    )

    _postal_code_pattern = re.compile(
        r"^\d{5,6}$"
    )

    _strip_characters = (
        " \t\r\n"
        ":;,.!?"
        "()[]{}"
        "«»\"'"
    )

    def __init__(
        self,
        config: AddressRuleConfig | None = None,
    ) -> None:
        self.config = (
            config
            or AddressRuleConfig()
        )

    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        groups: dict[
            tuple[int, str | None],
            list[LayoutLine],
        ] = defaultdict(list)

        for line in spatial.lines:
            groups[
                (
                    line.page,
                    line.region,
                )
            ].append(line)

        candidates: list[
            EntityCandidate
        ] = []

        seen_word_sets: set[
            tuple[str, ...]
        ] = set()

        for group_lines in groups.values():
            sorted_lines = sorted(
                group_lines,
                key=lambda line: (
                    line.bbox.y1,
                    line.bbox.x1,
                ),
            )

            for line_position, line in enumerate(
                sorted_lines
            ):
                anchor_result = (
                    self._find_anchor(line)
                )

                if anchor_result is not None:
                    (
                        anchor_start,
                        anchor_end,
                        anchor_text,
                    ) = anchor_result

                    address_words = (
                        self._collect_anchored_address(
                            lines=sorted_lines,
                            line_position=(
                                line_position
                            ),
                            anchor_end=anchor_end,
                        )
                    )

                    anchor_word_ids = tuple(
                        word.id
                        for word in line.words[
                            anchor_start:
                            anchor_end + 1
                        ]
                    )

                    role_hint = (
                        self._address_role_from_anchor(
                            anchor_text
                        )
                    )

                    explicit_anchor = True

                else:
                    if (
                        self._address_score(line)
                        < self.config.min_address_score
                    ):
                        continue

                    address_words = (
                        self._collect_address_lines(
                            lines=sorted_lines,
                            line_position=(
                                line_position
                            ),
                            first_line_words=(
                                line.words
                            ),
                        )
                    )

                    anchor_word_ids = ()
                    role_hint = "address"
                    explicit_anchor = False

                if not address_words:
                    continue

                word_ids = tuple(
                    word.id
                    for word in address_words
                )

                if word_ids in seen_word_sets:
                    continue

                seen_word_sets.add(
                    word_ids
                )

                value = self._clean_value(
                    address_words
                )

                if not value:
                    continue

                candidate = self._build_candidate(
                    spatial=spatial,
                    first_line=line,
                    words=address_words,
                    value=value,
                    anchor_word_ids=(
                        anchor_word_ids
                    ),
                    role_hint=role_hint,
                    explicit_anchor=(
                        explicit_anchor
                    ),
                )

                if (
                    candidate.confidence
                    < self.config.min_candidate_confidence
                ):
                    continue

                candidates.append(candidate)

        return tuple(candidates)

    def _find_anchor(
        self,
        line: LayoutLine,
    ) -> tuple[int, int, str] | None:
        tokens = tuple(
            self._normalize_token(
                word.search_text
            )
            for word in line.words
        )

        aliases = sorted(
            self._anchor_aliases,
            key=len,
            reverse=True,
        )

        for start in range(len(tokens)):
            for alias in aliases:
                end = start + len(alias)

                if tokens[start:end] != alias:
                    continue

                return (
                    start,
                    end - 1,
                    " ".join(alias),
                )

        return None

    def _collect_anchored_address(
        self,
        lines: list[LayoutLine],
        line_position: int,
        anchor_end: int,
    ) -> tuple[OCRWord, ...]:
        line = lines[line_position]

        first_line_words = tuple(
            line.words[
                anchor_end + 1:
            ]
        )

        return self._collect_address_lines(
            lines=lines,
            line_position=line_position,
            first_line_words=first_line_words,
            allow_empty_first_line=True,
        )

    def _collect_address_lines(
        self,
        lines: list[LayoutLine],
        line_position: int,
        first_line_words: tuple[
            OCRWord,
            ...
        ],
        allow_empty_first_line: bool = False,
    ) -> tuple[OCRWord, ...]:
        result: list[OCRWord] = []

        if first_line_words:
            result.extend(
                first_line_words
            )
        elif not allow_empty_first_line:
            return ()

        used_line_count = 1

        next_position = (
            line_position + 1
        )

        while (
            next_position < len(lines)
            and used_line_count
            < self.config.max_address_lines
        ):
            next_line = lines[
                next_position
            ]

            if self._contains_stop_token(
                next_line
            ):
                break

            score = self._address_score(
                next_line
            )

            if score < 1:
                break

            result.extend(
                next_line.words
            )

            used_line_count += 1
            next_position += 1

        return tuple(result)

    def _address_score(
        self,
        line: LayoutLine,
    ) -> int:
        score = 0

        tokens = tuple(
            self._normalize_token(
                word.search_text
            )
            for word in line.words
        )

        if any(
            self._postal_code_pattern.fullmatch(
                token
            )
            for token in tokens
        ):
            score += 2

        address_marker_count = sum(
            token in self._address_tokens
            for token in tokens
        )

        score += min(
            3,
            address_marker_count,
        )

        if any(
            token in self._country_tokens
            for token in tokens
        ):
            score += 1

        if any(
            "/" in word.search_text
            and any(
                character.isdigit()
                for character
                in word.search_text
            )
            for word in line.words
        ):
            score += 1

        return score

    def _contains_stop_token(
        self,
        line: LayoutLine,
    ) -> bool:
        return any(
            self._normalize_token(
                word.search_text
            )
            in self._stop_tokens
            for word in line.words
        )

    def _build_candidate(
        self,
        spatial: SpatialSearch,
        first_line: LayoutLine,
        words: tuple[OCRWord, ...],
        value: str,
        anchor_word_ids: tuple[str, ...],
        role_hint: str,
        explicit_anchor: bool,
    ) -> EntityCandidate:
        average_ocr_confidence = mean(
            word.confidence
            for word in words
        )

        confidence = 0.20

        evidence: list[
            EntityEvidence
        ] = []

        if explicit_anchor:
            anchor_score = 0.35

            evidence.append(
                EntityEvidence(
                    kind="address_anchor",
                    description=(
                        "найден явный ключ адреса"
                    ),
                    score_delta=anchor_score,
                    data={
                        "anchor_word_ids": list(
                            anchor_word_ids
                        ),
                    },
                )
            )

            confidence += anchor_score

        address_score = (
            self._address_words_score(
                words
            )
        )

        shape_score = min(
            0.30,
            address_score * 0.06,
        )

        evidence.append(
            EntityEvidence(
                kind="address_shape",
                description=(
                    "фрагмент содержит признаки адреса"
                ),
                score_delta=shape_score,
                data={
                    "address_score": address_score,
                },
            )
        )

        confidence += shape_score

        ocr_score = (
            average_ocr_confidence * 0.10
        )

        evidence.append(
            EntityEvidence(
                kind="ocr_confidence",
                description=(
                    "учтена средняя уверенность OCR"
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

        postal_code = (
            self._find_postal_code(words)
        )

        first_word = words[0]
        last_word = words[-1]

        candidate_id = (
            f"candidate-address-"
            f"p{first_line.page}-"
            f"w{first_word.index}-"
            f"{last_word.index}"
        )

        line_ids: list[str] = []

        for word in words:
            word_line = spatial.line_for_word(
                word
            )

            if (
                word_line is not None
                and word_line.id
                not in line_ids
            ):
                line_ids.append(
                    word_line.id
                )

        return EntityCandidate(
            id=candidate_id,
            entity_type=self.entity_type,
            value=value,
            raw_value=" ".join(
                word.text
                for word in words
            ),
            page=first_line.page,
            bbox=self._union_boxes(
                words
            ),
            word_ids=tuple(
                word.id
                for word in words
            ),
            confidence=min(
                1.0,
                confidence,
            ),
            rule_id=self.rule_id,
            region=first_line.region,
            line_id=first_line.id,
            anchor_word_ids=(
                anchor_word_ids
            ),
            validation={
                "format": "postal_address",
                "status": "context_match",
                "postal_code": postal_code,
                "word_count": len(words),
            },
            evidence=tuple(evidence),
            metadata={
                "role_hint": role_hint,
                "line_ids": line_ids,
                "context_text": (
                    spatial.context_text(
                        first_word,
                        before=1,
                        after=1,
                        same_region=True,
                        normalized=True,
                    )
                ),
            },
        )

    def _address_words_score(
        self,
        words: tuple[OCRWord, ...],
    ) -> int:
        fake_line = tuple(
            self._normalize_token(
                word.search_text
            )
            for word in words
        )

        score = sum(
            token in self._address_tokens
            for token in fake_line
        )

        if any(
            self._postal_code_pattern.fullmatch(
                token
            )
            for token in fake_line
        ):
            score += 2

        return score

    def _find_postal_code(
        self,
        words: tuple[OCRWord, ...],
    ) -> str | None:
        for word in words:
            token = self._normalize_token(
                word.search_text
            )

            if self._postal_code_pattern.fullmatch(
                token
            ):
                return token

        return None

    @staticmethod
    def _address_role_from_anchor(
        anchor_text: str,
    ) -> str:
        if "юридический" in anchor_text:
            return "legal_address"

        if "почтовый" in anchor_text:
            return "postal_address"

        if "фактический" in anchor_text:
            return "actual_address"

        if (
            "registered" in anchor_text
            or "legal" in anchor_text
        ):
            return "legal_address"

        return "address"

    @classmethod
    def _normalize_token(
        cls,
        value: str,
    ) -> str:
        return (
            value
            .casefold()
            .replace("ё", "е")
            .strip(
                cls._strip_characters
            )
        )

    @staticmethod
    def _clean_value(
        words: tuple[OCRWord, ...],
    ) -> str:
        value = " ".join(
            word.text
            for word in words
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        ).strip()

        return value.strip(
            " \t\r\n,;:–—-"
        )

    @staticmethod
    def _union_boxes(
        words: tuple[OCRWord, ...],
    ) -> BoundingBox:
        result = words[0].bbox

        for word in words[1:]:
            result = result.union(
                word.bbox
            )

        return result