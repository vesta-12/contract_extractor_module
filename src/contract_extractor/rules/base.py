from __future__ import annotations
from abc import ABC, abstractmethod
from contract_extractor.layout import SpatialSearch
from contract_extractor.models import (
    EntityCandidate,
    OCRDocument,
)

class ExtractionRule(ABC):

    rule_id: str
    entity_type: str

    @abstractmethod
    def extract(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:

        raise NotImplementedError

    def __call__(
        self,
        document: OCRDocument,
        spatial: SpatialSearch,
    ) -> tuple[EntityCandidate, ...]:
        return self.extract(
            document=document,
            spatial=spatial,
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"rule_id={self.rule_id!r}, "
            f"entity_type={self.entity_type!r})"
        )