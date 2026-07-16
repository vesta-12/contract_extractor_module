from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from statistics import mean
from contract_extractor.layout import SpatialSearch
from contract_extractor.models import (
    BoundingBox,
    EntityCandidate,
    EntityEvidence,
    LayoutLine,
    OCRDocument,
    OCRWord,
)
from contract_extractor.rules.base import ExtractionRule

@dataclass(frozen=True, slots=True)
class DocumentValueRuleConfig:

    min_candidate_confidence: float = 0.45

    max_amount_words: int = 6

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

        if self.max_amount_words < 1:
            raise ValueError(
                "max_amount_words должен быть не меньше 1"
            )


DateRuleConfig = DocumentValueRuleConfig
MoneyAmountRuleConfig = DocumentValueRuleConfig
PercentageRuleConfig = DocumentValueRuleConfig


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


def _average_confidence(
    words: tuple[OCRWord, ...],
) -> float:
    if not words:
        return 0.0

    return mean(
        word.confidence
        for word in words
    )


class DateRule(ExtractionRule):

    rule_id = "document.date.patterns.v1"
    entity_type = "date"

    _numeric_date_pattern = re.compile(
        r"^(?P<day>\d{1,2})"
        r"[./\-]"
        r"(?P<month>\d{1,2})"
        r"[./\-]"
        r"(?P<year>\d{4})"
        r"(?:\s*г(?:ода)?\.?)?$",
        re.IGNORECASE,
    )

    _day_pattern = re.compile(
        r"^\d{1,2}$"
    )

    _year_pattern = re.compile(
        r"^\d{4}$"
    )

    _strip_characters = (
        " \t\r\n"
        ":;,.!?"
        "()[]{}"
        "«»\"'"
    )

    _months = {
        "январь": 1,
        "января": 1,
        "февраль": 2,
        "февраля": 2,
        "март": 3,
        "марта": 3,
        "апрель": 4,
        "апреля": 4,
        "май": 5,
        "мая": 5,
        "июнь": 6,
        "июня": 6,
        "июль": 7,
        "июля": 7,
        "август": 8,
        "августа": 8,
        "сентябрь": 9,
        "сентября": 9,
        "октябрь": 10,
        "октября": 10,
        "ноябрь": 11,
        "ноября": 11,
        "декабрь": 12,
        "декабря": 12,

        "қаңтар": 1,
        "ақпан": 2,
        "наурыз": 3,
        "сәуір": 4,
        "мамыр": 5,
        "маусым": 6,
        "шілде": 7,
        "тамыз": 8,
        "қыркүйек": 9,
        "қазан": 10,
        "қараша": 11,
        "желтоқсан": 12,

        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    def __init__(
        self,
        config: DateRuleConfig | None = None,
    ) -> None:
        self.config = (
            config
            or DateRuleConfig()
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

        for line in spatial.lines:
            numeric_candidates = (
                self._extract_numeric_dates(
                    line=line,
                    spatial=spatial,
                )
            )

            textual_candidates = (
                self._extract_textual_dates(
                    line=line,
                    spatial=spatial,
                )
            )

            for candidate in (
                *numeric_candidates,
                *textual_candidates,
            ):
                key = (
                    candidate.page,
                    candidate.value,
                    candidate.word_ids,
                )

                if key in seen:
                    continue

                seen.add(key)

                if (
                    candidate.confidence
                    < self.config.min_candidate_confidence
                ):
                    continue

                candidates.append(candidate)

        return tuple(candidates)

    def _extract_numeric_dates(
        self,
        line: LayoutLine,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        candidates: list[EntityCandidate] = []

        for word in line.words:
            normalized_word = (
                word.search_text
                .strip(self._strip_characters)
            )

            match = (
                self._numeric_date_pattern
                .fullmatch(normalized_word)
            )

            if match is None:
                continue

            day_value = int(
                match.group("day")
            )

            month_value = int(
                match.group("month")
            )

            year_value = int(
                match.group("year")
            )

            normalized_date = (
                self._normalize_calendar_date(
                    day=day_value,
                    month=month_value,
                    year=year_value,
                )
            )

            if normalized_date is None:
                continue

            candidate = self._build_candidate(
                words=(word,),
                line=line,
                spatial=spatial,
                normalized_date=normalized_date,
                day=day_value,
                month=month_value,
                year=year_value,
                date_format="numeric",
            )

            candidates.append(candidate)

        return tuple(candidates)

    def _extract_textual_dates(
        self,
        line: LayoutLine,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        candidates: list[EntityCandidate] = []

        words = line.words

        if len(words) < 3:
            return ()

        for position in range(
            len(words) - 2
        ):
            day_word = words[position]
            month_word = words[position + 1]
            year_word = words[position + 2]

            day_value = self._parse_day(
                day_word.search_text
            )

            if day_value is None:
                continue

            month_value = self._parse_month(
                month_word.search_text
            )

            if month_value is None:
                continue

            year_value = self._parse_year(
                year_word.search_text
            )

            if year_value is None:
                continue

            normalized_date = (
                self._normalize_calendar_date(
                    day=day_value,
                    month=month_value,
                    year=year_value,
                )
            )

            if normalized_date is None:
                continue

            candidate = self._build_candidate(
                words=(
                    day_word,
                    month_word,
                    year_word,
                ),
                line=line,
                spatial=spatial,
                normalized_date=normalized_date,
                day=day_value,
                month=month_value,
                year=year_value,
                date_format="textual",
            )

            candidates.append(candidate)

        return tuple(candidates)

    def _build_candidate(
        self,
        words: tuple[OCRWord, ...],
        line: LayoutLine,
        spatial: SpatialSearch,
        normalized_date: str,
        day: int,
        month: int,
        year: int,
        date_format: str,
    ) -> EntityCandidate:
        role_hint = self._detect_role_hint(
            line=line,
        )

        average_ocr_confidence = (
            _average_confidence(words)
        )

        evidence: list[EntityEvidence] = []

        confidence = 0.20

        pattern_score = 0.35

        evidence.append(
            EntityEvidence(
                kind="date_pattern",
                description=(
                    "найдена календарная дата "
                    "по текстовому или числовому шаблону"
                ),
                score_delta=pattern_score,
                data={
                    "date_format": date_format,
                },
            )
        )

        confidence += pattern_score

        calendar_score = 0.30

        evidence.append(
            EntityEvidence(
                kind="calendar_validation",
                description=(
                    "день, месяц и год образуют "
                    "корректную календарную дату"
                ),
                score_delta=calendar_score,
                data={
                    "day": day,
                    "month": month,
                    "year": year,
                },
            )
        )

        confidence += calendar_score

        if role_hint != "general_date":
            role_score = 0.05

            evidence.append(
                EntityEvidence(
                    kind="semantic_context",
                    description=(
                        "по контексту определена "
                        "предварительная роль даты"
                    ),
                    score_delta=role_score,
                    data={
                        "role_hint": role_hint,
                    },
                )
            )

            confidence += role_score

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

        confidence = min(
            1.0,
            confidence,
        )

        first_word = words[0]
        last_word = words[-1]

        candidate_id = (
            f"candidate-date-"
            f"p{line.page}-"
            f"w{first_word.index}-"
            f"{last_word.index}"
        )

        raw_value = " ".join(
            word.text
            for word in words
        )

        return EntityCandidate(
            id=candidate_id,
            entity_type=self.entity_type,
            value=normalized_date,
            raw_value=raw_value,
            page=line.page,
            bbox=_union_word_boxes(words),
            word_ids=tuple(
                word.id
                for word in words
            ),
            confidence=confidence,
            rule_id=self.rule_id,
            region=line.region,
            line_id=line.id,
            validation={
                "format": "iso_date",
                "status": "valid",
                "calendar_valid": True,
                "day": day,
                "month": month,
                "year": year,
                "date_format": date_format,
            },
            evidence=tuple(evidence),
            metadata={
                "role_hint": role_hint,
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

    def _detect_role_hint(
        self,
        line: LayoutLine,
    ) -> str:
        text = (
            line.normalized_text
            .casefold()
        )

        if "доверенност" in text:
            return "power_of_attorney_date"

        if (
            "срок до" in text
            or (
                "вернуть" in text
                and "года" in text
            )
            or "погашен" in text
        ):
            return "repayment_due_date"

        if (
            "начиная с" in text
            or "начисляются" in text
            or "начисление" in text
        ):
            return "interest_start_date"

        if (
            line.page == 1
            and line.index <= 5
        ):
            return "contract_date"

        return "general_date"

    def _parse_day(
        self,
        value: str,
    ) -> int | None:
        normalized = value.strip(
            self._strip_characters
        )

        if not self._day_pattern.fullmatch(
            normalized
        ):
            return None

        return int(normalized)

    def _parse_month(
        self,
        value: str,
    ) -> int | None:
        normalized = (
            value
            .casefold()
            .strip(self._strip_characters)
        )

        return self._months.get(
            normalized
        )

    def _parse_year(
        self,
        value: str,
    ) -> int | None:
        normalized = value.strip(
            self._strip_characters
        )

        if not self._year_pattern.fullmatch(
            normalized
        ):
            return None

        return int(normalized)

    @staticmethod
    def _normalize_calendar_date(
        day: int,
        month: int,
        year: int,
    ) -> str | None:
        try:
            parsed_date = date(
                year,
                month,
                day,
            )
        except ValueError:
            return None

        return parsed_date.isoformat()


class MoneyAmountRule(ExtractionRule):

    rule_id = "document.money_amount.currency.v1"
    entity_type = "money_amount"

    _amount_fragment_pattern = re.compile(
        r"^[\d\s.,'’]+$"
    )

    _strip_characters = (
        " \t\r\n"
        ":;,.!?"
        "()[]{}"
        "«»\"'"
    )

    _currency_aliases: tuple[
        tuple[tuple[str, ...], str],
        ...
    ] = (
        (("долларов", "сша"), "USD"),
        (("доллары", "сша"), "USD"),
        (("доллар", "сша"), "USD"),
        (("usd",), "USD"),
        (("$",), "USD"),
        (("долларов",), "USD"),
        (("доллара",), "USD"),
        (("доллар",), "USD"),

        (("kzt",), "KZT"),
        (("₸",), "KZT"),
        (("тенге",), "KZT"),

        (("kgs",), "KGS"),
        (("сомов",), "KGS"),
        (("сома",), "KGS"),
        (("сом",), "KGS"),

        (("rub",), "RUB"),
        (("рублей",), "RUB"),
        (("рубля",), "RUB"),
        (("руб",), "RUB"),
        (("₽",), "RUB"),

        (("eur",), "EUR"),
        (("евро",), "EUR"),
        (("€",), "EUR"),
    )

    def __init__(
        self,
        config: MoneyAmountRuleConfig | None = None,
    ) -> None:
        self.config = (
            config
            or MoneyAmountRuleConfig()
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
                str,
                tuple[str, ...],
            ]
        ] = set()

        for line in spatial.lines:
            currency_matches = (
                self._find_currency_matches(
                    line
                )
            )

            for (
                currency_start,
                currency_end,
                currency_code,
            ) in currency_matches:
                amount_words = (
                    self._find_amount_words(
                        line=line,
                        currency_start=(
                            currency_start
                        ),
                    )
                )

                if not amount_words:
                    continue

                normalized_result = (
                    self._normalize_amount(
                        amount_words
                    )
                )

                if normalized_result is None:
                    continue

                (
                    normalized_amount,
                    decimal_places,
                ) = normalized_result

                currency_words = tuple(
                    line.words[
                        currency_start:
                        currency_end + 1
                    ]
                )

                all_words = (
                    amount_words
                    + currency_words
                )

                key = (
                    line.page,
                    normalized_amount,
                    currency_code,
                    tuple(
                        word.id
                        for word in all_words
                    ),
                )

                if key in seen:
                    continue

                seen.add(key)

                candidate = self._build_candidate(
                    spatial=spatial,
                    line=line,
                    amount_words=amount_words,
                    currency_words=currency_words,
                    normalized_amount=(
                        normalized_amount
                    ),
                    decimal_places=(
                        decimal_places
                    ),
                    currency_code=currency_code,
                )

                if (
                    candidate.confidence
                    < self.config.min_candidate_confidence
                ):
                    continue

                candidates.append(candidate)

        return tuple(candidates)

    def _find_currency_matches(
        self,
        line: LayoutLine,
    ) -> tuple[
        tuple[int, int, str],
        ...
    ]:
        tokens = tuple(
            self._normalize_currency_token(
                word.search_text
            )
            for word in line.words
        )

        aliases = sorted(
            self._currency_aliases,
            key=lambda item: len(item[0]),
            reverse=True,
        )

        matches: list[
            tuple[int, int, str]
        ] = []

        position = 0

        while position < len(tokens):
            found: tuple[
                int,
                int,
                str,
            ] | None = None

            for alias, currency_code in aliases:
                end = (
                    position
                    + len(alias)
                )

                if tokens[position:end] != alias:
                    continue

                found = (
                    position,
                    end - 1,
                    currency_code,
                )

                break

            if found is None:
                position += 1
                continue

            matches.append(found)
            position = found[1] + 1

        return tuple(matches)

    def _find_amount_words(
        self,
        line: LayoutLine,
        currency_start: int,
    ) -> tuple[OCRWord, ...]:
        result: list[OCRWord] = []

        position = currency_start - 1

        while (
            position >= 0
            and len(result)
            < self.config.max_amount_words
        ):
            word = line.words[position]

            text = word.search_text.strip()

            if not text:
                break

            if not self._amount_fragment_pattern.fullmatch(
                text
            ):
                break

            if not any(
                character.isdigit()
                for character in text
            ):
                break

            result.append(word)
            position -= 1

        result.reverse()

        return tuple(result)

    def _normalize_amount(
        self,
        words: tuple[OCRWord, ...],
    ) -> tuple[str, int] | None:
        source = "".join(
            word.search_text
            for word in words
        )

        compact = re.sub(
            r"[^\d,.]",
            "",
            source,
        )

        if not compact:
            return None

        decimal_separator: str | None = None

        if "," in compact and "." in compact:
            last_comma = compact.rfind(",")
            last_dot = compact.rfind(".")

            latest_position = max(
                last_comma,
                last_dot,
            )

            suffix_length = (
                len(compact)
                - latest_position
                - 1
            )

            if 1 <= suffix_length <= 2:
                decimal_separator = (
                    ","
                    if last_comma > last_dot
                    else "."
                )

        elif "," in compact:
            last_position = compact.rfind(",")

            suffix_length = (
                len(compact)
                - last_position
                - 1
            )

            if (
                compact.count(",") == 1
                and 1 <= suffix_length <= 2
            ):
                decimal_separator = ","

            elif (
                compact.count(",") > 1
                and 1 <= suffix_length <= 2
            ):
                decimal_separator = ","

        elif "." in compact:
            last_position = compact.rfind(".")

            suffix_length = (
                len(compact)
                - last_position
                - 1
            )

            if (
                compact.count(".") == 1
                and 1 <= suffix_length <= 2
            ):
                decimal_separator = "."

            elif (
                compact.count(".") > 1
                and 1 <= suffix_length <= 2
            ):
                decimal_separator = "."

        decimal_places = 0

        if decimal_separator is None:
            normalized = re.sub(
                r"[,.]",
                "",
                compact,
            )

        else:
            separator_position = compact.rfind(
                decimal_separator
            )

            integer_part = re.sub(
                r"[,.]",
                "",
                compact[
                    :separator_position
                ],
            )

            fractional_part = re.sub(
                r"[,.]",
                "",
                compact[
                    separator_position + 1:
                ],
            )

            if not fractional_part:
                return None

            decimal_places = len(
                fractional_part
            )

            normalized = (
                f"{integer_part}."
                f"{fractional_part}"
            )

        try:
            decimal_value = Decimal(
                normalized
            )
        except InvalidOperation:
            return None

        if decimal_value < 0:
            return None

        if decimal_places > 0:
            normalized_value = format(
                decimal_value,
                f".{decimal_places}f",
            )
        else:
            normalized_value = format(
                decimal_value,
                "f",
            )

        return (
            normalized_value,
            decimal_places,
        )

    def _build_candidate(
        self,
        spatial: SpatialSearch,
        line: LayoutLine,
        amount_words: tuple[OCRWord, ...],
        currency_words: tuple[OCRWord, ...],
        normalized_amount: str,
        decimal_places: int,
        currency_code: str,
    ) -> EntityCandidate:
        all_words = (
            amount_words
            + currency_words
        )

        role_hint = self._detect_role_hint(
            line
        )

        average_ocr_confidence = (
            _average_confidence(
                all_words
            )
        )

        evidence: list[EntityEvidence] = []

        confidence = 0.20

        amount_score = 0.30

        evidence.append(
            EntityEvidence(
                kind="amount_format",
                description=(
                    "найдена корректная числовая "
                    "денежная сумма"
                ),
                score_delta=amount_score,
                data={
                    "amount": normalized_amount,
                    "decimal_places": (
                        decimal_places
                    ),
                },
            )
        )

        confidence += amount_score

        currency_score = 0.25

        evidence.append(
            EntityEvidence(
                kind="currency",
                description=(
                    "рядом с суммой найдена "
                    "поддерживаемая валюта"
                ),
                score_delta=currency_score,
                data={
                    "currency": currency_code,
                },
            )
        )

        confidence += currency_score

        if role_hint != "general_money_amount":
            role_score = 0.05

            evidence.append(
                EntityEvidence(
                    kind="semantic_context",
                    description=(
                        "по контексту определено "
                        "предварительное назначение суммы"
                    ),
                    score_delta=role_score,
                    data={
                        "role_hint": role_hint,
                    },
                )
            )

            confidence += role_score

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

        confidence = min(
            1.0,
            confidence,
        )

        first_word = all_words[0]
        last_word = all_words[-1]

        candidate_id = (
            f"candidate-money-"
            f"p{line.page}-"
            f"w{first_word.index}-"
            f"{last_word.index}"
        )

        return EntityCandidate(
            id=candidate_id,
            entity_type=self.entity_type,
            value=normalized_amount,
            raw_value=" ".join(
                word.text
                for word in all_words
            ),
            page=line.page,
            bbox=_union_word_boxes(
                all_words
            ),
            word_ids=tuple(
                word.id
                for word in all_words
            ),
            confidence=confidence,
            rule_id=self.rule_id,
            region=line.region,
            line_id=line.id,
            validation={
                "format": "decimal_money",
                "status": "valid",
                "amount": normalized_amount,
                "currency": currency_code,
                "decimal_places": (
                    decimal_places
                ),
            },
            evidence=tuple(evidence),
            metadata={
                "currency": currency_code,
                "amount_word_ids": [
                    word.id
                    for word in amount_words
                ],
                "currency_word_ids": [
                    word.id
                    for word in currency_words
                ],
                "role_hint": role_hint,
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

    @staticmethod
    def _detect_role_hint(
        line: LayoutLine,
    ) -> str:
        text = (
            line.normalized_text
            .casefold()
        )

        if (
            "сумма займа" in text
            or "размере" in text
            or "денежные средства" in text
        ):
            return "principal_amount"

        if "пеня" in text:
            return "penalty_amount"

        if (
            "процент" in text
            and "уплат" in text
        ):
            return "interest_amount"

        return "general_money_amount"

    def _normalize_currency_token(
        self,
        value: str,
    ) -> str:
        return (
            value
            .casefold()
            .strip(
                self._strip_characters
            )
        )


class PercentageRule(ExtractionRule):

    rule_id = "document.percentage.patterns.v1"
    entity_type = "percentage"

    _number_pattern = re.compile(
        r"^\d+(?:[.,]\d+)?$"
    )

    _inline_percentage_pattern = re.compile(
        r"^(?P<value>\d+(?:[.,]\d+)?)\s*%$"
    )

    _percentage_tokens = frozenset(
        {
            "%",
            "процент",
            "процента",
            "процентов",
            "percent",
            "percents",
        }
    )

    _strip_characters = (
        " \t\r\n"
        ":;,.!?"
        "()[]{}"
        "«»\"'"
    )

    def __init__(
        self,
        config: PercentageRuleConfig | None = None,
    ) -> None:
        self.config = (
            config
            or PercentageRuleConfig()
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

        for line in spatial.lines:
            detected = self._extract_from_line(
                line=line,
                spatial=spatial,
            )

            for candidate in detected:
                key = (
                    candidate.page,
                    candidate.value,
                    candidate.word_ids,
                )

                if key in seen:
                    continue

                seen.add(key)

                if (
                    candidate.confidence
                    < self.config.min_candidate_confidence
                ):
                    continue

                candidates.append(candidate)

        return tuple(candidates)

    def _extract_from_line(
        self,
        line: LayoutLine,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        candidates: list[EntityCandidate] = []

        words = line.words

        for position, word in enumerate(
            words
        ):
            normalized_word = (
                word.search_text.strip()
            )

            inline_match = (
                self._inline_percentage_pattern
                .fullmatch(normalized_word)
            )

            if inline_match is not None:
                normalized_value = (
                    self._normalize_number(
                        inline_match.group(
                            "value"
                        )
                    )
                )

                if normalized_value is None:
                    continue

                candidates.append(
                    self._build_candidate(
                        words=(word,),
                        line=line,
                        spatial=spatial,
                        normalized_value=(
                            normalized_value
                        ),
                    )
                )

                continue

            numeric_token = (
                normalized_word
                .strip(self._strip_characters)
            )

            if not self._number_pattern.fullmatch(
                numeric_token
            ):
                continue

            if position + 1 >= len(words):
                continue

            unit_word = words[position + 1]

            unit_token = (
                unit_word.search_text
                .casefold()
                .strip(self._strip_characters)
            )

            if (
                unit_token
                not in self._percentage_tokens
            ):
                continue

            normalized_value = (
                self._normalize_number(
                    numeric_token
                )
            )

            if normalized_value is None:
                continue

            candidates.append(
                self._build_candidate(
                    words=(
                        word,
                        unit_word,
                    ),
                    line=line,
                    spatial=spatial,
                    normalized_value=(
                        normalized_value
                    ),
                )
            )

        return tuple(candidates)

    def _build_candidate(
        self,
        words: tuple[OCRWord, ...],
        line: LayoutLine,
        spatial: SpatialSearch,
        normalized_value: str,
    ) -> EntityCandidate:
        role_hint = self._detect_role_hint(
            line
        )

        average_ocr_confidence = (
            _average_confidence(words)
        )

        evidence: list[EntityEvidence] = []

        confidence = 0.25

        pattern_score = 0.40

        evidence.append(
            EntityEvidence(
                kind="percentage_pattern",
                description=(
                    "найдено число с обозначением процента"
                ),
                score_delta=pattern_score,
                data={
                    "percentage": normalized_value,
                },
            )
        )

        confidence += pattern_score

        if role_hint != "general_percentage":
            role_score = 0.15

            evidence.append(
                EntityEvidence(
                    kind="semantic_context",
                    description=(
                        "по контексту определён тип "
                        "процентного значения"
                    ),
                    score_delta=role_score,
                    data={
                        "role_hint": role_hint,
                    },
                )
            )

            confidence += role_score

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

        confidence = min(
            1.0,
            confidence,
        )

        first_word = words[0]
        last_word = words[-1]

        candidate_id = (
            f"candidate-percentage-"
            f"p{line.page}-"
            f"w{first_word.index}-"
            f"{last_word.index}"
        )

        return EntityCandidate(
            id=candidate_id,
            entity_type=self.entity_type,
            value=normalized_value,
            raw_value=" ".join(
                word.text
                for word in words
            ),
            page=line.page,
            bbox=_union_word_boxes(words),
            word_ids=tuple(
                word.id
                for word in words
            ),
            confidence=confidence,
            rule_id=self.rule_id,
            region=line.region,
            line_id=line.id,
            validation={
                "format": "percentage",
                "status": "valid",
                "percentage": normalized_value,
                "unit": "percent",
            },
            evidence=tuple(evidence),
            metadata={
                "role_hint": role_hint,
                "period": (
                    "annual"
                    if role_hint
                    == "annual_interest_rate"
                    else None
                ),
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

    @staticmethod
    def _detect_role_hint(
        line: LayoutLine,
    ) -> str:
        text = (
            line.normalized_text
            .casefold()
        )

        if (
            "годовых" in text
            or "годовая" in text
            or "годовой" in text
        ):
            if (
                "ставк" in text
                or "процент" in text
                or "займ" in text
            ):
                return "annual_interest_rate"

        if "пен" in text:
            return "penalty_rate"

        if "комисси" in text:
            return "commission_rate"

        return "general_percentage"

    @staticmethod
    def _normalize_number(
        value: str,
    ) -> str | None:
        normalized = (
            value
            .replace(",", ".")
        )

        try:
            decimal_value = Decimal(
                normalized
            )
        except InvalidOperation:
            return None

        if decimal_value < 0:
            return None

        result = format(
            decimal_value,
            "f",
        )

        if "." in result:
            result = result.rstrip("0")
            result = result.rstrip(".")

        return result