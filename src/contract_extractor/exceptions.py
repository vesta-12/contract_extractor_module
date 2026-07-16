class ContractExtractorError(Exception):
    pass


class OCRLoadError(ContractExtractorError):
    pass


class OCRSchemaError(ContractExtractorError):

    def __init__(
        self,
        errors: list[str] | tuple[str, ...],
    ) -> None:
        self.errors = tuple(errors)

        message = (
            "OCR JSON не прошёл проверку:\n"
            + "\n".join(
                f"{index}. {error}"
                for index, error in enumerate(
                    self.errors,
                    start=1,
                )
            )
        )

        super().__init__(message)


class RuleExecutionError(ContractExtractorError):
    def __init__(
        self,
        rule_id: str,
        message: str,
    ) -> None:
        self.rule_id = rule_id

        super().__init__(
            f"ошибка выполнения правила "
            f"{rule_id!r}: {message}"
        )