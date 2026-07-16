from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass
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
class LegalPartyRuleConfig:

    min_candidate_confidence: float = 0.45
    max_organization_words: int = 10
    max_bank_words: int = 12
    max_bank_lines: int = 3
    max_person_words: int = 4
    max_person_line_lookahead: int = 2

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_candidate_confidence <= 1.0:
            raise ValueError(
                "min_candidate_confidence должен находиться "
                "в диапазоне от 0.0 до 1.0"
            )

        if self.max_organization_words < 2:
            raise ValueError(
                "max_organization_words должен быть не меньше 2"
            )

        if self.max_bank_words < 1:
            raise ValueError("max_bank_words должен быть не меньше 1")

        if self.max_bank_lines < 1:
            raise ValueError("max_bank_lines должен быть не меньше 1")

        if self.max_person_words < 1:
            raise ValueError("max_person_words должен быть не меньше 1")

        if self.max_person_line_lookahead < 0:
            raise ValueError(
                "max_person_line_lookahead не может быть отрицательным"
            )


OrganizationRuleConfig = LegalPartyRuleConfig
BankNameRuleConfig = LegalPartyRuleConfig
PersonNameRuleConfig = LegalPartyRuleConfig
PositionRuleConfig = LegalPartyRuleConfig


@dataclass(frozen=True, slots=True)
class _PhraseMatch:
    words: tuple[OCRWord, ...]
    start_position: int
    end_position: int
    canonical_value: str

    @property
    def word_ids(self) -> tuple[str, ...]:
        return tuple(word.id for word in self.words)


_STRIP_CHARACTERS = (
    " \t\r\n"
    ":;,.!?"
    "()[]{}"
    "«»\"'"
)

_PARTY_ROLE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lender", ("займодав", "кредитор")),
    ("borrower", ("заемщик", "заёмщик", "должник")),
    ("supplier", ("поставщик",)),
    ("buyer", ("покупатель",)),
    ("seller", ("продавец",)),
    ("customer", ("заказчик",)),
    ("contractor", ("исполнитель", "подрядчик")),
    ("lessor", ("арендодатель",)),
    ("lessee", ("арендатор",)),
    ("guarantor", ("гарант", "поручитель")),
)


def _normalize_token(value: str) -> str:
    return (
        value.casefold()
        .replace("ё", "е")
        .strip(_STRIP_CHARACTERS)
        .replace("\\", "/")
    )


def _normalize_text(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        value.casefold().replace("ё", "е"),
    ).strip()


def _clean_value(words: tuple[OCRWord, ...]) -> str:
    value = " ".join(word.text for word in words)
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" \t\r\n,;:.–—-")


def _union_word_boxes(words: tuple[OCRWord, ...]) -> BoundingBox:
    if not words:
        raise ValueError("нельзя объединить координаты пустого списка слов")

    result = words[0].bbox

    for word in words[1:]:
        result = result.union(word.bbox)

    return result


def _average_confidence(words: tuple[OCRWord, ...]) -> float:
    if not words:
        return 0.0

    return mean(word.confidence for word in words)


def _ordered_region_lines(
    spatial: SpatialSearch,
    line: LayoutLine,
) -> tuple[LayoutLine, ...]:
    return tuple(
        sorted(
            (
                candidate
                for candidate in spatial.lines
                if candidate.page == line.page
                and candidate.region == line.region
            ),
            key=lambda candidate: (
                candidate.bbox.y1,
                candidate.bbox.x1,
            ),
        )
    )


def _neighbor_lines(
    spatial: SpatialSearch,
    line: LayoutLine,
    before: int,
    after: int,
) -> tuple[LayoutLine, ...]:
    lines = _ordered_region_lines(spatial, line)

    for position, candidate in enumerate(lines):
        if candidate.id != line.id:
            continue

        start = max(0, position - before)
        end = min(len(lines), position + after + 1)
        return lines[start:end]

    return (line,)


def _role_from_text(text: str) -> str:
    normalized = _normalize_text(text)

    best_role = "unknown"
    best_position: int | None = None

    for role, patterns in _PARTY_ROLE_PATTERNS:
        for pattern in patterns:
            position = normalized.find(pattern)

            if position < 0:
                continue

            if best_position is None or position < best_position:
                best_position = position
                best_role = role

    return best_role


def _infer_role_for_span(
    spatial: SpatialSearch,
    line: LayoutLine,
    span_end: int,
) -> str:
    after_text = " ".join(
        word.search_text
        for word in line.words[span_end + 1 :]
    )

    role = _role_from_text(after_text)
    if role != "unknown":
        return role

    region_lines = _ordered_region_lines(spatial, line)

    current_position: int | None = None

    for position, candidate in enumerate(region_lines):
        if candidate.id == line.id:
            current_position = position
            break

    if current_position is not None:
        for offset in (1, 2):
            next_position = current_position + offset

            if next_position >= len(region_lines):
                break

            role = _role_from_text(
                region_lines[next_position].normalized_text
            )

            if role != "unknown":
                return role

    before_text = " ".join(
        word.search_text
        for word in line.words[:span_end]
    )

    role = _role_from_text(before_text)
    if role != "unknown":
        return role

    if current_position is not None:
        for offset in (1, 2):
            previous_position = current_position - offset

            if previous_position < 0:
                break

            role = _role_from_text(
                region_lines[previous_position].normalized_text
            )

            if role != "unknown":
                return role

    return "unknown"


def _build_candidate(
    *,
    entity_type: str,
    rule_id: str,
    words: tuple[OCRWord, ...],
    line: LayoutLine,
    value: str,
    confidence: float,
    validation: dict[str, object],
    evidence: tuple[EntityEvidence, ...],
    metadata: dict[str, object],
    anchor_word_ids: tuple[str, ...] = (),
) -> EntityCandidate:
    first_word = words[0]
    last_word = words[-1]

    candidate_id = (
        f"candidate-{entity_type}-"
        f"p{line.page}-"
        f"w{first_word.index}-"
        f"{last_word.index}"
    )

    return EntityCandidate(
        id=candidate_id,
        entity_type=entity_type,
        value=value,
        raw_value=" ".join(word.text for word in words),
        page=line.page,
        bbox=_union_word_boxes(words),
        word_ids=tuple(word.id for word in words),
        confidence=max(0.0, min(1.0, confidence)),
        rule_id=rule_id,
        region=line.region,
        line_id=line.id,
        anchor_word_ids=anchor_word_ids,
        validation=validation,
        evidence=evidence,
        metadata=metadata,
    )


class OrganizationRule(ExtractionRule):

    rule_id = "legal.organization.legal_form.v2"
    entity_type = "organization"

    _legal_forms: tuple[tuple[tuple[str, ...], str], ...] = (
        (("общество", "с", "ограниченной", "ответственностью"), "ООО"),
        (("товарищество", "с", "ограниченной", "ответственностью"), "ТОО"),
        (("акционерное", "общество"), "АО"),
        (("индивидуальный", "предприниматель"), "ИП"),
        (("частная", "компания"), "ЧК"),
        (("тоо",), "ТОО"),
        (("ооо",), "ООО"),
        (("ао",), "АО"),
        (("ип",), "ИП"),
        (("llp",), "LLP"),
        (("llc",), "LLC"),
        (("ltd",), "LTD"),
        (("jsc",), "JSC"),
        (("inc",), "INC"),
    )

    _stop_tokens = frozenset(
        {
            "бин",
            "иин",
            "инн",
            "бик",
            "bik",
            "swift",
            "bic",
            "iban",
            "иик",
            "р/с",
            "к/с",
            "счет",
            "счёт",
            "account",
            "адрес",
            "телефон",
            "тел",
            "email",
            "e-mail",
            "именуемый",
            "именуемая",
            "именуемое",
            "именуемые",
            "далее",
            "в",
        }
    )

    _bank_tokens = frozenset({"банк", "bank", "banking"})

    def __init__(
        self,
        config: OrganizationRuleConfig | None = None,
    ) -> None:
        self.config = config or OrganizationRuleConfig()

    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        del document

        candidates: list[EntityCandidate] = []
        seen: set[tuple[int, str, tuple[str, ...]]] = set()

        for line in spatial.lines:
            for match in self._find_legal_forms(line):
                organization_words = self._collect_organization_words(
                    line=line,
                    match=match,
                )

                if not organization_words:
                    continue

                value = _clean_value(organization_words)

                if not self._is_valid_name(
                    value=value,
                    match=match,
                    words=organization_words,
                ):
                    continue

                if self._is_bank_name(value):
                    continue

                key = (
                    line.page,
                    _normalize_text(value),
                    tuple(word.id for word in organization_words),
                )

                if key in seen:
                    continue

                seen.add(key)

                role_hint = _infer_role_for_span(
                    spatial=spatial,
                    line=line,
                    span_end=match.end_position,
                )

                candidate = self._create_candidate(
                    line=line,
                    spatial=spatial,
                    match=match,
                    words=organization_words,
                    value=value,
                    role_hint=role_hint,
                )

                if candidate.confidence >= self.config.min_candidate_confidence:
                    candidates.append(candidate)

        return tuple(candidates)

    def _find_legal_forms(
        self,
        line: LayoutLine,
    ) -> tuple[_PhraseMatch, ...]:
        tokens = tuple(_normalize_token(word.search_text) for word in line.words)
        aliases = sorted(self._legal_forms, key=lambda item: len(item[0]), reverse=True)
        matches: list[_PhraseMatch] = []
        position = 0

        while position < len(tokens):
            found: _PhraseMatch | None = None

            for alias, canonical in aliases:
                end = position + len(alias)

                if tokens[position:end] != alias:
                    continue

                found = _PhraseMatch(
                    words=tuple(line.words[position:end]),
                    start_position=position,
                    end_position=end - 1,
                    canonical_value=canonical,
                )
                break

            if found is None:
                position += 1
                continue

            matches.append(found)
            position = found.end_position + 1

        return tuple(matches)

    def _collect_organization_words(
        self,
        line: LayoutLine,
        match: _PhraseMatch,
    ) -> tuple[OCRWord, ...]:
        result = list(match.words)
        position = match.end_position + 1

        while (
            position < len(line.words)
            and len(result) < self.config.max_organization_words
        ):
            word = line.words[position]
            token = _normalize_token(word.search_text)

            if token in self._stop_tokens:
                break

            if not token:
                position += 1
                continue

            result.append(word)

            if word.text.rstrip().endswith((",", ";")):
                break

            position += 1

        if len(result) <= len(match.words):
            return ()

        return tuple(result)

    @staticmethod
    def _is_valid_name(
        value: str,
        match: _PhraseMatch,
        words: tuple[OCRWord, ...],
    ) -> bool:
        del value

        name_words = words[len(match.words) :]
        return any(
            any(character.isalpha() for character in word.text)
            for word in name_words
        )

    def _is_bank_name(self, value: str) -> bool:
        tokens = set(re.findall(r"[a-zа-я]+", _normalize_text(value)))
        return bool(tokens.intersection(self._bank_tokens))

    def _create_candidate(
        self,
        line: LayoutLine,
        spatial: SpatialSearch,
        match: _PhraseMatch,
        words: tuple[OCRWord, ...],
        value: str,
        role_hint: str,
    ) -> EntityCandidate:
        context_text = spatial.context_text(
            words[0],
            before=2,
            after=2,
            same_region=True,
            normalized=True,
        )
        average_ocr_confidence = _average_confidence(words)
        confidence = 0.20
        evidence: list[EntityEvidence] = []

        evidence.append(
            EntityEvidence(
                kind="legal_form",
                description=(
                    "найдена организационно-правовая форма юридического лица"
                ),
                score_delta=0.40,
                data={
                    "legal_form": match.canonical_value,
                    "legal_form_word_ids": list(match.word_ids),
                },
            )
        )
        confidence += 0.40

        evidence.append(
            EntityEvidence(
                kind="organization_name",
                description="после правовой формы найдено название",
                score_delta=0.20,
                data={"value": value},
            )
        )
        confidence += 0.20

        if role_hint != "unknown":
            evidence.append(
                EntityEvidence(
                    kind="party_context",
                    description=(
                        "по ближайшему контексту определена роль стороны"
                    ),
                    score_delta=0.10,
                    data={"role_hint": role_hint},
                )
            )
            confidence += 0.10

        ocr_score = average_ocr_confidence * 0.10
        evidence.append(
            EntityEvidence(
                kind="ocr_confidence",
                description="учтена средняя уверенность OCR",
                score_delta=ocr_score,
                data={
                    "average_ocr_confidence": round(
                        average_ocr_confidence,
                        6,
                    )
                },
            )
        )
        confidence += ocr_score

        return _build_candidate(
            entity_type=self.entity_type,
            rule_id=self.rule_id,
            words=words,
            line=line,
            value=value,
            confidence=confidence,
            validation={
                "format": "legal_entity_name",
                "status": "context_match",
                "legal_form": match.canonical_value,
                "has_name": True,
            },
            evidence=tuple(evidence),
            metadata={
                "legal_form": match.canonical_value,
                "role_hint": role_hint,
                "context_text": context_text,
            },
            anchor_word_ids=match.word_ids,
        )


class BankNameRule(ExtractionRule):
    rule_id = "legal.bank_name.context.v2"
    entity_type = "bank_name"

    _anchor_aliases: tuple[tuple[tuple[str, ...], str], ...] = (
        (("банк", "корреспондент"), "correspondent_bank"),
        (("банк-корреспондент",), "correspondent_bank"),
        (("корреспондентский", "банк"), "correspondent_bank"),
        (("correspondent", "bank"), "correspondent_bank"),
        (("банк",), "bank"),
        (("bank",), "bank"),
    )

    _legal_forms = frozenset(
        {
            "ао",
            "ооо",
            "тоо",
            "jsc",
            "llc",
            "llp",
            "ltd",
            "inc",
        }
    )

    _bank_tokens = frozenset({"банк", "bank", "banking"})

    _detail_anchor_tokens = frozenset(
        {
            "бик",
            "bik",
            "swift",
            "bic",
            "бик/swift",
            "swift/bic",
            "iban",
            "иик",
            "р/с",
            "р/счет",
            "р/счёт",
            "account",
            "счет",
            "счёт",
            "бин",
            "иин",
            "инн",
            "адрес",
            "телефон",
            "email",
        }
    )

    _continuation_endings = frozenset(
        {
            "of",
            "the",
            "and",
            "de",
            "la",
            "имени",
        }
    )

    def __init__(
        self,
        config: BankNameRuleConfig | None = None,
    ) -> None:
        self.config = config or BankNameRuleConfig()

    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        del document

        candidates: list[EntityCandidate] = []
        seen_word_sets: set[tuple[str, ...]] = set()

        for line in spatial.lines:
            extraction = self._extract_bank_words(
                line=line,
                spatial=spatial,
            )

            if extraction is None:
                continue

            words, anchor_word_ids, role_hint, line_ids, explicit_anchor = extraction
            word_ids = tuple(word.id for word in words)

            if word_ids in seen_word_sets:
                continue

            seen_word_sets.add(word_ids)
            value = _clean_value(words)

            if not self._is_valid_bank_name(
                value=value,
                explicit_anchor=explicit_anchor,
                region=line.region,
            ):
                continue

            candidate = self._create_candidate(
                line=line,
                spatial=spatial,
                words=words,
                value=value,
                anchor_word_ids=anchor_word_ids,
                role_hint=role_hint,
                line_ids=line_ids,
                explicit_anchor=explicit_anchor,
            )

            if candidate.confidence >= self.config.min_candidate_confidence:
                candidates.append(candidate)

        return tuple(candidates)

    def _extract_bank_words(
        self,
        line: LayoutLine,
        spatial: SpatialSearch,
    ) -> tuple[
        tuple[OCRWord, ...],
        tuple[str, ...],
        str,
        tuple[str, ...],
        bool,
    ] | None:
        tokens = tuple(_normalize_token(word.search_text) for word in line.words)
        anchor = self._find_explicit_anchor(line, tokens)

        if anchor is not None:
            start_position, end_position, role_hint = anchor
            name_start = end_position + 1

            if name_start >= len(line.words):
                return None

            first_words = self._collect_line_name_words(
                line=line,
                start_position=name_start,
            )

            if not first_words:
                return None

            words = list(first_words)
            line_ids = [line.id]

            if self._should_continue_name(
                role_hint=role_hint,
                words=tuple(words),
            ):
                self._append_continuation_lines(
                    spatial=spatial,
                    line=line,
                    result=words,
                    line_ids=line_ids,
                )

            return (
                tuple(words),
                tuple(
                    word.id
                    for word in line.words[start_position : end_position + 1]
                ),
                role_hint,
                tuple(line_ids),
                True,
            )

        legal_form_position = self._find_legal_form_position(tokens)

        if legal_form_position is None:
            return None

        if not any(
            token in self._bank_tokens
            for token in tokens[legal_form_position:]
        ):
            return None

        if line.region == "body" and len(line.words) > 8:
            return None

        words = self._collect_line_name_words(
            line=line,
            start_position=legal_form_position,
        )

        if not words:
            return None

        return words, (), "bank", (line.id,), False

    def _find_explicit_anchor(
        self,
        line: LayoutLine,
        tokens: tuple[str, ...],
    ) -> tuple[int, int, str] | None:
        aliases = sorted(
            self._anchor_aliases,
            key=lambda item: len(item[0]),
            reverse=True,
        )

        for position in range(len(tokens)):
            for alias, role_hint in aliases:
                end = position + len(alias)

                if tokens[position:end] != alias:
                    continue

                if len(alias) == 1:
                    raw = line.words[position].text.strip()

                    if position != 0 and not raw.endswith(":"):
                        continue

                return position, end - 1, role_hint

        return None

    def _find_legal_form_position(
        self,
        tokens: tuple[str, ...],
    ) -> int | None:
        for position, token in enumerate(tokens):
            if token in self._legal_forms:
                return position

        return None

    def _collect_line_name_words(
        self,
        line: LayoutLine,
        start_position: int,
    ) -> tuple[OCRWord, ...]:
        result: list[OCRWord] = []

        for word in line.words[start_position:]:
            token = _normalize_token(word.search_text)

            if token in self._detail_anchor_tokens:
                break

            if not token:
                continue

            result.append(word)

            if len(result) >= self.config.max_bank_words:
                break

        return tuple(result)

    def _should_continue_name(
        self,
        role_hint: str,
        words: tuple[OCRWord, ...],
    ) -> bool:
        if role_hint == "correspondent_bank":
            return True

        value = _clean_value(words)
        normalized_tokens = re.findall(r"[a-zа-я]+", _normalize_text(value))

        if normalized_tokens and normalized_tokens[-1] in self._continuation_endings:
            return True

        return value.count("«") > value.count("»") or value.count('"') % 2 == 1

    def _append_continuation_lines(
        self,
        spatial: SpatialSearch,
        line: LayoutLine,
        result: list[OCRWord],
        line_ids: list[str],
    ) -> None:
        region_lines = _ordered_region_lines(spatial, line)
        current_position: int | None = None

        for position, candidate in enumerate(region_lines):
            if candidate.id == line.id:
                current_position = position
                break

        if current_position is None:
            return

        used_lines = 1

        for next_line in region_lines[current_position + 1 :]:
            if used_lines >= self.config.max_bank_lines:
                break

            first_token = (
                _normalize_token(next_line.words[0].search_text)
                if next_line.words
                else ""
            )

            if first_token in self._detail_anchor_tokens:
                break

            next_anchor = self._find_explicit_anchor(
                next_line,
                tuple(
                    _normalize_token(word.search_text)
                    for word in next_line.words
                ),
            )

            if next_anchor is not None:
                break

            continuation_words = self._collect_line_name_words(
                line=next_line,
                start_position=0,
            )

            if not continuation_words:
                break

            result.extend(continuation_words)
            line_ids.append(next_line.id)
            used_lines += 1

            if len(result) >= self.config.max_bank_words:
                break

            if not self._should_continue_name(
                role_hint="bank",
                words=tuple(result),
            ):
                break

    def _is_valid_bank_name(
        self,
        value: str,
        explicit_anchor: bool,
        region: str | None,
    ) -> bool:
        normalized = _normalize_text(value)
        tokens = re.findall(r"[a-zа-я]+", normalized)

        if not tokens:
            return False

        if len(tokens) > self.config.max_bank_words:
            return False

        has_legal_form = bool(set(tokens).intersection(self._legal_forms))
        has_bank_token = bool(set(tokens).intersection(self._bank_tokens))

        if explicit_anchor:
            return len(tokens) >= 2

        if region == "body" and not has_legal_form:
            return False

        return has_legal_form and has_bank_token

    def _create_candidate(
        self,
        line: LayoutLine,
        spatial: SpatialSearch,
        words: tuple[OCRWord, ...],
        value: str,
        anchor_word_ids: tuple[str, ...],
        role_hint: str,
        line_ids: tuple[str, ...],
        explicit_anchor: bool,
    ) -> EntityCandidate:
        average_ocr_confidence = _average_confidence(words)
        confidence = 0.15
        evidence: list[EntityEvidence] = []

        if explicit_anchor:
            evidence.append(
                EntityEvidence(
                    kind="bank_anchor",
                    description="найден явный банковский ключ",
                    score_delta=0.40,
                    data={
                        "anchor_word_ids": list(anchor_word_ids),
                        "bank_role": role_hint,
                    },
                )
            )
            confidence += 0.40
        else:
            evidence.append(
                EntityEvidence(
                    kind="bank_legal_form",
                    description=(
                        "название содержит правовую форму и банковский термин"
                    ),
                    score_delta=0.35,
                    data={"bank_role": role_hint},
                )
            )
            confidence += 0.35

        evidence.append(
            EntityEvidence(
                kind="bank_name_shape",
                description="фрагмент соответствует форме названия банка",
                score_delta=0.25,
                data={
                    "value": value,
                    "line_count": len(line_ids),
                },
            )
        )
        confidence += 0.25

        if line.region in {"left_column", "right_column"}:
            evidence.append(
                EntityEvidence(
                    kind="requisites_region",
                    description="название находится в блоке реквизитов",
                    score_delta=0.10,
                    data={"region": line.region},
                )
            )
            confidence += 0.10

        ocr_score = average_ocr_confidence * 0.10
        evidence.append(
            EntityEvidence(
                kind="ocr_confidence",
                description="учтена средняя уверенность OCR",
                score_delta=ocr_score,
                data={
                    "average_ocr_confidence": round(
                        average_ocr_confidence,
                        6,
                    )
                },
            )
        )
        confidence += ocr_score

        context_text = spatial.context_text(
            words[0],
            before=2,
            after=2,
            same_region=True,
            normalized=True,
        )

        return _build_candidate(
            entity_type=self.entity_type,
            rule_id=self.rule_id,
            words=words,
            line=line,
            value=value,
            confidence=confidence,
            validation={
                "format": "bank_name",
                "status": "context_match",
                "explicit_anchor": explicit_anchor,
                "line_count": len(line_ids),
            },
            evidence=tuple(evidence),
            metadata={
                "context_text": context_text,
                "role_hint": "bank",
                "bank_role": role_hint,
                "line_ids": list(line_ids),
            },
            anchor_word_ids=anchor_word_ids,
        )


class PositionRule(ExtractionRule):

    rule_id = "legal.position.dictionary.v2"
    entity_type = "position"

    _position_aliases: tuple[tuple[tuple[str, ...], str], ...] = (
        (("генеральный", "директор"), "генеральный директор"),
        (("генерального", "директора"), "генеральный директор"),
        (("исполнительный", "директор"), "исполнительный директор"),
        (("исполнительного", "директора"), "исполнительный директор"),
        (("председатель", "правления"), "председатель правления"),
        (("председателя", "правления"), "председатель правления"),
        (("директор",), "директор"),
        (("директора",), "директор"),
        (("руководитель",), "руководитель"),
        (("руководителя",), "руководитель"),
        (("представитель",), "представитель"),
        (("представителя",), "представитель"),
        (("президент",), "президент"),
        (("управляющий",), "управляющий"),
        (("бухгалтер",), "бухгалтер"),
    )

    def __init__(
        self,
        config: PositionRuleConfig | None = None,
    ) -> None:
        self.config = config or PositionRuleConfig()

    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        del document

        candidates: list[EntityCandidate] = []
        seen: set[tuple[int, str, tuple[str, ...]]] = set()

        for line in spatial.lines:
            for match in self._find_matches(line):
                key = (line.page, match.canonical_value, match.word_ids)

                if key in seen:
                    continue

                seen.add(key)
                party_role_hint = _infer_role_for_span(
                    spatial=spatial,
                    line=line,
                    span_end=match.end_position,
                )

                candidate = self._create_candidate(
                    line=line,
                    spatial=spatial,
                    match=match,
                    party_role_hint=party_role_hint,
                )

                if candidate.confidence >= self.config.min_candidate_confidence:
                    candidates.append(candidate)

        return tuple(candidates)

    def _find_matches(self, line: LayoutLine) -> tuple[_PhraseMatch, ...]:
        tokens = tuple(_normalize_token(word.search_text) for word in line.words)
        aliases = sorted(
            self._position_aliases,
            key=lambda item: len(item[0]),
            reverse=True,
        )
        matches: list[_PhraseMatch] = []
        position = 0

        while position < len(tokens):
            found: _PhraseMatch | None = None

            for alias, canonical in aliases:
                end = position + len(alias)

                if tokens[position:end] != alias:
                    continue

                found = _PhraseMatch(
                    words=tuple(line.words[position:end]),
                    start_position=position,
                    end_position=end - 1,
                    canonical_value=canonical,
                )
                break

            if found is None:
                position += 1
                continue

            matches.append(found)
            position = found.end_position + 1

        return tuple(matches)

    def _create_candidate(
        self,
        line: LayoutLine,
        spatial: SpatialSearch,
        match: _PhraseMatch,
        party_role_hint: str,
    ) -> EntityCandidate:
        average_ocr_confidence = _average_confidence(match.words)
        ocr_score = average_ocr_confidence * 0.10
        evidence = (
            EntityEvidence(
                kind="position_dictionary",
                description="найдена известная должность",
                score_delta=0.50,
                data={"canonical_position": match.canonical_value},
            ),
            EntityEvidence(
                kind="ocr_confidence",
                description="учтена средняя уверенность OCR",
                score_delta=ocr_score,
                data={
                    "average_ocr_confidence": round(
                        average_ocr_confidence,
                        6,
                    )
                },
            ),
        )

        return _build_candidate(
            entity_type=self.entity_type,
            rule_id=self.rule_id,
            words=match.words,
            line=line,
            value=match.canonical_value,
            confidence=0.25 + 0.50 + ocr_score,
            validation={
                "format": "position_title",
                "status": "dictionary_match",
                "canonical_value": match.canonical_value,
            },
            evidence=evidence,
            metadata={
                "context_text": spatial.context_text(
                    match.words[0],
                    before=2,
                    after=2,
                    same_region=True,
                    normalized=True,
                ),
                "role_hint": "representative_position",
                "party_role_hint": party_role_hint,
            },
        )


class PersonNameRule(ExtractionRule):
    rule_id = "legal.person_name.context.v2"
    entity_type = "person_name"

    _context_tokens = frozenset(
        {
            "лице",
            "директор",
            "директора",
            "руководитель",
            "руководителя",
            "представитель",
            "представителя",
            "председатель",
            "председателя",
            "президент",
            "управляющий",
            "подписант",
            "фио",
        }
    )

    _skip_tokens = frozenset(
        {
            "в",
            "лице",
            "генеральный",
            "генерального",
            "исполнительный",
            "исполнительного",
            "директор",
            "директора",
            "руководитель",
            "руководителя",
            "представитель",
            "представителя",
            "председатель",
            "председателя",
            "правления",
            "президент",
            "управляющий",
            "господин",
            "господина",
            "г-н",
        }
    )

    _stop_tokens = frozenset(
        {
            "действующий",
            "действующего",
            "действующая",
            "действующей",
            "действующие",
            "действующих",
            "действует",
            "на",
            "основании",
            "устава",
            "доверенности",
            "именуемый",
            "именуемая",
            "именуемое",
            "далее",
            "бин",
            "инн",
            "иин",
            "подпись",
            "м.п",
        }
    )

    _name_blacklist = frozenset(
        {
            "республика",
            "казахстан",
            "кыргызстан",
            "россия",
            "сторона",
            "стороны",
            "договор",
            "общество",
            "товарищество",
            "компания",
            "банк",
            "заемщик",
            "заёмщик",
            "займодавец",
            "поставщик",
            "покупатель",
            "заказчик",
            "исполнитель",
            "тест",
        }
    )

    _word_name_pattern = re.compile(r"^[A-Za-zА-Яа-яЁёҚқҒғҢңҮүҰұІіҺһӘәӨө-]{2,}$")
    _initials_pattern = re.compile(
        r"^(?:[A-Za-zА-Яа-яЁёҚқҒғҢңҮүҰұІіҺһӘәӨө]\.?){1,3}$"
    )
    _surname_initials_pattern = re.compile(
        r"^[A-Za-zА-Яа-яЁёҚқҒғҢңҮүҰұІіҺһӘәӨө-]{2,}"
        r"(?:[A-Za-zА-Яа-яЁёҚқҒғҢңҮүҰұІіҺһӘәӨө]\.?){1,3}$"
    )

    def __init__(
        self,
        config: PersonNameRuleConfig | None = None,
    ) -> None:
        self.config = config or PersonNameRuleConfig()

    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        del document

        results: list[EntityCandidate] = []
        seen_word_sets: set[tuple[str, ...]] = set()

        grouped_lines: dict[tuple[int, str | None], list[LayoutLine]] = defaultdict(list)

        for line in spatial.lines:
            grouped_lines[(line.page, line.region)].append(line)

        for lines in grouped_lines.values():
            ordered_lines = sorted(
                lines,
                key=lambda candidate: (
                    candidate.bbox.y1,
                    candidate.bbox.x1,
                ),
            )

            for line_position, line in enumerate(ordered_lines):
                tokens = tuple(
                    _normalize_token(word.search_text)
                    for word in line.words
                )

                for anchor_position, token in enumerate(tokens):
                    if token not in self._context_tokens:
                        continue

                    extraction = self._extract_after_anchor(
                        lines=ordered_lines,
                        line_position=line_position,
                        anchor_position=anchor_position,
                    )

                    if extraction is None:
                        continue

                    words, source_line = extraction
                    word_ids = tuple(word.id for word in words)

                    if word_ids in seen_word_sets:
                        continue

                    seen_word_sets.add(word_ids)
                    value = _clean_value(words)

                    if not self._is_valid_person_words(words):
                        continue

                    party_role_hint = self._infer_person_party_role(
                        spatial=spatial,
                        anchor_line=line,
                        source_line=source_line,
                        anchor_position=anchor_position,
                    )

                    candidate = self._create_candidate(
                        line=source_line,
                        spatial=spatial,
                        words=words,
                        value=value,
                        anchor_word_id=line.words[anchor_position].id,
                        party_role_hint=party_role_hint,
                    )

                    if candidate.confidence >= self.config.min_candidate_confidence:
                        results.append(candidate)

        return tuple(results)

    def _extract_after_anchor(
        self,
        lines: list[LayoutLine],
        line_position: int,
        anchor_position: int,
    ) -> tuple[tuple[OCRWord, ...], LayoutLine] | None:
        line = lines[line_position]
        start_position = anchor_position + 1

        while (
            start_position < len(line.words)
            and _normalize_token(line.words[start_position].search_text)
            in self._skip_tokens
        ):
            start_position += 1

        same_line_words = self._collect_person_words(
            line=line,
            start_position=start_position,
        )

        if self._is_valid_person_words(same_line_words):
            return same_line_words, line

        for offset in range(1, self.config.max_person_line_lookahead + 1):
            next_position = line_position + offset

            if next_position >= len(lines):
                break

            next_line = lines[next_position]
            next_words = self._collect_person_words(
                line=next_line,
                start_position=0,
            )

            if self._is_valid_person_words(next_words):
                return next_words, next_line

            first_token = (
                _normalize_token(next_line.words[0].search_text)
                if next_line.words
                else ""
            )

            if first_token in self._stop_tokens:
                break

        return None

    def _collect_person_words(
        self,
        line: LayoutLine,
        start_position: int,
    ) -> tuple[OCRWord, ...]:
        result: list[OCRWord] = []
        position = start_position

        while (
            position < len(line.words)
            and len(result) < self.config.max_person_words
        ):
            word = line.words[position]
            normalized = _normalize_token(word.search_text)
            cleaned = word.text.strip(_STRIP_CHARACTERS)

            if normalized in self._skip_tokens and not result:
                position += 1
                continue

            if normalized in self._stop_tokens:
                break

            if self._is_name_component(cleaned) or self._is_initials(cleaned) or self._is_surname_with_initials(cleaned):
                result.append(word)
                position += 1
                continue

            if result:
                break

            position += 1

        return tuple(result)

    def _is_valid_person_words(
        self,
        words: tuple[OCRWord, ...],
    ) -> bool:
        if not words:
            return False

        if len(words) == 1:
            return self._is_surname_with_initials(
                words[0].text.strip(_STRIP_CHARACTERS)
            )

        name_components = 0
        initials = 0

        for word in words:
            cleaned = word.text.strip(_STRIP_CHARACTERS)

            if self._is_initials(cleaned):
                initials += 1
            elif self._is_name_component(cleaned):
                name_components += 1

        return name_components >= 2 or (
            name_components >= 1 and initials >= 1
        )

    def _is_name_component(self, value: str) -> bool:
        normalized = _normalize_token(value)

        if normalized in self._name_blacklist:
            return False

        return bool(self._word_name_pattern.fullmatch(value))

    def _is_initials(self, value: str) -> bool:
        compact = re.sub(r"\s+", "", value)
        return bool(self._initials_pattern.fullmatch(compact))

    def _is_surname_with_initials(self, value: str) -> bool:
        compact = re.sub(r"[\s.]", "", value)
        return bool(self._surname_initials_pattern.fullmatch(compact))

    def _infer_person_party_role(
        self,
        spatial: SpatialSearch,
        anchor_line: LayoutLine,
        source_line: LayoutLine,
        anchor_position: int,
    ) -> str:
        role = _infer_role_for_span(
            spatial=spatial,
            line=anchor_line,
            span_end=anchor_position,
        )

        if role != "unknown":
            return role

        context_lines = _neighbor_lines(
            spatial=spatial,
            line=source_line,
            before=3,
            after=2,
        )

        for candidate_line in reversed(context_lines):
            role = _role_from_text(candidate_line.normalized_text)

            if role != "unknown":
                return role

        return "unknown"

    def _create_candidate(
        self,
        line: LayoutLine,
        spatial: SpatialSearch,
        words: tuple[OCRWord, ...],
        value: str,
        anchor_word_id: str,
        party_role_hint: str,
    ) -> EntityCandidate:
        average_ocr_confidence = _average_confidence(words)
        confidence = 0.20
        evidence: list[EntityEvidence] = []

        evidence.append(
            EntityEvidence(
                kind="person_context",
                description=(
                    "ФИО найдено после юридического контекста "
                    "в той же или следующей строке"
                ),
                score_delta=0.40,
                data={"anchor_word_id": anchor_word_id},
            )
        )
        confidence += 0.40

        evidence.append(
            EntityEvidence(
                kind="person_name_shape",
                description=(
                    "последовательность соответствует ФИО "
                    "или фамилии с инициалами"
                ),
                score_delta=0.25,
                data={"word_count": len(words)},
            )
        )
        confidence += 0.25

        if party_role_hint != "unknown":
            evidence.append(
                EntityEvidence(
                    kind="party_context",
                    description="определена сторона представителя",
                    score_delta=0.05,
                    data={"party_role_hint": party_role_hint},
                )
            )
            confidence += 0.05

        ocr_score = average_ocr_confidence * 0.10
        evidence.append(
            EntityEvidence(
                kind="ocr_confidence",
                description="учтена средняя уверенность OCR",
                score_delta=ocr_score,
                data={
                    "average_ocr_confidence": round(
                        average_ocr_confidence,
                        6,
                    )
                },
            )
        )
        confidence += ocr_score

        return _build_candidate(
            entity_type=self.entity_type,
            rule_id=self.rule_id,
            words=words,
            line=line,
            value=value,
            confidence=confidence,
            validation={
                "format": "person_name",
                "status": "context_match",
                "word_count": len(words),
            },
            evidence=tuple(evidence),
            metadata={
                "context_text": spatial.context_text(
                    words[0],
                    before=2,
                    after=2,
                    same_region=True,
                    normalized=True,
                ),
                "role_hint": "representative",
                "party_role_hint": party_role_hint,
            },
            anchor_word_ids=(anchor_word_id,),
        )


__all__ = [
    "LegalPartyRuleConfig",
    "OrganizationRuleConfig",
    "BankNameRuleConfig",
    "PersonNameRuleConfig",
    "PositionRuleConfig",
    "OrganizationRule",
    "BankNameRule",
    "PersonNameRule",
    "PositionRule",
]