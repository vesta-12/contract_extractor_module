from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable
from contract_extractor.exceptions import (
    RuleExecutionError,
)
from contract_extractor.layout import SpatialSearch
from contract_extractor.models import (
    EntityCandidate,
    OCRDocument,
)
from contract_extractor.rules.base import ExtractionRule

@dataclass(frozen=True, slots=True)
class RuleEngineConfig:

    continue_on_error: bool = False

    strict_candidate_validation: bool = True


@dataclass(frozen=True, slots=True)
class RuleExecutionIssue:
    rule_id: str
    rule_class: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "rule_class": self.rule_class,
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class RuleEngineResult:

    candidates: tuple[EntityCandidate, ...]

    executed_rule_ids: tuple[str, ...]

    issues: tuple[RuleExecutionIssue, ...] = ()

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def successful(self) -> bool:
        return not self.issues

    @property
    def entity_types(self) -> tuple[str, ...]:

        result: list[str] = []

        for candidate in self.candidates:
            if candidate.entity_type not in result:
                result.append(
                    candidate.entity_type
                )

        return tuple(result)

    def candidates_by_type(
        self,
        entity_type: str,
    ) -> tuple[EntityCandidate, ...]:

        normalized_type = entity_type.casefold()

        return tuple(
            candidate
            for candidate in self.candidates
            if (
                candidate.entity_type.casefold()
                == normalized_type
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "candidate_count": (
                    self.candidate_count
                ),
                "issue_count": self.issue_count,
                "successful": self.successful,
                "executed_rule_count": len(
                    self.executed_rule_ids
                ),
                "entity_types": list(
                    self.entity_types
                ),
            },
            "executed_rule_ids": list(
                self.executed_rule_ids
            ),
            "candidates": [
                candidate.to_dict()
                for candidate in self.candidates
            ],
            "issues": [
                issue.to_dict()
                for issue in self.issues
            ],
        }


class RuleEngine:
    def __init__(
        self,
        rules: Iterable[ExtractionRule] | None = None,
        config: RuleEngineConfig | None = None,
    ) -> None:
        self.config = config or RuleEngineConfig()

        self._rules: list[ExtractionRule] = []
        self._rule_ids: set[str] = set()

        if rules is not None:
            for rule in rules:
                self.register(rule)

    @property
    def rules(self) -> tuple[ExtractionRule, ...]:

        return tuple(self._rules)

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def register(
        self,
        rule: ExtractionRule,
    ) -> None:

        if not isinstance(rule, ExtractionRule):
            raise TypeError(
                "RuleEngine принимает только объекты, "
                "наследующие ExtractionRule"
            )

        if not rule.rule_id:
            raise ValueError(
                "у правила отсутствует rule_id"
            )

        if not rule.entity_type:
            raise ValueError(
                f"у правила {rule.rule_id!r} "
                "отсутствует entity_type"
            )

        if rule.rule_id in self._rule_ids:
            raise ValueError(
                f"правило с ID {rule.rule_id!r} "
                "уже зарегистрировано"
            )

        self._rules.append(rule)
        self._rule_ids.add(rule.rule_id)

    def unregister(
        self,
        rule_id: str,
    ) -> ExtractionRule:

        for index, rule in enumerate(
            self._rules
        ):
            if rule.rule_id == rule_id:
                removed_rule = self._rules.pop(
                    index
                )

                self._rule_ids.remove(rule_id)

                return removed_rule

        raise KeyError(
            f"правило с ID {rule_id!r} "
            "не зарегистрировано"
        )

    def get_rule(
        self,
        rule_id: str,
    ) -> ExtractionRule:

        for rule in self._rules:
            if rule.rule_id == rule_id:
                return rule

        raise KeyError(
            f"правило с ID {rule_id!r} "
            "не зарегистрировано"
        )

    def run(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> RuleEngineResult:

        candidates: list[EntityCandidate] = []
        issues: list[RuleExecutionIssue] = []
        executed_rule_ids: list[str] = []

        candidate_ids: set[str] = set()

        for rule in self._rules:
            try:
                rule_candidates = rule.extract(
                    document=document,
                    spatial=spatial,
                )

                validated_candidates = (
                    self._validate_rule_result(
                        rule=rule,
                        candidates=rule_candidates,
                        existing_candidate_ids=(
                            candidate_ids
                        ),
                    )
                )

                candidates.extend(
                    validated_candidates
                )

                candidate_ids.update(
                    candidate.id
                    for candidate
                    in validated_candidates
                )

                executed_rule_ids.append(
                    rule.rule_id
                )

            except Exception as error:
                issue = RuleExecutionIssue(
                    rule_id=rule.rule_id,
                    rule_class=(
                        rule.__class__.__name__
                    ),
                    error_type=(
                        error.__class__.__name__
                    ),
                    message=str(error),
                )

                issues.append(issue)

                if not self.config.continue_on_error:
                    raise RuleExecutionError(
                        rule_id=rule.rule_id,
                        message=str(error),
                    ) from error

        sorted_candidates = tuple(
            sorted(
                candidates,
                key=self._candidate_sort_key,
            )
        )

        return RuleEngineResult(
            candidates=sorted_candidates,
            executed_rule_ids=tuple(
                executed_rule_ids
            ),
            issues=tuple(issues),
        )

    def __call__(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> RuleEngineResult:
        return self.run(
            document=document,
            spatial=spatial,
        )

    def _validate_rule_result(
        self,
        rule: ExtractionRule,
        candidates: Any,
        existing_candidate_ids: set[str],
    ) -> tuple[EntityCandidate, ...]:

        if not isinstance(candidates, tuple):
            raise TypeError(
                f"правило {rule.rule_id!r} должно "
                "возвращать tuple[EntityCandidate, ...], "
                f"получено {type(candidates).__name__}"
            )

        validated: list[EntityCandidate] = []
        current_rule_ids: set[str] = set()

        for position, candidate in enumerate(
            candidates
        ):
            if not isinstance(
                candidate,
                EntityCandidate,
            ):
                raise TypeError(
                    f"правило {rule.rule_id!r} "
                    f"вернуло элемент #{position} типа "
                    f"{type(candidate).__name__}, "
                    "ожидался EntityCandidate"
                )

            if (
                self.config.strict_candidate_validation
            ):
                self._validate_candidate_owner(
                    rule=rule,
                    candidate=candidate,
                )

            if candidate.id in existing_candidate_ids:
                raise ValueError(
                    f"кандидат с ID {candidate.id!r} "
                    "уже был создан другим правилом"
                )

            if candidate.id in current_rule_ids:
                raise ValueError(
                    f"правило {rule.rule_id!r} "
                    "дважды вернуло кандидата с ID "
                    f"{candidate.id!r}"
                )

            current_rule_ids.add(
                candidate.id
            )

            validated.append(candidate)

        return tuple(validated)

    @staticmethod
    def _validate_candidate_owner(
        rule: ExtractionRule,
        candidate: EntityCandidate,
    ) -> None:

        if candidate.rule_id != rule.rule_id:
            raise ValueError(
                f"правило {rule.rule_id!r} вернуло "
                f"кандидата {candidate.id!r} "
                f"с rule_id={candidate.rule_id!r}"
            )

        if (
            candidate.entity_type
            != rule.entity_type
        ):
            raise ValueError(
                f"правило {rule.rule_id!r} извлекает "
                f"тип {rule.entity_type!r}, но вернуло "
                f"кандидата типа "
                f"{candidate.entity_type!r}"
            )

    @staticmethod
    def _candidate_sort_key(
        candidate: EntityCandidate,
    ) -> tuple[
        int,
        float,
        float,
        str,
        str,
    ]:
        return (
            candidate.page,
            candidate.bbox.y1,
            candidate.bbox.x1,
            candidate.entity_type,
            candidate.value,
        )