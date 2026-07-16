from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router
from document_processing.config import AppConfig
from document_processing.errors import ProcessingError
from document_processing.jobs import JobManager
from document_processing.logging_config import configure_logging
from document_processing.pipeline import DocumentProcessingService
from document_processing.storage import FileJobStore


logger = logging.getLogger(__name__)


def create_app(
    config: AppConfig | None = None,
    *,
    processor: DocumentProcessingService | None = None,
    job_store: FileJobStore | None = None,
    job_manager: JobManager | None = None,
) -> FastAPI:
    resolved_config = config or AppConfig.from_env()
    configure_logging(resolved_config)
    resolved_store = job_store or FileJobStore(resolved_config)
    resolved_processor = processor or DocumentProcessingService(
        resolved_config
    )
    resolved_manager = job_manager or JobManager(
        resolved_config,
        resolved_store,
        resolved_processor,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        removed = resolved_store.cleanup_expired()
        if removed:
            logger.info("Удалено просроченных заданий: %s", removed)
        yield
        resolved_manager.shutdown(wait=True)

    app = FastAPI(
        title="Document Processing Application",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.config = resolved_config
    app.state.job_store = resolved_store
    app.state.processor = resolved_processor
    app.state.job_manager = resolved_manager
    app.include_router(router)

    @app.exception_handler(ProcessingError)
    async def processing_error_handler(
        _request: Request,
        error: ProcessingError,
    ) -> JSONResponse:
        if error.code in {
            "JOB_NOT_FOUND",
            "DOCUMENT_NOT_FOUND",
            "RESULT_NOT_FOUND",
        }:
            status_code = 404
        elif error.code in {"RESULT_NOT_READY"}:
            status_code = 409
        elif error.code in {"FILE_TOO_LARGE"}:
            status_code = 413
        else:
            status_code = 400
        return JSONResponse(status_code=status_code, content=error.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "REQUEST_VALIDATION_ERROR",
                "message": "Некорректные параметры запроса",
                "details": None,
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        logger.exception("Необработанная ошибка API")
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "Внутренняя ошибка сервера",
                "details": None,
            },
        )

    frontend_dir = Path(resolved_config.frontend_dir)
    if frontend_dir.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=frontend_dir, html=True),
            name="frontend",
        )
    return app
