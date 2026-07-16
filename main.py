import json
from pathlib import Path
from contract_extractor import extract_contract_data

INPUT_PATH = Path(
    "data/input/formatted/test_loan_agreement_ocr.json"
)

DEBUG_OUTPUT_PATH = Path(
    "data/output/test_loan_agreement_result.debug.json"
)

PRODUCTION_OUTPUT_PATH = Path(
    "data/output/test_loan_agreement_result.json"
)


def write_json(
    path: Path,
    data: dict,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    result = extract_contract_data(
        INPUT_PATH
    )

    write_json(
        DEBUG_OUTPUT_PATH,
        result.to_dict(),
    )

    write_json(
        PRODUCTION_OUTPUT_PATH,
        result.to_production_dict(),
    )

    print("обработка завершена")
    print(f"статус: {result.status}")
    print(
        f"тип документа: "
        f"{result.document.get('type')}"
    )
    print(
        f"найдено сторон: "
        f"{result.party_count}"
    )
    print(
        f"найдено сущностей: "
        f"{result.entity_count}"
    )
    print(
        f"непривязанных сущностей: "
        f"{len(result.unassigned_entities)}"
    )
    print(
        f"отклонённых кандидатов: "
        f"{len(result.rejected_candidates)}"
    )
    print(
        f"предупреждений: "
        f"{len(result.warnings)}"
    )

    print(
        f"Debug JSON: "
        f"{DEBUG_OUTPUT_PATH}"
    )
    print(
        f"Production JSON: "
        f"{PRODUCTION_OUTPUT_PATH}"
    )

    print("\nстороны:")

    for party in result.parties:
        print(
            f"\n  {party.role}: "
            f"{party.organization.value}"
        )

        for identifier in party.identifiers:
            print(
                f"    "
                f"{identifier.entity_type.upper()}: "
                f"{identifier.value}"
            )

        for representative in party.representatives:
            name = (
                representative.name.value
                if representative.name is not None
                else "не найдено"
            )
            position = (
                representative.position.value
                if representative.position is not None
                else "не найдено"
            )

            print(
                f"    Представитель: "
                f"{position} — {name}"
            )

        for bank_block in party.bank_details:
            bank_name = (
                bank_block.bank_name.value
                if bank_block.bank_name is not None
                else "банк не определён"
            )

            print(
                f"    Банк: {bank_name}"
            )

            for account in bank_block.accounts:
                print(
                    f"      "
                    f"{account.entity_type.upper()}: "
                    f"{account.value}"
                )

            for bik in bank_block.bik_codes:
                print(
                    f"      БИК: {bik.value}"
                )

            for swift in bank_block.swift_codes:
                print(
                    f"      SWIFT/BIC: "
                    f"{swift.value}"
                )

    if result.warnings:
        print("\nпредупреждения:")

        for warning in result.warnings:
            print(f"  - {warning}")


if __name__ == "__main__":
    main()