from __future__ import annotations

from dotenv import load_dotenv

from api import create_app
from document_processing.config import AppConfig


load_dotenv()
config = AppConfig.from_env()
app = create_app(config)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.casefold(),
    )


if __name__ == "__main__":
    main()
