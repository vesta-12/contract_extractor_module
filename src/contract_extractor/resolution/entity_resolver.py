from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from contract_extractor.models import (
    EntityCandidate,
    ResolutionIssue,
    ResolvedEntities,
)

@dataclass(frozen=True, slots=True)
class EntityResolverConfig:
    same_bbox_iou_threshold: float = 0.65
    overlapping_words_threshold: float = 0.60
    confidence_difference_threshold: float = 0.08
    keep_ambiguous_candidates: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.same_bbox_iou_threshold <= 1.0:
            raise ValueError(
                "same_bbox_iou_threshold должен быть "
                "в диапазоне от 0 до 1"
            )

        if not 0.0 <= self.overlapping_words_threshold <= 1.0:
            raise ValueError(
                "overlapping_words_threshold должен быть "
                "в диапазоне от 0 до 1"
            )

        if self.confidence_difference_threshold < 0:
            raise ValueError(
                "confidence_difference_threshold не может быть отрицательным"
            )


class EntityResolver:

    _exclusive_type_groups: tuple[frozenset[str], ...] = (
        frozenset({"bin", "iin", "inn"}),
        frozenset({"iban", "bank_account"}),
        frozenset({"bik", "bic_swift"}),
        frozenset({"organization", "bank_name"}),
    )

    def __init__(
        self,
        config: EntityResolverConfig | None = None,
    ) -> None:
        self.config = config or EntityResolverConfig()

    def resolve(
        self,
        candidates: tuple[EntityCandidate, ...],
    ) -> ResolvedEntities:
        exact_resolved, exact_rejected = self._remove_exact_duplicates(
            candidates
        )
        overlap_groups = self._build_overlap_groups(exact_resolved)

        accepted: list[EntityCandidate] = []
        rejected: list[EntityCandidate] = list(exact_rejected)
        issues: list[ResolutionIssue] = []
        processed_ids: set[str] = set()

        for group in overlap_groups:
            current_group = tuple(
                candidate
                for candidate in group
                if candidate.id not in processed_ids
            )

            if not current_group:
                continue

            processed_ids.update(candidate.id for candidate in current_group)

            if len(current_group) == 1:
                accepted.append(current_group[0])
                continue

            resolution = self._resolve_overlap_group(current_group)
            accepted.extend(resolution.entities)
            rejected.extend(resolution.rejected)
            issues.extend(resolution.issues)

        for candidate in exact_resolved:
            if candidate.id not in processed_ids:
                accepted.append(candidate)

        accepted = self._unique_by_id(accepted)
        rejected = self._unique_by_id(rejected)
        accepted.sort(key=self._sort_key)
        rejected.sort(key=self._sort_key)

        return ResolvedEntities(
            entities=tuple(accepted),
            rejected=tuple(rejected),
            issues=tuple(issues),
        )

    def _remove_exact_duplicates(
        self,
        candidates: tuple[EntityCandidate, ...],
    ) -> tuple[tuple[EntityCandidate, ...], tuple[EntityCandidate, ...]]:
        grouped: dict[
            tuple[str, str, int, tuple[str, ...]],
            list[EntityCandidate],
        ] = defaultdict(list)

        for candidate in candidates:
            key = (
                candidate.entity_type,
                self._normalize_value(candidate.value),
                candidate.page,
                tuple(sorted(candidate.word_ids)),
            )
            grouped[key].append(candidate)

        accepted: list[EntityCandidate] = []
        rejected: list[EntityCandidate] = []

        for group in grouped.values():
            group.sort(key=self._candidate_quality, reverse=True)
            accepted.append(group[0])
            rejected.extend(group[1:])

        return tuple(accepted), tuple(rejected)

    def _build_overlap_groups(
        self,
        candidates: tuple[EntityCandidate, ...],
    ) -> tuple[tuple[EntityCandidate, ...], ...]:
        candidates_by_page: dict[int, list[EntityCandidate]] = defaultdict(list)

        for candidate in candidates:
            candidates_by_page[candidate.page].append(candidate)

        groups: list[tuple[EntityCandidate, ...]] = []

        for page_candidates in candidates_by_page.values():
            remaining = list(page_candidates)

            while remaining:
                first = remaining.pop(0)
                group = [first]
                changed = True

                while changed:
                    changed = False

                    for candidate in list(remaining):
                        if any(
                            self._candidates_overlap(candidate, existing)
                            for existing in group
                        ):
                            group.append(candidate)
                            remaining.remove(candidate)
                            changed = True

                groups.append(tuple(group))

        return tuple(groups)

    def _resolve_overlap_group(
        self,
        group: tuple[EntityCandidate, ...],
    ) -> ResolvedEntities:
        if self._all_same_type(group):
            best = max(group, key=self._candidate_quality)
            rejected = tuple(
                candidate
                for candidate in group
                if candidate.id != best.id
            )

            return ResolvedEntities(
                entities=(best,),
                rejected=rejected,
            )

        conflicts = self._find_conflicts(group)

        if not conflicts:
            return ResolvedEntities(entities=group)

        sorted_group = sorted(
            group,
            key=self._candidate_quality,
            reverse=True,
        )
        best = sorted_group[0]
        second = sorted_group[1]
        confidence_difference = best.confidence - second.confidence

        if (
            confidence_difference
            >= self.config.confidence_difference_threshold
        ):
            rejected = tuple(
                candidate
                for candidate in group
                if candidate.id != best.id
                and self._types_conflict(
                    best.entity_type,
                    candidate.entity_type,
                )
            )
            accepted = tuple(
                candidate
                for candidate in group
                if candidate not in rejected
            )

            issue = ResolutionIssue(
                code="conflict_resolved_by_confidence",
                message=(
                    "конфликтующие кандидаты занимают одну область. "
                    "выбран кандидат с большей уверенностью."
                ),
                candidate_ids=tuple(candidate.id for candidate in group),
                data={
                    "selected_candidate_id": best.id,
                    "selected_type": best.entity_type,
                },
            )

            return ResolvedEntities(
                entities=accepted,
                rejected=rejected,
                issues=(issue,),
            )

        issue = ResolutionIssue(
            code="ambiguous_entity_type",
            message=(
                "одна область документа соответствует нескольким "
                "типам сущностей с близкой уверенностью."
            ),
            candidate_ids=tuple(candidate.id for candidate in group),
            data={
                "entity_types": sorted(
                    {candidate.entity_type for candidate in group}
                )
            },
        )

        if self.config.keep_ambiguous_candidates:
            return ResolvedEntities(
                entities=group,
                issues=(issue,),
            )

        return ResolvedEntities(
            entities=(best,),
            rejected=tuple(sorted_group[1:]),
            issues=(issue,),
        )

    def _find_conflicts(
        self,
        group: tuple[EntityCandidate, ...],
    ) -> tuple[tuple[EntityCandidate, EntityCandidate], ...]:
        conflicts: list[tuple[EntityCandidate, EntityCandidate]] = []

        for first_index, first in enumerate(group):
            for second in group[first_index + 1 :]:
                if self._types_conflict(
                    first.entity_type,
                    second.entity_type,
                ):
                    conflicts.append((first, second))

        return tuple(conflicts)

    def _types_conflict(
        self,
        first_type: str,
        second_type: str,
    ) -> bool:
        if first_type == second_type:
            return False

        return any(
            first_type in group and second_type in group
            for group in self._exclusive_type_groups
        )

    def _candidates_overlap(
        self,
        first: EntityCandidate,
        second: EntityCandidate,
    ) -> bool:
        if first.page != second.page:
            return False

        first_word_ids = set(first.word_ids)
        second_word_ids = set(second.word_ids)

        if first_word_ids and second_word_ids:
            intersection_count = len(
                first_word_ids.intersection(second_word_ids)
            )
            minimum_word_count = min(
                len(first_word_ids),
                len(second_word_ids),
            )

            if minimum_word_count > 0:
                overlap_ratio = intersection_count / minimum_word_count

                if (
                    overlap_ratio
                    >= self.config.overlapping_words_threshold
                ):
                    return True

        return (
            first.bbox.iou(second.bbox)
            >= self.config.same_bbox_iou_threshold
        )

    @staticmethod
    def _all_same_type(
        candidates: tuple[EntityCandidate, ...],
    ) -> bool:
        return len({candidate.entity_type for candidate in candidates}) == 1

    @staticmethod
    def _candidate_quality(
        candidate: EntityCandidate,
    ) -> tuple[float, int, int, int]:
        confirmation_bonus = int(
            bool(candidate.metadata.get("ocr_value_confirmation"))
        )
        validation_status = candidate.validation.get("status")
        validation_bonus = int(
            validation_status
            in {
                "valid",
                "shape_valid",
                "dictionary_match",
                "context_match",
            }
        )

        return (
            candidate.confidence,
            confirmation_bonus,
            validation_bonus,
            len(candidate.word_ids),
        )

    @staticmethod
    def _normalize_value(value: str) -> str:
        return "".join(value.casefold().split())

    @staticmethod
    def _unique_by_id(
        candidates: list[EntityCandidate],
    ) -> list[EntityCandidate]:
        result: list[EntityCandidate] = []
        seen: set[str] = set()

        for candidate in candidates:
            if candidate.id in seen:
                continue

            seen.add(candidate.id)
            result.append(candidate)

        return result

    @staticmethod
    def _sort_key(
        candidate: EntityCandidate,
    ) -> tuple[int, float, float, str]:
        return (
            candidate.page,
            candidate.bbox.y1,
            candidate.bbox.x1,
            candidate.entity_type,
        )