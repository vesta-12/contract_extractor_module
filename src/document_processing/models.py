from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProcessingStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessingIssue:
    code: str
    message: str
    details: object | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(slots=True)
class DocumentProcessingResult:
    status: ProcessingStatus
    page_count: int | None = None
    entity_count: int = 0
    entities: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[ProcessingIssue] = field(default_factory=list)
    output_files: dict[str, str] = field(default_factory=dict)
    timing: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "page_count": self.page_count,
            "entity_count": self.entity_count,
            "entities": list(self.entities),
            "warnings": list(self.warnings),
            "errors": [error.to_dict() for error in self.errors],
            "output_files": dict(self.output_files),
            "timing": dict(self.timing),
        }
