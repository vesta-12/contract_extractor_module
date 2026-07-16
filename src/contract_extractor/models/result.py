from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from contract_extractor.models.entities import EntityCandidate
from contract_extractor.models.parties import ContractParty

@dataclass(frozen=True, slots=True)
class ResolutionIssue:

    code: str
    message: str
    candidate_ids: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "candidate_ids": list(self.candidate_ids),
            "data": dict(self.data),
        }


@dataclass(frozen=True, slots=True)
class ResolvedEntities:

    entities: tuple[EntityCandidate, ...]
    rejected: tuple[EntityCandidate, ...] = ()
    issues: tuple[ResolutionIssue, ...] = ()

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def by_type(
        self,
        entity_type: str,
    ) -> tuple[EntityCandidate, ...]:
        normalized_type = entity_type.casefold()

        return tuple(
            candidate
            for candidate in self.entities
            if candidate.entity_type.casefold() == normalized_type
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "entity_count": self.entity_count,
                "rejected_count": self.rejected_count,
                "issue_count": len(self.issues),
            },
            "entities": [candidate.to_dict() for candidate in self.entities],
            "rejected": [candidate.to_dict() for candidate in self.rejected],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class ContractExtractionResult:

    source: dict[str, Any]
    document: dict[str, Any]
    parties: tuple[ContractParty, ...]
    entities: tuple[EntityCandidate, ...]
    unassigned_entities: tuple[EntityCandidate, ...] = ()
    rejected_candidates: tuple[EntityCandidate, ...] = ()
    warnings: tuple[str, ...] = ()
    issues: tuple[ResolutionIssue, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.entities:
            return "failed"

        if self.warnings or self.issues:
            return "success_with_warnings"

        return "success"

    @property
    def successful(self) -> bool:
        return self.status != "failed"

    @property
    def party_count(self) -> int:
        return len(self.parties)

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    def entities_by_type(
        self,
        entity_type: str,
    ) -> tuple[EntityCandidate, ...]:
        normalized_type = entity_type.casefold()

        return tuple(
            candidate
            for candidate in self.entities
            if candidate.entity_type.casefold() == normalized_type
        )

    def to_dict(self) -> dict[str, Any]:

        return {
            "summary": {
                "status": self.status,
                "successful": self.successful,
                "party_count": self.party_count,
                "entity_count": self.entity_count,
                "unassigned_entity_count": len(self.unassigned_entities),
                "rejected_candidate_count": len(self.rejected_candidates),
                "warning_count": len(self.warnings),
                "issue_count": len(self.issues),
            },
            "source": dict(self.source),
            "document": dict(self.document),
            "parties": [party.to_dict() for party in self.parties],
            "entities": [entity.to_dict() for entity in self.entities],
            "unassigned_entities": [
                entity.to_dict()
                for entity in self.unassigned_entities
            ],
            "rejected_candidates": [
                candidate.to_dict()
                for candidate in self.rejected_candidates
            ],
            "warnings": list(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
            "metadata": dict(self.metadata),
        }

    def to_production_dict(self) -> dict[str, Any]:

        registry = {
            entity.id: self._compact_entity(entity)
            for entity in self.entities
        }

        document_refs: dict[str, Any] = {
            "type": self.document.get("type", "contract")
        }

        for key, value in self.document.items():
            if key == "type":
                continue

            document_refs[f"{key}_entity_id"] = (
                value.get("id")
                if isinstance(value, dict)
                else None
            )

        parties = []

        for party in self.parties:
            parties.append(
                {
                    "id": party.id,
                    "role": party.role,
                    "organization_entity_id": party.organization.id,
                    "identifier_entity_ids": [
                        candidate.id
                        for candidate in party.identifiers
                    ],
                    "address_entity_ids": [
                        candidate.id
                        for candidate in party.addresses
                    ],
                    "representatives": [
                        {
                            "name_entity_id": (
                                representative.name.id
                                if representative.name is not None
                                else None
                            ),
                            "position_entity_id": (
                                representative.position.id
                                if representative.position is not None
                                else None
                            ),
                            "name_occurrence_ids": list(
                                representative.name_occurrence_ids
                            ),
                            "position_occurrence_ids": list(
                                representative.position_occurrence_ids
                            ),
                            "confidence": round(
                                representative.confidence,
                                6,
                            ),
                        }
                        for representative in party.representatives
                    ],
                    "bank_details": [
                        {
                            "bank_name_entity_id": (
                                bank_block.bank_name.id
                                if bank_block.bank_name is not None
                                else None
                            ),
                            "account_entity_ids": [
                                candidate.id
                                for candidate in bank_block.accounts
                            ],
                            "bik_entity_ids": [
                                candidate.id
                                for candidate in bank_block.bik_codes
                            ],
                            "swift_entity_ids": [
                                candidate.id
                                for candidate in bank_block.swift_codes
                            ],
                            "confidence": round(
                                bank_block.confidence,
                                6,
                            ),
                        }
                        for bank_block in party.bank_details
                    ],
                    "confidence": round(party.confidence, 6),
                    "warnings": list(party.warnings),
                }
            )

        return {
            "summary": {
                "status": self.status,
                "successful": self.successful,
                "party_count": self.party_count,
                "entity_count": self.entity_count,
                "unassigned_entity_count": len(self.unassigned_entities),
                "warning_count": len(self.warnings),
            },
            "source": dict(self.source),
            "document": document_refs,
            "parties": parties,
            "entity_registry": registry,
            "unassigned_entity_ids": [
                entity.id
                for entity in self.unassigned_entities
            ],
            "warnings": list(self.warnings),
            "metadata": {
                "processing_seconds": self.metadata.get(
                    "processing_seconds",
                    {},
                )
            },
        }

    @staticmethod
    def _compact_entity(entity: EntityCandidate) -> dict[str, Any]:
        metadata: dict[str, Any] = {}

        for key in (
            "role_hint",
            "party_role_hint",
            "currency",
            "period",
            "display_name",
            "corrections",
        ):
            value = entity.metadata.get(key)
            if value not in (None, "", [], ()):
                metadata[key] = value

        return {
            "id": entity.id,
            "type": entity.entity_type,
            "value": entity.value,
            "raw_value": entity.raw_value,
            "page": entity.page,
            "bbox": entity.bbox.to_list(),
            "word_ids": list(entity.word_ids),
            "anchor_word_ids": list(entity.anchor_word_ids),
            "confidence": round(entity.confidence, 6),
            "validation": dict(entity.validation),
            "metadata": metadata,
        }