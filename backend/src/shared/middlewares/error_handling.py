from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.shared.exceptions import ApplicationError

logger = logging.getLogger(__name__)


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


class ExceptionHandlingMiddleware(BaseHTTPMiddleware):
    """Maps application and unexpected exceptions to safe JSON responses."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except ApplicationError as error:
            return error_response(error.status_code, error.code, error.message)
        except Exception:
            logger.exception(
                "unhandled_request_error", extra={"path": request.url.path}
            )
            return error_response(
                500, "internal_error", "An unexpected error occurred."
            )


def register_http_exception_handlers(app: FastAPI) -> None:
    """Translate framework HTTP errors without exposing validation internals."""

    @app.exception_handler(HTTPException)
    async def handle_http_error(_: Request, error: HTTPException) -> JSONResponse:
        message = (
            error.detail
            if isinstance(error.detail, str)
            else "The request could not be processed."
        )
        return error_response(error.status_code, "http_error", message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, __: RequestValidationError
    ) -> JSONResponse:
        return error_response(422, "validation_error", "The request data is invalid.")
