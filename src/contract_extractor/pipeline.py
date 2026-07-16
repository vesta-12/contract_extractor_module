from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from contract_extractor.input import load_ocr_document
from contract_extractor.layout import LayoutLineBuilder, SpatialSearch
from contract_extractor.linking import PartyLinker, PartyLinkerConfig
from contract_extractor.models import (
    ContractExtractionResult,
    EntityCandidate,
    OCRDocument,
)
from contract_extractor.resolution import (
    EntityResolver,
    EntityResolverConfig,
)
from contract_extractor.rules import (
    AddressRule,
    BICSWIFTRule,
    BIKRule,
    BINRule,
    BankAccountRule,
    BankNameRule,
    DateRule,
    IINRule,
    INNRule,
    KZIBANRule,
    MoneyAmountRule,
    OrganizationRule,
    PercentageRule,
    PersonNameRule,
    PositionRule,
    RuleEngine,
    RuleEngineConfig,
)


@dataclass(frozen=True, slots=True)
class ContractExtractorConfig:

    continue_on_rule_error: bool = True
    strict_candidate_validation: bool = True
    include_rejected_candidates: bool = True
    include_unassigned_entities: bool = True


class ContractExtractor:

    def __init__(
        self,
        config: ContractExtractorConfig | None = None,
        resolver_config: EntityResolverConfig | None = None,
        linker_config: PartyLinkerConfig | None = None,
    ) -> None:
        self.config = config or ContractExtractorConfig()

        self.engine = RuleEngine(
            rules=self._build_default_rules(),
            config=RuleEngineConfig(
                continue_on_error=self.config.continue_on_rule_error,
                strict_candidate_validation=(
                    self.config.strict_candidate_validation
                ),
            ),
        )

        self.resolver = EntityResolver(
            config=(
                resolver_config
                or EntityResolverConfig(
                    keep_ambiguous_candidates=False
                )
            )
        )
        self.linker = PartyLinker(config=linker_config)

    def extract(
        self,
        source: str | Path | OCRDocument,
    ) -> ContractExtractionResult:
        processing_start = perf_counter()

        if isinstance(source, OCRDocument):
            document = source
            source_path: str | None = None
        else:
            source_path = str(Path(source))
            document = load_ocr_document(source)

        loading_finished = perf_counter()

        lines = LayoutLineBuilder().build_document(document)
        spatial = SpatialSearch(
            document=document,
            lines=lines,
        )

        layout_finished = perf_counter()

        extraction_result = self.engine.run(
            document=document,
            spatial=spatial,
        )

        rules_finished = perf_counter()

        resolution_result = self.resolver.resolve(
            extraction_result.candidates
        )

        resolution_finished = perf_counter()

        linking_result = self.linker.link(
            document=document,
            spatial=spatial,
            candidates=resolution_result.entities,
        )

        linking_finished = perf_counter()

        document_summary = self._build_document_summary(
            candidates=resolution_result.entities
        )

        used_candidate_ids = {
            candidate_id
            for party in linking_result.parties
            for candidate_id in party.candidate_ids
        }
        used_candidate_ids.update(
            candidate.id
            for candidate in linking_result.document_candidates
        )

        unassigned_entities = tuple(
            candidate
            for candidate in resolution_result.entities
            if candidate.id not in used_candidate_ids
        )

        warnings: list[str] = []
        warnings.extend(linking_result.warnings)
        warnings.extend(
            warning
            for party in linking_result.parties
            for warning in party.warnings
        )
        warnings.extend(
            issue.message
            for issue in resolution_result.issues
        )
        warnings.extend(
            issue.message
            for issue in extraction_result.issues
        )
        warnings.extend(
            self._build_validation_warnings(
                resolution_result.entities
            )
        )

        if unassigned_entities:
            warnings.append(
                f"осталось непривязанных сущностей: "
                f"{len(unassigned_entities)}"
            )

        source_data = {
            "path": source_path,
            "page_count": document.page_count,
            "word_count": document.word_count,
            "line_count": len(lines),
        }

        metadata = {
            "rule_count": self.engine.rule_count,
            "executed_rule_ids": list(
                extraction_result.executed_rule_ids
            ),
            "raw_candidate_count": extraction_result.candidate_count,
            "resolved_candidate_count": resolution_result.entity_count,
            "processing_seconds": {
                "loading": round(
                    loading_finished - processing_start,
                    6,
                ),
                "layout": round(
                    layout_finished - loading_finished,
                    6,
                ),
                "rules": round(
                    rules_finished - layout_finished,
                    6,
                ),
                "resolution": round(
                    resolution_finished - rules_finished,
                    6,
                ),
                "linking": round(
                    linking_finished - resolution_finished,
                    6,
                ),
                "total": round(
                    linking_finished - processing_start,
                    6,
                ),
            },
        }

        return ContractExtractionResult(
            source=source_data,
            document=document_summary,
            parties=linking_result.parties,
            entities=resolution_result.entities,
            unassigned_entities=(
                unassigned_entities
                if self.config.include_unassigned_entities
                else ()
            ),
            rejected_candidates=(
                resolution_result.rejected
                if self.config.include_rejected_candidates
                else ()
            ),
            warnings=tuple(dict.fromkeys(warnings)),
            issues=resolution_result.issues,
            metadata=metadata,
        )

    @staticmethod
    def _build_default_rules() -> list:
        return [
            DateRule(),
            MoneyAmountRule(),
            PercentageRule(),
            OrganizationRule(),
            BankNameRule(),
            AddressRule(),
            PositionRule(),
            PersonNameRule(),
            BINRule(),
            IINRule(),
            INNRule(),
            BIKRule(),
            BICSWIFTRule(),
            KZIBANRule(),
            BankAccountRule(),
        ]

    @staticmethod
    def _build_document_summary(
        candidates: tuple[EntityCandidate, ...],
    ) -> dict:
        dates = [
            candidate
            for candidate in candidates
            if candidate.entity_type == "date"
        ]
        amounts = [
            candidate
            for candidate in candidates
            if candidate.entity_type == "money_amount"
        ]
        percentages = [
            candidate
            for candidate in candidates
            if candidate.entity_type == "percentage"
        ]

        contract_date = ContractExtractor._select_by_role_hint(
            dates,
            "contract_date",
        )
        principal_amount = ContractExtractor._select_by_role_hint(
            amounts,
            "principal_amount",
        )
        annual_interest_rate = ContractExtractor._select_by_role_hint(
            percentages,
            "annual_interest_rate",
        )
        repayment_due_date = ContractExtractor._select_by_role_hint(
            dates,
            "repayment_due_date",
        )
        interest_start_date = ContractExtractor._select_by_role_hint(
            dates,
            "interest_start_date",
        )

        return {
            "type": ContractExtractor._infer_document_type(candidates),
            "date": (
                contract_date.to_dict()
                if contract_date is not None
                else None
            ),
            "principal_amount": (
                principal_amount.to_dict()
                if principal_amount is not None
                else None
            ),
            "annual_interest_rate": (
                annual_interest_rate.to_dict()
                if annual_interest_rate is not None
                else None
            ),
            "repayment_due_date": (
                repayment_due_date.to_dict()
                if repayment_due_date is not None
                else None
            ),
            "interest_start_date": (
                interest_start_date.to_dict()
                if interest_start_date is not None
                else None
            ),
        }

    @staticmethod
    def _select_by_role_hint(
        candidates: list[EntityCandidate],
        role_hint: str,
    ) -> EntityCandidate | None:
        matching = [
            candidate
            for candidate in candidates
            if candidate.metadata.get("role_hint") == role_hint
        ]

        if not matching:
            return None

        return max(
            matching,
            key=lambda candidate: candidate.confidence,
        )

    @staticmethod
    def _infer_document_type(
        candidates: tuple[EntityCandidate, ...],
    ) -> str:
        role_hints = {
            candidate.metadata.get("role_hint")
            for candidate in candidates
        }

        if {"lender", "borrower"}.intersection(role_hints):
            return "loan_agreement"

        if {"supplier", "buyer"}.intersection(role_hints):
            return "supply_agreement"

        if {"lessor", "lessee"}.intersection(role_hints):
            return "lease_agreement"

        if {"customer", "contractor"}.intersection(role_hints):
            return "service_agreement"

        return "contract"

    @staticmethod
    def _build_validation_warnings(
        candidates: tuple[EntityCandidate, ...],
    ) -> tuple[str, ...]:
        warnings: list[str] = []

        for candidate in candidates:
            status = candidate.validation.get("status")

            if (
                candidate.entity_type == "iban"
                and status == "checksum_invalid"
            ):
                warnings.append(
                    f"IBAN {candidate.value} имеет корректную форму, "
                    "но не прошёл проверку MOD 97"
                )

        return tuple(warnings)


def extract_contract_data(
    source: str | Path | OCRDocument,
    config: ContractExtractorConfig | None = None,
) -> ContractExtractionResult:

    extractor = ContractExtractor(config=config)
    return extractor.extract(source)