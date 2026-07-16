from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from contract_extractor.models.geometry import BoundingBox

@dataclass(frozen=True, slots=True)
class EntityEvidence:
    kind: str
    description: str
    score_delta: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError(
                "тип доказательства не может быть пустым"
            )

        if not self.description:
            raise ValueError(
                "описание доказательства не может быть пустым"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "score_delta": round(
                self.score_delta,
                6,
            ),
            "data": dict(self.data),
        }


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    id: str
    entity_type: str

    value: str
    raw_value: str

    page: int
    bbox: BoundingBox
    word_ids: tuple[str, ...]

    confidence: float
    rule_id: str

    region: str | None = None
    line_id: str | None = None

    anchor_word_ids: tuple[str, ...] = ()

    validation: dict[str, Any] = field(
        default_factory=dict
    )

    evidence: tuple[EntityEvidence, ...] = ()

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError(
                "ID кандидата не может быть пустым"
            )

        if not self.entity_type:
            raise ValueError(
                "тип сущности не может быть пустым"
            )

        if not self.value:
            raise ValueError(
                f"значение кандидата {self.id} "
                "не может быть пустым"
            )

        if not self.raw_value:
            raise ValueError(
                f"исходное значение кандидата {self.id} "
                "не может быть пустым"
            )

        if self.page < 1:
            raise ValueError(
                f"номер страницы должен начинаться с 1: "
                f"{self.page}"
            )

        if not self.word_ids:
            raise ValueError(
                f"кандидат {self.id} должен быть связан "
                "хотя бы с одним OCR-словом"
            )

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "уверенность кандидата должна находиться "
                f"в диапазоне от 0.0 до 1.0: "
                f"{self.confidence}"
            )

        if not self.rule_id:
            raise ValueError(
                f"у кандидата {self.id} не указано правило"
            )

    @property
    def all_word_ids(self) -> tuple[str, ...]:
        result: list[str] = []

        for word_id in (
            *self.anchor_word_ids,
            *self.word_ids,
        ):
            if word_id not in result:
                result.append(word_id)

        return tuple(result)

    def to_dict(self) -> dict[str, Any]:

        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "value": self.value,
            "raw_value": self.raw_value,
            "page": self.page,
            "bbox": self.bbox.to_list(),
            "word_ids": list(self.word_ids),
            "confidence": round(
                self.confidence,
                6,
            ),
            "rule_id": self.rule_id,
            "region": self.region,
            "line_id": self.line_id,
            "anchor_word_ids": list(
                self.anchor_word_ids
            ),
            "validation": dict(
                self.validation
            ),
            "evidence": [
                item.to_dict()
                for item in self.evidence
            ],
            "metadata": dict(
                self.metadata
            ),
        }