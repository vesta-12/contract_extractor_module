from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean
from contract_extractor.layout import SpatialSearch
from contract_extractor.models import (
    ContractParty,
    EntityCandidate,
    OCRDocument,
    PartyBankDetails,
    PartyLinkingResult,
    PartyRepresentative,
)

@dataclass(frozen=True, slots=True)
class PartyLinkerConfig:

    minimum_assignment_score: float = 0.45
    ambiguity_margin: float = 0.06
    same_region_bonus: float = 0.55
    same_page_bonus: float = 0.15
    same_line_bonus: float = 0.10
    max_vertical_bonus: float = 0.30
    party_role_bonus: float = 0.35

    def __post_init__(self) -> None:
        if not 0.0 <= self.minimum_assignment_score <= 1.0:
            raise ValueError(
                "minimum_assignment_score должен быть "
                "в диапазоне от 0.0 до 1.0"
            )

        if self.ambiguity_margin < 0:
            raise ValueError("ambiguity_margin не может быть отрицательным")


@dataclass(frozen=True, slots=True)
class _OrganizationGroup:
    normalized_name: str
    candidates: tuple[EntityCandidate, ...]
    primary: EntityCandidate
    role: str


class PartyLinker:

    _party_detail_types = frozenset(
        {
            "bin",
            "iin",
            "inn",
            "address",
            "bank_name",
            "bik",
            "bic_swift",
            "iban",
            "bank_account",
            "person_name",
            "position",
        }
    )

    _identifier_types = frozenset({"bin", "iin", "inn"})
    _bank_detail_types = frozenset(
        {
            "bank_name",
            "bik",
            "bic_swift",
            "iban",
            "bank_account",
        }
    )

    _party_roles = frozenset(
        {
            "lender",
            "borrower",
            "supplier",
            "buyer",
            "seller",
            "customer",
            "contractor",
            "lessor",
            "lessee",
            "guarantor",
        }
    )

    _role_patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("lender", ("займодав", "кредитор")),
        ("borrower", ("заемщик", "заёмщик", "должник")),
        ("supplier", ("поставщик",)),
        ("buyer", ("покупатель",)),
        ("seller", ("продавец",)),
        ("customer", ("заказчик",)),
        ("contractor", ("исполнитель", "подрядчик")),
        ("lessor", ("арендодатель",)),
        ("lessee", ("арендатор",)),
        ("guarantor", ("поручитель", "гарант")),
    )

    def __init__(
        self,
        config: PartyLinkerConfig | None = None,
    ) -> None:
        self.config = config or PartyLinkerConfig()

    def link(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
        candidates: tuple[EntityCandidate, ...],
    ) -> PartyLinkingResult:
        del document

        organization_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.entity_type == "organization"
        )

        organization_groups = self._build_organization_groups(
            spatial=spatial,
            organizations=organization_candidates,
            all_candidates=candidates,
        )

        document_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.entity_type not in self._party_detail_types
            and candidate.entity_type != "organization"
        )

        assignable_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.entity_type in self._party_detail_types
        )

        if not organization_groups:
            return PartyLinkingResult(
                parties=(),
                document_candidates=document_candidates,
                unassigned_party_candidates=assignable_candidates,
                warnings=(
                    "не найдено ни одной организации, которую можно "
                    "использовать как сторону договора",
                ),
            )

        assignments: dict[str, list[EntityCandidate]] = {
            group.normalized_name: []
            for group in organization_groups
        }

        unassigned: list[EntityCandidate] = []
        warnings: list[str] = []

        for candidate in assignable_candidates:
            scored_groups = [
                (
                    self._assignment_score(
                        candidate=candidate,
                        group=group,
                    ),
                    group,
                )
                for group in organization_groups
            ]
            scored_groups.sort(key=lambda item: item[0], reverse=True)

            best_score, best_group = scored_groups[0]

            if best_score < self.config.minimum_assignment_score:
                unassigned.append(candidate)
                continue

            if len(scored_groups) > 1:
                second_score = scored_groups[1][0]

                if best_score - second_score < self.config.ambiguity_margin:
                    unassigned.append(candidate)
                    warnings.append(
                        f"кандидат {candidate.id!r} одинаково близок "
                        "к нескольким сторонам"
                    )
                    continue

            assignments[best_group.normalized_name].append(candidate)

        parties = tuple(
            sorted(
                (
                    self._build_party(
                        group=group,
                        assigned=tuple(assignments[group.normalized_name]),
                    )
                    for group in organization_groups
                ),
                key=lambda party: (
                    party.organization.page,
                    party.organization.bbox.x1,
                    party.organization.bbox.y1,
                ),
            )
        )

        return PartyLinkingResult(
            parties=parties,
            document_candidates=document_candidates,
            unassigned_party_candidates=tuple(
                sorted(unassigned, key=self._candidate_sort_key)
            ),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _build_organization_groups(
        self,
        spatial: SpatialSearch,
        organizations: tuple[EntityCandidate, ...],
        all_candidates: tuple[EntityCandidate, ...],
    ) -> tuple[_OrganizationGroup, ...]:
        grouped: dict[str, list[EntityCandidate]] = defaultdict(list)

        for candidate in organizations:
            grouped[self._normalize_organization(candidate.value)].append(candidate)

        result: list[_OrganizationGroup] = []

        for normalized_name, occurrences in grouped.items():
            occurrence_tuple = tuple(occurrences)
            primary = self._select_primary(
                occurrences=occurrence_tuple,
                all_candidates=all_candidates,
            )
            role = self._infer_role(
                spatial=spatial,
                occurrences=occurrence_tuple,
            )

            result.append(
                _OrganizationGroup(
                    normalized_name=normalized_name,
                    candidates=occurrence_tuple,
                    primary=primary,
                    role=role,
                )
            )

        result.sort(
            key=lambda group: (
                group.primary.page,
                group.primary.bbox.x1,
                group.primary.bbox.y1,
            )
        )

        return tuple(result)

    def _select_primary(
        self,
        occurrences: tuple[EntityCandidate, ...],
        all_candidates: tuple[EntityCandidate, ...],
    ) -> EntityCandidate:
        def score(candidate: EntityCandidate) -> tuple[float, float, int]:
            value = candidate.confidence

            if candidate.region in {"left_column", "right_column"}:
                value += 0.35

            if candidate.metadata.get("role_hint") not in {None, "", "unknown"}:
                value += 0.20

            nearby_detail_count = sum(
                other.entity_type in self._party_detail_types
                and other.page == candidate.page
                and other.region == candidate.region
                for other in all_candidates
            )

            value += min(0.25, nearby_detail_count * 0.03)

            return value, candidate.confidence, -candidate.page

        return max(occurrences, key=score)

    def _infer_role(
        self,
        spatial: SpatialSearch,
        occurrences: tuple[EntityCandidate, ...],
    ) -> str:
        role_scores: dict[str, float] = defaultdict(float)

        for occurrence in occurrences:
            metadata_role = occurrence.metadata.get("role_hint")

            if isinstance(metadata_role, str) and metadata_role in self._party_roles:
                role_scores[metadata_role] += 3.0

            context_parts: list[str] = []
            metadata_context = occurrence.metadata.get("context_text")

            if isinstance(metadata_context, str):
                context_parts.append(metadata_context)

            if occurrence.word_ids:
                try:
                    first_word = spatial.get_word(occurrence.word_ids[0])
                    context_parts.append(
                        spatial.context_text(
                            first_word,
                            before=4,
                            after=3,
                            same_region=True,
                            normalized=True,
                        )
                    )
                except KeyError:
                    pass

            context = " ".join(context_parts).casefold().replace("ё", "е")

            for role, patterns in self._role_patterns:
                for pattern in patterns:
                    if pattern in context:
                        role_scores[role] += 1.0

        if not role_scores:
            return "unknown"

        return max(role_scores, key=role_scores.get)

    def _assignment_score(
        self,
        candidate: EntityCandidate,
        group: _OrganizationGroup,
    ) -> float:
        best_score = 0.0
        candidate_party_role = self._candidate_party_role(candidate)

        for organization in group.candidates:
            if candidate.page != organization.page:
                continue

            score = self.config.same_page_bonus

            if candidate.region == organization.region:
                score += self.config.same_region_bonus
            elif candidate.region is None or organization.region is None:
                score += 0.05

            if (
                candidate.line_id is not None
                and candidate.line_id == organization.line_id
            ):
                score += self.config.same_line_bonus

            vertical_distance = abs(
                candidate.bbox.center_y - organization.bbox.center_y
            )
            score += max(
                0.0,
                self.config.max_vertical_bonus - vertical_distance,
            )

            if (
                candidate_party_role != "unknown"
                and group.role != "unknown"
            ):
                if candidate_party_role == group.role:
                    score += self.config.party_role_bonus
                else:
                    score -= self.config.party_role_bonus

            best_score = max(best_score, score)

        return max(0.0, min(1.0, best_score))

    def _candidate_party_role(self, candidate: EntityCandidate) -> str:
        for key in ("party_role_hint", "role_hint"):
            value = candidate.metadata.get(key)

            if isinstance(value, str) and value in self._party_roles:
                return value

        return "unknown"

    def _build_party(
        self,
        group: _OrganizationGroup,
        assigned: tuple[EntityCandidate, ...],
    ) -> ContractParty:
        identifiers = tuple(
            sorted(
                (
                    candidate
                    for candidate in assigned
                    if candidate.entity_type in self._identifier_types
                ),
                key=self._candidate_sort_key,
            )
        )

        addresses = tuple(
            sorted(
                (
                    candidate
                    for candidate in assigned
                    if candidate.entity_type == "address"
                ),
                key=self._candidate_sort_key,
            )
        )

        people = tuple(
            candidate
            for candidate in assigned
            if candidate.entity_type == "person_name"
        )
        positions = tuple(
            candidate
            for candidate in assigned
            if candidate.entity_type == "position"
        )

        representatives = self._build_representatives(
            people=people,
            positions=positions,
        )

        bank_candidates = tuple(
            candidate
            for candidate in assigned
            if candidate.entity_type in self._bank_detail_types
        )
        bank_details = self._build_bank_details(bank_candidates)

        confidence_values = [group.primary.confidence]
        confidence_values.extend(candidate.confidence for candidate in assigned)
        confidence = mean(confidence_values)

        if group.role != "unknown":
            confidence = min(1.0, confidence + 0.05)

        party_warnings: list[str] = []

        if group.role == "unknown":
            party_warnings.append("не удалось надёжно определить роль стороны")

        if not identifiers:
            party_warnings.append(
                "к стороне не привязан ни один регистрационный идентификатор"
            )

        role_for_id = group.role if group.role != "unknown" else "party"
        party_id = f"party-{role_for_id}-{group.primary.id}"

        return ContractParty(
            id=party_id,
            role=group.role,
            organization=group.primary,
            identifiers=identifiers,
            addresses=addresses,
            representatives=representatives,
            bank_details=bank_details,
            confidence=confidence,
            warnings=tuple(party_warnings),
            metadata={
                "normalized_organization_name": group.normalized_name,
                "organization_occurrence_ids": [
                    candidate.id
                    for candidate in group.candidates
                ],
                "assigned_candidate_ids": [
                    candidate.id
                    for candidate in assigned
                ],
                "primary_region": group.primary.region,
                "primary_page": group.primary.page,
            },
        )

    def _build_representatives(
        self,
        people: tuple[EntityCandidate, ...],
        positions: tuple[EntityCandidate, ...],
    ) -> tuple[PartyRepresentative, ...]:
        if not people and not positions:
            return ()

        person_groups: dict[str, list[EntityCandidate]] = defaultdict(list)

        for person in people:
            person_groups[self._person_identity_key(person.value)].append(person)

        position_groups: dict[str, list[EntityCandidate]] = defaultdict(list)

        for position in positions:
            position_groups[position.value.casefold()].append(position)

        remaining_position_keys = set(position_groups)
        representatives: list[PartyRepresentative] = []

        for person_key, occurrences in person_groups.items():
            primary_name = max(
                occurrences,
                key=lambda candidate: (
                    len(candidate.word_ids),
                    candidate.confidence,
                ),
            )

            nearest_position_key: str | None = None

            if remaining_position_keys:
                nearest_position_key = min(
                    remaining_position_keys,
                    key=lambda key: min(
                        self._candidate_distance(primary_name, candidate)
                        for candidate in position_groups[key]
                    ),
                )

            primary_position: EntityCandidate | None = None
            position_occurrence_ids: tuple[str, ...] = ()

            if nearest_position_key is not None:
                position_occurrences = position_groups[nearest_position_key]
                primary_position = min(
                    position_occurrences,
                    key=lambda candidate: self._candidate_distance(
                        primary_name,
                        candidate,
                    ),
                )
                position_occurrence_ids = tuple(
                    candidate.id
                    for candidate in position_occurrences
                )
                remaining_position_keys.remove(nearest_position_key)

            confidence_values = [primary_name.confidence]

            if primary_position is not None:
                confidence_values.append(primary_position.confidence)

            representatives.append(
                PartyRepresentative(
                    name=primary_name,
                    position=primary_position,
                    name_occurrence_ids=tuple(
                        candidate.id
                        for candidate in occurrences
                    ),
                    position_occurrence_ids=position_occurrence_ids,
                    confidence=mean(confidence_values),
                )
            )

        for position_key in sorted(remaining_position_keys):
            position_occurrences = position_groups[position_key]
            primary_position = max(
                position_occurrences,
                key=lambda candidate: candidate.confidence,
            )

            representatives.append(
                PartyRepresentative(
                    name=None,
                    position=primary_position,
                    position_occurrence_ids=tuple(
                        candidate.id
                        for candidate in position_occurrences
                    ),
                    confidence=primary_position.confidence * 0.75,
                )
            )

        representatives.sort(
            key=lambda representative: (
                representative.name.page
                if representative.name is not None
                else representative.position.page
                if representative.position is not None
                else 10**9,
                representative.name.bbox.y1
                if representative.name is not None
                else representative.position.bbox.y1
                if representative.position is not None
                else 10**9,
            )
        )

        return tuple(representatives)

    def _build_bank_details(
        self,
        candidates: tuple[EntityCandidate, ...],
    ) -> tuple[PartyBankDetails, ...]:
        if not candidates:
            return ()

        grouped: dict[tuple[int, str | None], list[EntityCandidate]] = defaultdict(list)

        for candidate in candidates:
            grouped[(candidate.page, candidate.region)].append(candidate)

        blocks: list[PartyBankDetails] = []

        for group_candidates in grouped.values():
            bank_names = sorted(
                (
                    candidate
                    for candidate in group_candidates
                    if candidate.entity_type == "bank_name"
                ),
                key=self._candidate_sort_key,
            )
            details = sorted(
                (
                    candidate
                    for candidate in group_candidates
                    if candidate.entity_type
                    in {"bik", "bic_swift", "iban", "bank_account"}
                ),
                key=self._candidate_sort_key,
            )

            if not bank_names:
                blocks.append(
                    self._create_bank_block(
                        bank_name=None,
                        details=tuple(details),
                    )
                )
                continue

            assignments: dict[str, list[EntityCandidate]] = {
                bank_name.id: []
                for bank_name in bank_names
            }

            for detail in details:
                selected_bank = bank_names[0]

                for bank_name in bank_names:
                    if bank_name.bbox.y1 <= detail.bbox.center_y:
                        selected_bank = bank_name
                    else:
                        break

                assignments[selected_bank.id].append(detail)

            for bank_name in bank_names:
                blocks.append(
                    self._create_bank_block(
                        bank_name=bank_name,
                        details=tuple(assignments[bank_name.id]),
                    )
                )

        blocks.sort(
            key=lambda block: (
                block.bank_name.page
                if block.bank_name is not None
                else block.accounts[0].page
                if block.accounts
                else block.bik_codes[0].page
                if block.bik_codes
                else block.swift_codes[0].page
                if block.swift_codes
                else 10**9,
                block.bank_name.bbox.y1
                if block.bank_name is not None
                else 0.0,
            )
        )

        return tuple(blocks)

    def _create_bank_block(
        self,
        bank_name: EntityCandidate | None,
        details: tuple[EntityCandidate, ...],
    ) -> PartyBankDetails:
        accounts = tuple(
            sorted(
                (
                    candidate
                    for candidate in details
                    if candidate.entity_type in {"iban", "bank_account"}
                ),
                key=self._candidate_sort_key,
            )
        )
        bik_codes = tuple(
            sorted(
                (
                    candidate
                    for candidate in details
                    if candidate.entity_type == "bik"
                ),
                key=self._candidate_sort_key,
            )
        )
        swift_codes = tuple(
            sorted(
                (
                    candidate
                    for candidate in details
                    if candidate.entity_type == "bic_swift"
                ),
                key=self._candidate_sort_key,
            )
        )

        confidence_values = [candidate.confidence for candidate in details]

        if bank_name is not None:
            confidence_values.append(bank_name.confidence)

        confidence = mean(confidence_values) if confidence_values else 0.0

        return PartyBankDetails(
            bank_name=bank_name,
            accounts=accounts,
            bik_codes=bik_codes,
            swift_codes=swift_codes,
            confidence=confidence,
        )

    @staticmethod
    def _person_identity_key(value: str) -> str:
        normalized = value.casefold().replace("ё", "е")
        tokens = re.findall(r"[a-zа-я]+", normalized)

        if not tokens:
            return normalized

        surname_stem = tokens[0][:5]
        initials = "".join(token[0] for token in tokens[1:3] if token)

        if len(tokens) == 1:
            compact = re.sub(r"[^a-zа-я]", "", normalized)
            surname_match = re.match(r"([a-zа-я]{3,})([a-zа-я]{1,3})$", compact)

            if surname_match is not None:
                surname_stem = surname_match.group(1)[:5]
                initials = surname_match.group(2)[:2]

        return f"{surname_stem}:{initials}"

    @staticmethod
    def _normalize_organization(value: str) -> str:
        normalized = value.casefold().replace("ё", "е")
        normalized = re.sub(r"[«»\"'`.,;:()\[\]{}]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    @staticmethod
    def _candidate_distance(
        first: EntityCandidate,
        second: EntityCandidate,
    ) -> float:
        page_penalty = abs(first.page - second.page) * 2.0
        region_penalty = 0.0 if first.region == second.region else 0.5
        vertical_distance = abs(first.bbox.center_y - second.bbox.center_y)
        horizontal_distance = abs(first.bbox.center_x - second.bbox.center_x)

        return (
            page_penalty
            + region_penalty
            + vertical_distance
            + horizontal_distance * 0.25
        )

    @staticmethod
    def _candidate_sort_key(
        candidate: EntityCandidate,
    ) -> tuple[int, float, float, str]:
        return (
            candidate.page,
            candidate.bbox.y1,
            candidate.bbox.x1,
            candidate.entity_type,
        )