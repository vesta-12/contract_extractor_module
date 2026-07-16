from __future__ import annotations


class ProcessingError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, object | None]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
