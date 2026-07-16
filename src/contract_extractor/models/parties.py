from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from contract_extractor.models.entities import EntityCandidate

def _append_unique(target: list[str], values: tuple[str, ...] | list[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


@dataclass(frozen=True, slots=True)
class PartyRepresentative:
    name: EntityCandidate | None = None
    position: EntityCandidate | None = None
    name_occurrence_ids: tuple[str, ...] = ()
    position_occurrence_ids: tuple[str, ...] = ()
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "уверенность представителя должна находиться "
                "в диапазоне от 0.0 до 1.0"
            )

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        result: list[str] = []

        if self.name is not None:
            _append_unique(result, [self.name.id])

        if self.position is not None:
            _append_unique(result, [self.position.id])

        _append_unique(result, list(self.name_occurrence_ids))
        _append_unique(result, list(self.position_occurrence_ids))

        return tuple(result)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.to_dict() if self.name is not None else None,
            "position": (
                self.position.to_dict()
                if self.position is not None
                else None
            ),
            "confidence": round(self.confidence, 6),
            "candidate_ids": list(self.candidate_ids),
            "name_occurrence_ids": list(self.name_occurrence_ids),
            "position_occurrence_ids": list(self.position_occurrence_ids),
        }


@dataclass(frozen=True, slots=True)
class PartyBankDetails:
    bank_name: EntityCandidate | None = None
    accounts: tuple[EntityCandidate, ...] = ()
    bik_codes: tuple[EntityCandidate, ...] = ()
    swift_codes: tuple[EntityCandidate, ...] = ()
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "уверенность банковского блока должна находиться "
                "в диапазоне от 0.0 до 1.0"
            )

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        result: list[str] = []

        if self.bank_name is not None:
            _append_unique(result, [self.bank_name.id])

        for candidate in (
            *self.accounts,
            *self.bik_codes,
            *self.swift_codes,
        ):
            _append_unique(result, [candidate.id])

        return tuple(result)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bank_name": (
                self.bank_name.to_dict()
                if self.bank_name is not None
                else None
            ),
            "accounts": [candidate.to_dict() for candidate in self.accounts],
            "bik_codes": [candidate.to_dict() for candidate in self.bik_codes],
            "swift_codes": [
                candidate.to_dict()
                for candidate in self.swift_codes
            ],
            "confidence": round(self.confidence, 6),
            "candidate_ids": list(self.candidate_ids),
        }


@dataclass(frozen=True, slots=True)
class ContractParty:
    id: str
    role: str
    organization: EntityCandidate
    identifiers: tuple[EntityCandidate, ...] = ()
    addresses: tuple[EntityCandidate, ...] = ()
    representatives: tuple[PartyRepresentative, ...] = ()
    bank_details: tuple[PartyBankDetails, ...] = ()
    confidence: float = 0.0
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ID стороны не может быть пустым")

        if not self.role:
            raise ValueError("роль стороны не может быть пустой")

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "уверенность стороны должна находиться "
                "в диапазоне от 0.0 до 1.0"
            )

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        result: list[str] = [self.organization.id]

        organization_occurrence_ids = self.metadata.get(
            "organization_occurrence_ids",
            [],
        )
        if isinstance(organization_occurrence_ids, list):
            _append_unique(result, organization_occurrence_ids)

        assigned_candidate_ids = self.metadata.get(
            "assigned_candidate_ids",
            [],
        )
        if isinstance(assigned_candidate_ids, list):
            _append_unique(result, assigned_candidate_ids)

        for candidate in (*self.identifiers, *self.addresses):
            _append_unique(result, [candidate.id])

        for representative in self.representatives:
            _append_unique(result, list(representative.candidate_ids))

        for bank_block in self.bank_details:
            _append_unique(result, list(bank_block.candidate_ids))

        return tuple(result)

    def identifiers_by_type(
        self,
        entity_type: str,
    ) -> tuple[EntityCandidate, ...]:
        normalized_type = entity_type.casefold()

        return tuple(
            candidate
            for candidate in self.identifiers
            if candidate.entity_type.casefold() == normalized_type
        )

    def to_dict(self) -> dict[str, Any]:
        identifiers: dict[str, list[dict[str, Any]]] = {}

        for candidate in self.identifiers:
            identifiers.setdefault(candidate.entity_type, []).append(
                candidate.to_dict()
            )

        return {
            "id": self.id,
            "role": self.role,
            "organization": self.organization.to_dict(),
            "identifiers": identifiers,
            "addresses": [candidate.to_dict() for candidate in self.addresses],
            "representatives": [
                representative.to_dict()
                for representative in self.representatives
            ],
            "bank_details": [
                bank_block.to_dict()
                for bank_block in self.bank_details
            ],
            "confidence": round(self.confidence, 6),
            "warnings": list(self.warnings),
            "candidate_ids": list(self.candidate_ids),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class PartyLinkingResult:
    parties: tuple[ContractParty, ...]
    document_candidates: tuple[EntityCandidate, ...] = ()
    unassigned_party_candidates: tuple[EntityCandidate, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def party_count(self) -> int:
        return len(self.parties)

    @property
    def unassigned_count(self) -> int:
        return len(self.unassigned_party_candidates)

    @property
    def successful(self) -> bool:
        return bool(self.parties)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "party_count": self.party_count,
                "document_candidate_count": len(self.document_candidates),
                "unassigned_party_candidate_count": self.unassigned_count,
                "warning_count": len(self.warnings),
                "successful": self.successful,
            },
            "parties": [party.to_dict() for party in self.parties],
            "document_candidates": [
                candidate.to_dict()
                for candidate in self.document_candidates
            ],
            "unassigned_party_candidates": [
                candidate.to_dict()
                for candidate in self.unassigned_party_candidates
            ],
            "warnings": list(self.warnings),
        }