"""Payloads shared by several resources, and the error body.

:class:`Problem` is the RFC 9457 "problem details" object every error response
carries, so a client has one shape to parse whatever failed. :class:`Page` is
the pagination envelope for collections: the reference collection endpoints are
small enough to return a bare list, and the endpoints that grow without bound
return a :class:`Page` instead.
"""

from pydantic import BaseModel


class Page[ItemT](BaseModel):
    """One page of a collection, with the counters a client needs to page on.

    ``total`` is the number of rows the filter matched, not the number of rows
    in ``items``, so a client can render a result count and request the next
    page without a second call.
    """

    items: list[ItemT]
    total: int
    limit: int
    offset: int


class FieldError(BaseModel):
    """One reason a request body or a query string was rejected.

    ``loc`` is the path of the offending value inside the request, ``msg`` is
    the human-readable reason and ``type`` is the stable machine-readable kind.
    The rejected input itself is never echoed: it is untrusted data and may be
    large.
    """

    loc: tuple[str | int, ...]
    msg: str
    type: str


class Problem(BaseModel):
    """An RFC 9457 problem details body.

    ``type`` is ``"about:blank"`` because the API does not publish problem type
    URIs: the HTTP status and the ``title`` carry the meaning. ``errors`` is set
    only by a validation failure; ``detail`` is ``None`` for an internal error,
    whose cause stays in the server log.
    """

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    errors: list[FieldError] | None = None
