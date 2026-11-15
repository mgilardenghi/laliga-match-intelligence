"""RFC 9457 problem details for every error response of the API.

A client parses one body shape whatever failed. Every error is a
:class:`~lmi_api.schemas.common.Problem` sent with the
``application/problem+json`` media type, and every route that can fail declares
the same schema, so the generated client models the real body instead of an
error envelope it does not produce.

:data:`PROBLEM_RESPONSES` declares a 422 entry as well, which replaces FastAPI's
default ``HTTPValidationError`` schema for that status. A real validation
failure never reaches the default handler, because
:func:`install_error_handlers` registers a handler for it; without the entry the
generated OpenAPI document would describe a shape the API does not return.

Nothing that an attacker controls, and nothing internal, is repeated in a
response: a rejected input is described by its location, reason and type but
never by the input itself, and an unhandled error is logged server-side and
answered with a body that carries no detail.
"""

import http
import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.types import ExceptionHandler

from lmi_api.schemas.common import FieldError, Problem

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"
"""The media type RFC 9457 assigns to a problem details body."""

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": Problem, "content": {PROBLEM_MEDIA_TYPE: {}}},
    422: {"model": Problem, "content": {PROBLEM_MEDIA_TYPE: {}}},
}
"""The error responses a route declares through its ``responses=`` argument."""


def install_error_handlers(application: FastAPI) -> None:
    """Register the problem details handlers on ``application``.

    The application factory calls it before mounting the routers, so the build
    reads in the order it happens: error handling, then routes.
    """
    application.add_exception_handler(
        StarletteHTTPException, cast(ExceptionHandler, _http_exception_handler)
    )
    application.add_exception_handler(
        RequestValidationError, cast(ExceptionHandler, _validation_exception_handler)
    )
    application.add_exception_handler(
        Exception, cast(ExceptionHandler, _unhandled_exception_handler)
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Describe an intentionally raised HTTP error, for example a 404.

    The title is the status phrase and the detail is the message the endpoint
    chose. Both are written by this application, never by the client, so
    repeating the detail is safe.
    """
    problem = Problem(
        title=_status_title(exc.status_code),
        status=exc.status_code,
        detail=str(exc.detail),
    )
    return _problem_response(problem)


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Describe a rejected request body, path parameter or query parameter.

    Only the location, the message and the error type of each finding are kept.
    The rejected input is dropped: it is untrusted, it can be large, and it may
    carry a value the caller did not intend to send.
    """
    errors = [
        FieldError(loc=error["loc"], msg=error["msg"], type=error["type"]) for error in exc.errors()
    ]
    problem = Problem(
        title="Unprocessable Content",
        status=422,
        detail="Request validation failed",
        errors=errors,
    )
    return _problem_response(problem)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer an unexpected failure with a body that leaks nothing.

    The exception is logged with its traceback, which is where the diagnostic
    lives. The response names only the status: a message, a SQL fragment or a
    traceback in the body could disclose the schema or the connection URL.
    """
    logger.exception("unhandled error handling %s %s", request.method, request.url.path)
    problem = Problem(title="Internal Server Error", status=500, detail=None)
    return _problem_response(problem)


def _status_title(status_code: int) -> str:
    """Return the HTTP status phrase of ``status_code``.

    An endpoint may raise any integer, and :class:`http.HTTPStatus` rejects the
    ones it does not know with :exc:`ValueError`. A handler must not fail while
    reporting a failure, so an unknown code falls back to a generic title.
    """
    try:
        return http.HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def _problem_response(problem: Problem) -> JSONResponse:
    """Render ``problem`` with the status and media type it describes."""
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(),
        media_type=PROBLEM_MEDIA_TYPE,
    )
